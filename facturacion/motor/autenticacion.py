"""Usuarios, claves y sesiones. Solo biblioteca estandar.

Decisiones y por que:

- **scrypt** para las claves (`hashlib.scrypt`), con sal propia por usuario y los
  parametros guardados dentro del hash, asi se pueden subir mas adelante sin
  invalidar las claves ya cargadas. Nunca se guarda la clave.
- **El token de sesion se guarda hasheado.** Si alguien se lleva el archivo de la
  base no puede usar las sesiones abiertas, igual que con las claves.
- **Sesiones revocables** y con vencimiento: estan en una tabla, no en una cookie
  firmada, asi cerrar sesion o dar de baja a alguien corta el acceso de verdad.
- **Bloqueo por intentos fallidos**, para que probar claves de a una no sirva.
- **CSRF por token propio en un encabezado**: una cookie sola alcanzaria para que
  otro sitio dispare un POST en nombre del usuario.

Lo que esto NO resuelve y hay que resolver al publicar: **HTTPS**. Sin TLS la
clave viaja en claro y nada de lo de arriba importa. Ver servidor.py.
"""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Parametros de scrypt. Se guardan en el hash, asi que subirlos no rompe nada:
# las claves viejas siguen verificando con los suyos y se re-hashean al entrar.
SCRYPT_N = 2 ** 16
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 256 * 1024 * 1024
LARGO_SAL = 16
LARGO_CLAVE_DERIVADA = 32

DURACION_SESION = timedelta(hours=12)
INTENTOS_ANTES_DE_BLOQUEAR = 5
DURACION_BLOQUEO = timedelta(minutes=15)

MINIMO_CLAVE = 10

ROLES = {
    # rol        -> que puede hacer
    "admin":     "todo, incluida la configuracion y los usuarios",
    "operador":  "facturar, cobrar y cargar pacientes; no toca configuracion",
    "lectura":   "solo mirar",
}


class ErrorAutenticacion(Exception):
    """Credenciales, bloqueo o permisos. El mensaje es el que ve el usuario."""


# --------------------------------------------------------------------------
# Esquema
# --------------------------------------------------------------------------

ESQUEMA_AUTH = """
CREATE TABLE IF NOT EXISTS usuario (
    id                INTEGER PRIMARY KEY,
    nombre_usuario    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nombre            TEXT NOT NULL DEFAULT '',
    clave_hash        TEXT NOT NULL,
    rol               TEXT NOT NULL DEFAULT 'operador',
    activo            INTEGER NOT NULL DEFAULT 1,
    creado            TEXT NOT NULL,
    ultimo_acceso     TEXT,
    intentos_fallidos INTEGER NOT NULL DEFAULT 0,
    bloqueado_hasta   TEXT,
    debe_cambiar_clave INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sesion (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    csrf        TEXT NOT NULL,
    usuario_id  INTEGER NOT NULL REFERENCES usuario(id),
    creada      TEXT NOT NULL,
    expira      TEXT NOT NULL,
    ultimo_uso  TEXT NOT NULL,
    ip          TEXT NOT NULL DEFAULT '',
    agente      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_sesion_usuario ON sesion (usuario_id);

-- Quien hizo que y cuando. El diseño dice que facturas y pagos son hechos que
-- no se editan; esto completa esa idea con el autor.
CREATE TABLE IF NOT EXISTS auditoria (
    id         INTEGER PRIMARY KEY,
    momento    TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuario(id),
    usuario    TEXT NOT NULL DEFAULT '',
    accion     TEXT NOT NULL,
    detalle    TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_auditoria_momento ON auditoria (momento);
"""


def crear_esquema(con: sqlite3.Connection) -> None:
    con.executescript(ESQUEMA_AUTH)
    con.commit()


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat()


def _de_iso(t: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(t) if t else None


# --------------------------------------------------------------------------
# Claves
# --------------------------------------------------------------------------

def hashear_clave(clave: str) -> str:
    """'scrypt$n$r$p$sal_hex$derivada_hex'. Los parametros viajan con el hash."""
    sal = os.urandom(LARGO_SAL)
    derivada = hashlib.scrypt(
        clave.encode("utf-8"), salt=sal, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=LARGO_CLAVE_DERIVADA, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${sal.hex()}${derivada.hex()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    """Comparacion en tiempo constante: comparar con == filtra informacion."""
    try:
        algoritmo, n, r, p, sal_hex, esperado_hex = guardado.split("$")
        if algoritmo != "scrypt":
            return False
        derivada = hashlib.scrypt(
            clave.encode("utf-8"), salt=bytes.fromhex(sal_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(esperado_hex) // 2, maxmem=SCRYPT_MAXMEM)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derivada.hex(), esperado_hex)


def hay_que_rehashear(guardado: str) -> bool:
    """True si la clave se guardo con parametros mas debiles que los de hoy."""
    try:
        algoritmo, n, r, p, _, _ = guardado.split("$")
    except ValueError:
        return True
    return (algoritmo != "scrypt" or int(n) < SCRYPT_N
            or int(r) < SCRYPT_R or int(p) < SCRYPT_P)


def revisar_clave(clave: str, nombre_usuario: str = "") -> None:
    """Reglas minimas. Largo antes que composicion: una frase larga es mejor
    que 'Abc123!' y mas facil de recordar."""
    if len(clave) < MINIMO_CLAVE:
        raise ErrorAutenticacion(
            f"La clave necesita al menos {MINIMO_CLAVE} caracteres. "
            f"Una frase de varias palabras sirve y es mas facil de recordar.")
    if nombre_usuario and clave.lower() == nombre_usuario.lower():
        raise ErrorAutenticacion("La clave no puede ser el nombre de usuario.")
    if re.fullmatch(r"(.)\1*", clave):
        raise ErrorAutenticacion("La clave no puede ser un solo caracter repetido.")
    comunes = {"contrasena", "contraseña", "password", "123456789",
               "1234567890", "qwertyuiop", "administrador"}
    if clave.lower() in comunes:
        raise ErrorAutenticacion("Esa clave es de las primeras que se prueban.")


def revisar_nombre_usuario(nombre: str) -> str:
    n = (nombre or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", n):
        raise ErrorAutenticacion(
            "El nombre de usuario va de 3 a 32 caracteres, con letras, numeros, "
            "punto, guion o guion bajo.")
    return n


# --------------------------------------------------------------------------
# Usuarios
# --------------------------------------------------------------------------

@dataclass
class Usuario:
    id: int
    nombre_usuario: str
    nombre: str
    rol: str
    activo: bool
    debe_cambiar_clave: bool = False
    ultimo_acceso: Optional[datetime] = None

    def puede(self, permiso: str) -> bool:
        """admin hace todo; operador escribe salvo configuracion; lectura mira."""
        if not self.activo:
            return False
        if self.rol == "admin":
            return True
        if self.rol == "operador":
            return permiso in ("ver", "facturar")
        if self.rol == "lectura":
            return permiso == "ver"
        return False


def _usuario_de(fila) -> Usuario:
    return Usuario(
        id=fila["id"], nombre_usuario=fila["nombre_usuario"],
        nombre=fila["nombre"], rol=fila["rol"], activo=bool(fila["activo"]),
        debe_cambiar_clave=bool(fila["debe_cambiar_clave"]),
        ultimo_acceso=_de_iso(fila["ultimo_acceso"]))


def crear_usuario(con, nombre_usuario: str, clave: str, rol: str = "operador",
                  nombre: str = "", debe_cambiar_clave: bool = False) -> Usuario:
    nombre_usuario = revisar_nombre_usuario(nombre_usuario)
    if rol not in ROLES:
        raise ErrorAutenticacion(
            f"Rol desconocido: {rol}. Los que hay son {', '.join(ROLES)}.")
    revisar_clave(clave, nombre_usuario)
    try:
        cur = con.execute(
            """INSERT INTO usuario
                 (nombre_usuario, nombre, clave_hash, rol, creado,
                  debe_cambiar_clave)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (nombre_usuario, nombre, hashear_clave(clave), rol, _iso(ahora()),
             int(debe_cambiar_clave)))
        con.commit()
    except sqlite3.IntegrityError:
        raise ErrorAutenticacion(f"El usuario {nombre_usuario} ya existe.")
    return buscar_usuario(con, cur.lastrowid)


def buscar_usuario(con, usuario_id: int) -> Optional[Usuario]:
    f = con.execute("SELECT * FROM usuario WHERE id = ?", (usuario_id,)).fetchone()
    return _usuario_de(f) if f else None


def buscar_por_nombre(con, nombre_usuario: str) -> Optional[Usuario]:
    f = con.execute("SELECT * FROM usuario WHERE nombre_usuario = ?",
                    (nombre_usuario,)).fetchone()
    return _usuario_de(f) if f else None


def listar_usuarios(con) -> list[Usuario]:
    return [_usuario_de(f) for f in
            con.execute("SELECT * FROM usuario ORDER BY nombre_usuario")]


def hay_usuarios(con) -> bool:
    return con.execute(
        "SELECT 1 FROM usuario WHERE activo = 1 LIMIT 1").fetchone() is not None


def cambiar_clave(con, usuario_id: int, clave_nueva: str,
                  clave_actual: Optional[str] = None) -> None:
    """Si se pasa clave_actual, se exige que coincida: es el cambio que hace el
    propio usuario. Sin ella es un blanqueo hecho por un admin."""
    f = con.execute("SELECT * FROM usuario WHERE id = ?", (usuario_id,)).fetchone()
    if not f:
        raise ErrorAutenticacion("El usuario no existe.")
    if clave_actual is not None and not verificar_clave(clave_actual, f["clave_hash"]):
        raise ErrorAutenticacion("La clave actual no coincide.")
    revisar_clave(clave_nueva, f["nombre_usuario"])
    con.execute(
        "UPDATE usuario SET clave_hash = ?, debe_cambiar_clave = 0 WHERE id = ?",
        (hashear_clave(clave_nueva), usuario_id))
    con.commit()
    # Cambiar la clave cierra las demas sesiones: es el sentido de cambiarla.
    cerrar_sesiones_de(con, usuario_id)


def cambiar_rol(con, usuario_id: int, rol: str) -> None:
    if rol not in ROLES:
        raise ErrorAutenticacion(f"Rol desconocido: {rol}.")
    con.execute("UPDATE usuario SET rol = ? WHERE id = ?", (rol, usuario_id))
    con.commit()


def activar_usuario(con, usuario_id: int, activo: bool) -> None:
    """Baja logica, como el resto del sistema. Dar de baja corta las sesiones."""
    con.execute("UPDATE usuario SET activo = ? WHERE id = ?",
                (int(activo), usuario_id))
    con.commit()
    if not activo:
        cerrar_sesiones_de(con, usuario_id)


# --------------------------------------------------------------------------
# Ingreso
# --------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class SesionAbierta:
    token: str
    csrf: str
    usuario: Usuario
    expira: datetime


def ingresar(con, nombre_usuario: str, clave: str, ip: str = "",
             agente: str = "") -> SesionAbierta:
    """Verifica y abre sesion. El mensaje de error no distingue entre usuario
    inexistente y clave incorrecta: decirlo permitiria averiguar quien existe."""
    generico = "Usuario o clave incorrectos."
    fila = con.execute("SELECT * FROM usuario WHERE nombre_usuario = ?",
                       ((nombre_usuario or "").strip(),)).fetchone()

    if fila is None:
        # Se verifica igual contra un hash descartable para que el tiempo de
        # respuesta no delate si el usuario existe.
        verificar_clave(clave or "", hashear_clave(secrets.token_urlsafe(16)))
        raise ErrorAutenticacion(generico)

    bloqueado = _de_iso(fila["bloqueado_hasta"])
    if bloqueado and bloqueado > ahora():
        faltan = int((bloqueado - ahora()).total_seconds() // 60) + 1
        raise ErrorAutenticacion(
            f"Usuario bloqueado por intentos fallidos. Volvé a probar en "
            f"{faltan} minuto{'s' if faltan != 1 else ''}.")

    if not verificar_clave(clave or "", fila["clave_hash"]):
        intentos = fila["intentos_fallidos"] + 1
        if intentos >= INTENTOS_ANTES_DE_BLOQUEAR:
            con.execute(
                "UPDATE usuario SET intentos_fallidos = 0, bloqueado_hasta = ? "
                "WHERE id = ?", (_iso(ahora() + DURACION_BLOQUEO), fila["id"]))
            con.commit()
            raise ErrorAutenticacion(
                f"Usuario bloqueado por {int(DURACION_BLOQUEO.total_seconds() // 60)} "
                f"minutos tras {INTENTOS_ANTES_DE_BLOQUEAR} intentos fallidos.")
        con.execute("UPDATE usuario SET intentos_fallidos = ? WHERE id = ?",
                    (intentos, fila["id"]))
        con.commit()
        raise ErrorAutenticacion(generico)

    # La cuenta inactiva se avisa recien despues de validar la clave, para no
    # revelar que ese usuario existe a quien no sabe la clave.
    if not fila["activo"]:
        raise ErrorAutenticacion("La cuenta esta dada de baja.")

    if hay_que_rehashear(fila["clave_hash"]):
        con.execute("UPDATE usuario SET clave_hash = ? WHERE id = ?",
                    (hashear_clave(clave), fila["id"]))

    con.execute(
        "UPDATE usuario SET intentos_fallidos = 0, bloqueado_hasta = NULL, "
        "ultimo_acceso = ? WHERE id = ?", (_iso(ahora()), fila["id"]))

    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expira = ahora() + DURACION_SESION
    con.execute(
        """INSERT INTO sesion
             (token_hash, csrf, usuario_id, creada, expira, ultimo_uso, ip, agente)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (_hash_token(token), csrf, fila["id"], _iso(ahora()), _iso(expira),
         _iso(ahora()), ip, (agente or "")[:200]))
    con.commit()
    limpiar_sesiones_vencidas(con)

    return SesionAbierta(token=token, csrf=csrf, usuario=_usuario_de(fila),
                         expira=expira)


def sesion_de_token(con, token: Optional[str]) -> Optional[tuple[Usuario, str]]:
    """Devuelve (usuario, csrf) si el token sirve. Renueva el vencimiento."""
    if not token:
        return None
    fila = con.execute("SELECT * FROM sesion WHERE token_hash = ?",
                       (_hash_token(token),)).fetchone()
    if fila is None:
        return None
    if _de_iso(fila["expira"]) <= ahora():
        con.execute("DELETE FROM sesion WHERE id = ?", (fila["id"],))
        con.commit()
        return None

    usuario = buscar_usuario(con, fila["usuario_id"])
    if usuario is None or not usuario.activo:
        con.execute("DELETE FROM sesion WHERE id = ?", (fila["id"],))
        con.commit()
        return None

    con.execute("UPDATE sesion SET ultimo_uso = ?, expira = ? WHERE id = ?",
                (_iso(ahora()), _iso(ahora() + DURACION_SESION), fila["id"]))
    con.commit()
    return usuario, fila["csrf"]


def cerrar_sesion(con, token: Optional[str]) -> None:
    if token:
        con.execute("DELETE FROM sesion WHERE token_hash = ?", (_hash_token(token),))
        con.commit()


def cerrar_sesiones_de(con, usuario_id: int) -> None:
    con.execute("DELETE FROM sesion WHERE usuario_id = ?", (usuario_id,))
    con.commit()


def limpiar_sesiones_vencidas(con) -> None:
    con.execute("DELETE FROM sesion WHERE expira <= ?", (_iso(ahora()),))
    con.commit()


def sesiones_de(con, usuario_id: int) -> list[dict]:
    return [{"creada": f["creada"], "ultimo_uso": f["ultimo_uso"],
             "expira": f["expira"], "ip": f["ip"], "agente": f["agente"]}
            for f in con.execute(
                "SELECT * FROM sesion WHERE usuario_id = ? ORDER BY ultimo_uso DESC",
                (usuario_id,))]


# --------------------------------------------------------------------------
# Auditoria
# --------------------------------------------------------------------------

def auditar(con, usuario: Optional[Usuario], accion: str, detalle: str = "",
            ip: str = "") -> None:
    con.execute(
        """INSERT INTO auditoria (momento, usuario_id, usuario, accion, detalle, ip)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (_iso(ahora()), usuario.id if usuario else None,
         usuario.nombre_usuario if usuario else "", accion, detalle[:500], ip))
    con.commit()


def auditoria(con, limite: int = 200) -> list[dict]:
    return [{"momento": f["momento"], "usuario": f["usuario"],
             "accion": f["accion"], "detalle": f["detalle"], "ip": f["ip"]}
            for f in con.execute(
                "SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))]
