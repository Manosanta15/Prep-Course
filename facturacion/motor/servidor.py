#!/usr/bin/env python3
"""Servidor HTTP del sistema. Solo biblioteca estandar: no hay que instalar nada.

    python3 usuarios.py crear --rol admin    # el primer usuario
    python3 servidor.py                      # http://localhost:8080

La API devuelve JSON y es la misma que va a consumir el programa de gestion
administrativa cuando esto sea una parte de el. La pagina de web/ es un cliente
de esa API, no tiene logica propia: todas las reglas estan en el motor.

Que protege este servidor:

- **Todo /api/ pide sesion**, salvo el ingreso. Sin cookie valida devuelve 401.
- **Permisos por rol** en cada ruta: lectura mira, operador factura, admin
  ademas configura y maneja usuarios.
- **CSRF**: los POST exigen el encabezado X-CSRF-Token de la sesion. La cookie
  sola alcanzaria para que otro sitio dispare un POST en nombre del usuario.
- **Cookie HttpOnly y SameSite=Strict**, y Secure cuando corre tras HTTPS, asi
  no la lee el javascript de la pagina ni viaja a otro sitio.
- **Auditoria**: cada cambio queda con usuario, momento e IP.
- **Limite de intentos por IP**, ademas del bloqueo por usuario.

Lo que este servidor NO puede resolver solo: **HTTPS**. Sin TLS la clave viaja
en claro y lo de arriba no sirve de nada. Para publicarlo va detras de nginx o
Caddy con certificado, y se arranca con --detras-de-proxy-https. Por eso el
servidor se niega a escuchar fuera de la propia maquina sin esa opcion.
"""

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
import traceback
from datetime import date
from functools import partial
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import autenticacion as auth
import persistencia as db
import servicios
from facturacion import OperacionNoPermitida
from feriados import feriados_nacionales, solo_habiles
from modelo import (
    Autorizacion,
    AutorizacionDia,
    CondicionIVA,
    ConvenioApross,
    Feriado,
    Internacion,
    MedioPago,
    MotivoNoFacturable,
    ObraSocial,
    Paciente,
    ValorModulo,
)
from sesiones import ValorModuloFaltante

WEB = Path(__file__).parent.parent / "web"
TIPOS = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".ico": "image/x-icon"}

COOKIE = "sesion"
HOSTS_LOCALES = {"127.0.0.1", "::1", "localhost"}

# Permiso que pide cada ruta. Lo que no figura pide "configurar", que es el mas
# restrictivo de los de escritura: una ruta nueva queda cerrada por omision en
# lugar de quedar abierta por olvido.
PERMISOS = {
    "/api/valor-modulo": "configurar",
    "/api/obra-social": "configurar",
    "/api/feriado": "configurar",
    "/api/feriado/borrar": "configurar",
    "/api/feriados-sugeridos/importar": "configurar",
    "/api/confirmar/valor-modulo": "configurar",
    "/api/confirmar/feriado": "configurar",
    "/api/paciente": "facturar",
    "/api/paciente/baja": "facturar",
    "/api/autorizacion": "facturar",
    "/api/internacion": "facturar",
    "/api/excepcion": "facturar",
    "/api/factura/emitir": "facturar",
    "/api/factura/anular": "facturar",
    "/api/pago": "facturar",
    "/api/pago/revertir": "facturar",
    "/api/apross": "facturar",
    "/api/confirmar/anulacion": "facturar",
    "/api/confirmar/baja-paciente": "facturar",
    "/api/usuario": "usuarios",
    "/api/usuario/rol": "usuarios",
    "/api/usuario/activar": "usuarios",
    "/api/usuario/blanquear-clave": "usuarios",
}

# Que se escribe en la auditoria por cada ruta. Lo que no figura no se audita
# porque no cambia nada.
AUDITA = {
    "/api/factura/emitir": "factura.emitir",
    "/api/factura/anular": "factura.anular",
    "/api/pago": "pago.registrar",
    "/api/pago/revertir": "pago.revertir",
    "/api/valor-modulo": "config.valor-modulo",
    "/api/obra-social": "config.obra-social",
    "/api/feriado": "config.feriado.agregar",
    "/api/feriado/borrar": "config.feriado.quitar",
    "/api/feriados-sugeridos/importar": "config.feriados.importar",
    "/api/paciente": "paciente.guardar",
    "/api/paciente/baja": "paciente.baja",
    "/api/autorizacion": "autorizacion.guardar",
    "/api/internacion": "internacion.guardar",
    "/api/excepcion": "sesion.excepcion",
    "/api/apross": "apross.guardar",
    "/api/usuario": "usuario.crear",
    "/api/usuario/rol": "usuario.rol",
    "/api/usuario/activar": "usuario.activar",
    "/api/usuario/blanquear-clave": "usuario.blanquear",
}

MAX_CUERPO = 1 * 1024 * 1024        # 1 MB: ningun pedido legitimo pasa de ahi


class LimiteIngresos:
    """Freno por IP, ademas del bloqueo por usuario.

    El bloqueo por usuario no alcanza: alguien puede probar una clave contra
    muchos usuarios distintos sin bloquear a ninguno.
    """

    def __init__(self, maximo=10, ventana=300):
        self.maximo, self.ventana = maximo, ventana
        self.intentos: dict[str, list[float]] = {}
        self.candado = threading.Lock()

    def permitido(self, ip: str) -> bool:
        ahora = time.monotonic()
        with self.candado:
            recientes = [t for t in self.intentos.get(ip, [])
                         if ahora - t < self.ventana]
            self.intentos[ip] = recientes
            return len(recientes) < self.maximo

    def anotar_fallo(self, ip: str) -> None:
        with self.candado:
            self.intentos.setdefault(ip, []).append(time.monotonic())

    def limpiar(self, ip: str) -> None:
        with self.candado:
            self.intentos.pop(ip, None)


LIMITE = LimiteIngresos()


def hoy_de(params) -> date:
    """Permite mirar el tablero a una fecha distinta: ?hoy=2026-12-01.

    Es clave para la puesta a punto, porque el semaforo depende de la fecha y
    asi se puede ver como se va a ver en diciembre sin esperar.
    """
    v = params.get("hoy", [None])[0]
    return date.fromisoformat(v) if v else date.today()


def confirmacion_json(c) -> dict:
    return {"nivel": int(c.nivel), "titulo": c.titulo, "detalle": c.detalle,
            "mensaje": c.mensaje, "texto_a_escribir": c.texto_a_escribir}


class Handler(BaseHTTPRequestHandler):
    # Sin version: decir que version corre solo le sirve a quien busca fallas.
    server_version = "servidor"
    sys_version = ""

    def __init__(self, *args, base="facturacion.db", seguro=False, **kw):
        self.base = base
        self.seguro = seguro          # True cuando corre detras de HTTPS
        self.usuario = None
        self.csrf = None
        super().__init__(*args, **kw)

    # ----- sesion -----------------------------------------------------
    @property
    def ip(self) -> str:
        """Con --detras-de-proxy-https la IP real viene en X-Forwarded-For."""
        if self.seguro:
            reenviada = self.headers.get("X-Forwarded-For", "")
            if reenviada:
                return reenviada.split(",")[0].strip()[:45]
        return self.client_address[0]

    def token_de_cookie(self):
        crudo = self.headers.get("Cookie")
        if not crudo:
            return None
        try:
            galletas = SimpleCookie()
            galletas.load(crudo)
        except Exception:
            return None
        morsel = galletas.get(COOKIE)
        return morsel.value if morsel else None

    def resolver_sesion(self, con):
        hallada = auth.sesion_de_token(con, self.token_de_cookie())
        if hallada:
            self.usuario, self.csrf = hallada
        return self.usuario

    def mandar_cookie(self, token, expira):
        partes = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict",
                  f"Expires={expira.strftime('%a, %d %b %Y %H:%M:%S GMT')}"]
        if self.seguro:
            partes.append("Secure")
        self.send_header("Set-Cookie", "; ".join(partes))

    def borrar_cookie(self):
        partes = [f"{COOKIE}=", "Path=/", "HttpOnly", "SameSite=Strict",
                  "Max-Age=0"]
        if self.seguro:
            partes.append("Secure")
        self.send_header("Set-Cookie", "; ".join(partes))

    # ----- utilidades -------------------------------------------------
    def log_message(self, formato, *args):
        pass          # sin ruido en consola

    def cabeceras_seguridad(self):
        # Sin scripts de afuera, sin quedar dentro de un iframe ajeno, y sin
        # mandar la direccion a otros sitios.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "form-action 'none'; frame-ancestors 'none'; "
                         "base-uri 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if self.seguro:
            self.send_header("Strict-Transport-Security",
                             "max-age=31536000; includeSubDomains")

    def responder(self, datos, codigo=200, cookie=None, borrar_cookie=False):
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.cabeceras_seguridad()
        if cookie:
            self.mandar_cookie(*cookie)
        if borrar_cookie:
            self.borrar_cookie()
        self.end_headers()
        self.wfile.write(cuerpo)

    def error(self, mensaje, codigo=400):
        self.responder({"error": mensaje}, codigo)

    def cuerpo(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_CUERPO:
            return {}
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def archivo(self, ruta: str):
        if ruta in ("/", ""):
            ruta = "/index.html"
        destino = (WEB / ruta.lstrip("/")).resolve()
        if not str(destino).startswith(str(WEB.resolve())) or not destino.is_file():
            return self.error("No encontrado", 404)
        cuerpo = destino.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         TIPOS.get(destino.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    # ----- ruteo ------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            # La pagina no trae datos: pide todo a la API, que si pide sesion.
            return self.archivo(u.path)
        con = db.conectar(self.base)
        try:
            if u.path == "/api/sesion/actual":
                return self.sesion_actual(con)
            if not self.resolver_sesion(con):
                return self.error("Hay que iniciar sesión.", 401)
            if not self.usuario.puede("ver"):
                return self.error("No tenés permiso para ver esto.", 403)
            self.get_api(u.path, parse_qs(u.query), con)
        except (OperacionNoPermitida, ValorModuloFaltante) as e:
            self.error(str(e))
        except auth.ErrorAutenticacion as e:
            self.error(str(e), 403)
        except sqlite3.IntegrityError as e:
            self.error(explicar_integridad(e))
        except Exception:
            traceback.print_exc()
            self.error("Error interno", 500)
        finally:
            con.close()

    def do_POST(self):
        u = urlparse(self.path)
        con = db.conectar(self.base)
        try:
            cuerpo = self.cuerpo()

            # El ingreso es la unica ruta sin sesion previa.
            if u.path == "/api/sesion":
                return self.ingresar(con, cuerpo)

            if not self.resolver_sesion(con):
                return self.error("Hay que iniciar sesión.", 401)

            # CSRF: sin este encabezado, otro sitio podria disparar el POST
            # usando la cookie del navegador.
            if self.headers.get("X-CSRF-Token") != self.csrf:
                return self.error(
                    "La sesión cambió o expiró. Volvé a cargar la página.", 403)

            if u.path == "/api/sesion/cerrar":
                auth.cerrar_sesion(con, self.token_de_cookie())
                auth.auditar(con, self.usuario, "sesion.cerrar", "", self.ip)
                return self.responder({"ok": True}, borrar_cookie=True)

            if u.path == "/api/sesion/cambiar-clave":
                auth.cambiar_clave(con, self.usuario.id,
                                   str(cuerpo.get("clave_nueva", "")),
                                   clave_actual=str(cuerpo.get("clave_actual", "")))
                auth.auditar(con, self.usuario, "usuario.clave-propia", "", self.ip)
                # cambiar_clave cierra las sesiones, incluida esta.
                return self.responder(
                    {"ok": True, "mensaje": "Clave cambiada. Volvé a ingresar."},
                    borrar_cookie=True)

            permiso = PERMISOS.get(u.path, "configurar")
            if not self.usuario.puede(permiso):
                return self.error(
                    f"Tu rol ({self.usuario.rol}) no puede hacer esto.", 403)

            self.post_api(u.path, parse_qs(u.query), cuerpo, con)

            accion = AUDITA.get(u.path)
            if accion:
                auth.auditar(con, self.usuario, accion, resumen(u.path, cuerpo),
                             self.ip)
        except (OperacionNoPermitida, ValorModuloFaltante) as e:
            self.error(str(e))
        except auth.ErrorAutenticacion as e:
            self.error(str(e), 400)
        except sqlite3.IntegrityError as e:
            self.error(explicar_integridad(e))
        except (ValueError, KeyError) as e:
            self.error(f"Datos inválidos: {e}")
        except Exception:
            traceback.print_exc()
            self.error("Error interno", 500)
        finally:
            con.close()

    # ----- sesion -----------------------------------------------------
    def ingresar(self, con, cuerpo):
        if not auth.hay_usuarios(con):
            return self.error(
                "No hay ningún usuario cargado. Creá el primero desde la "
                "máquina del servidor con: python3 usuarios.py crear --rol admin",
                409)
        if not LIMITE.permitido(self.ip):
            return self.error(
                "Demasiados intentos desde esta dirección. Esperá unos minutos.",
                429)
        try:
            s = auth.ingresar(con, str(cuerpo.get("usuario", "")),
                              str(cuerpo.get("clave", "")), self.ip,
                              self.headers.get("User-Agent", ""))
        except auth.ErrorAutenticacion as e:
            LIMITE.anotar_fallo(self.ip)
            auth.auditar(con, None, "sesion.fallida",
                         f"usuario {str(cuerpo.get('usuario', ''))[:40]!r}", self.ip)
            return self.error(str(e), 401)

        LIMITE.limpiar(self.ip)
        auth.auditar(con, s.usuario, "sesion.ingreso", "", self.ip)
        return self.responder(
            {"ok": True, "usuario": usuario_json(s.usuario), "csrf": s.csrf},
            cookie=(s.token, s.expira))

    def sesion_actual(self, con):
        """La pagina llama a esto al cargar: 401 significa mostrar el ingreso."""
        if not self.resolver_sesion(con):
            return self.responder(
                {"autenticado": False, "hay_usuarios": auth.hay_usuarios(con)}, 401)
        return self.responder({
            "autenticado": True, "usuario": usuario_json(self.usuario),
            "csrf": self.csrf,
            "permisos": {p: self.usuario.puede(p)
                         for p in ("ver", "facturar", "configurar", "usuarios")},
        })

    # ----- GET --------------------------------------------------------
    def get_api(self, ruta, params, con):
        anio = int(params.get("anio", [date.today().year])[0])
        hoy = hoy_de(params)

        if ruta == "/api/configuracion":
            return self.responder({
                "hoy": date.today().isoformat(),
                "obras_sociales": [
                    {"id": o.id, "nombre": o.nombre, "cuit": o.cuit,
                     "condicion_iva": o.condicion_iva.value,
                     "tiene_tope": o.tiene_tope,
                     "tope_sesiones_mes": o.tope_sesiones_mes,
                     "dias_vencimiento": o.dias_vencimiento, "activa": o.activa}
                    for o in db.obras_sociales(con).values()],
                "modulos": [{"codigo": m.codigo, "nombre": m.nombre,
                             "activo": m.activo} for m in db.modulos(con)],
                "profesionales": [{"id": p.id, "nombre": p.nombre,
                                   "activo": p.activo}
                                  for p in db.profesionales(con).values()],
                "valores": [{"modulo": v.modulo_codigo, "anio": v.anio,
                             "mes": v.mes, "valor_sesion": v.valor_sesion,
                             "tope_sesiones": v.tope_sesiones}
                            for v in db.valores_modulo(con, anio)],
                "feriados": [{"fecha": f.fecha.isoformat(),
                              "descripcion": f.descripcion, "tipo": f.tipo.value}
                             for f in db.feriados(con, anio)],
            })

        if ruta == "/api/pacientes":
            oses, profs = db.obras_sociales(con), db.profesionales(con)
            return self.responder([
                {"id": p.id, "nombre": p.apellido_nombre, "dni": p.dni,
                 "cuil": p.cuil, "nro_afiliado": p.nro_afiliado,
                 "obra_social": oses[p.obra_social_id].nombre,
                 "obra_social_id": p.obra_social_id, "modulo": p.modulo_codigo,
                 "profesional": profs[p.profesional_id].nombre
                 if p.profesional_id else None,
                 "profesional_id": p.profesional_id,
                 "datos_fiscales_completos": p.datos_fiscales_completos}
                for p in db.pacientes(con)])

        m = re.fullmatch(r"/api/facturacion/(\d{4})/(\d{1,2})", ruta)
        if m:
            a, mes = int(m[1]), int(m[2])
            filas = servicios.facturacion_del_mes(con, a, mes, hoy)
            return self.responder({
                "anio": a, "mes": mes, "hoy": hoy.isoformat(),
                "filas": [{
                    "paciente_id": f.paciente.id,
                    "paciente": f.paciente.apellido_nombre,
                    "obra_social": f.obra_social, "profesional": f.profesional,
                    "modulo": f.modulo, "sesiones": f.sesiones,
                    "explicacion": f.explicacion, "valor_sesion": f.valor_sesion,
                    "monto": f.monto, "tope_aplicado": f.tope_aplicado,
                    "factura_id": f.factura_id, "nro_factura": f.nro_factura,
                    "estado": f.estado.value, "color": f.color,
                    "cobrado": f.cobrado, "saldo": f.saldo, "dias": f.dias,
                    "aviso": f.aviso} for f in filas],
            })

        m = re.fullmatch(r"/api/tablero/(\d{4})", ruta)
        if m:
            a = int(m[1])
            t = servicios.tablero(con, a, hoy)
            r = servicios.resumen_anual(con, a, hoy)
            profs = db.profesionales(con)
            pacs = {p.id: p for p in db.pacientes(con, solo_activos=False)}
            return self.responder({
                "anio": a, "hoy": hoy.isoformat(),
                "pacientes": [
                    {"id": pid, "nombre": pacs[pid].apellido_nombre
                     if pid in pacs else f"#{pid}",
                     "total": t.total_por_paciente.get(pid, 0.0),
                     "meses": {str(mes): ({"monto": c.factura.monto,
                                           "color": c.color,
                                           "estado": c.estado.value,
                                           "saldo": c.saldo,
                                           "dias": c.dias,
                                           "nro": c.factura.nro_factura}
                                          if (c := t.celda(pid, mes)) else None)
                               for mes in range(1, 13)}}
                    for pid in sorted(t.celdas)],
                "total_por_mes": {str(k): v for k, v in t.total_por_mes.items()},
                "total_por_profesional": {
                    (profs[k].nombre if k in profs else "sin asignar"): v
                    for k, v in t.total_por_profesional.items()},
                "total_anual": t.total_anual,
                "resumen": {"facturado": r.facturado, "cobrado": r.cobrado,
                            "parcial_pendiente": r.parcial_pendiente,
                            "pendiente": r.pendiente, "atrasado": r.atrasado,
                            "por_cobrar": r.por_cobrar,
                            "cantidad_por_estado": {
                                e.value: n
                                for e, n in r.cantidad_por_estado.items()}},
            })

        m = re.fullmatch(r"/api/paciente/(\d+)/(\d{4})", ruta)
        if m:
            return self.responder(
                servicios.ficha_paciente(con, int(m[1]), int(m[2]), hoy))

        m = re.fullmatch(r"/api/factura/(\d+)/pagos", ruta)
        if m:
            return self.responder([
                {"id": p.id, "fecha_pago": p.fecha_pago.isoformat(),
                 "monto": p.monto, "medio_pago": p.medio_pago.value,
                 "observaciones": p.observaciones,
                 "es_reversion": p.es_reversion}
                for p in db.pagos(con, int(m[1]))])

        m = re.fullmatch(r"/api/apross/(\d{4})", ruta)
        if m:
            return self.responder([
                {"anio": c.anio, "mes": c.mes,
                 "cantidad_pacientes": c.cantidad_pacientes,
                 "cantidad_sesiones": c.cantidad_sesiones,
                 "valor_sesion": c.valor_sesion,
                 "porcentaje_descuento": c.porcentaje_descuento,
                 "total_bruto": c.total_bruto,
                 "monto_descuento": c.monto_descuento,
                 "importe_abonado": c.importe_abonado,
                 "fecha_pago": c.fecha_pago.isoformat() if c.fecha_pago else None,
                 "medio_pago": c.medio_pago.value if c.medio_pago else None,
                 "estado": estado}
                for c, estado in db.apross(con, int(m[1]))])

        if ruta == "/api/usuarios":
            if not self.usuario.puede("usuarios"):
                return self.error("Solo un administrador ve los usuarios.", 403)
            return self.responder([usuario_json(u) for u in auth.listar_usuarios(con)])

        if ruta == "/api/auditoria":
            if not self.usuario.puede("usuarios"):
                return self.error("Solo un administrador ve la auditoría.", 403)
            limite = min(int(params.get("limite", [200])[0]), 1000)
            return self.responder(auth.auditoria(con, limite))

        if ruta == "/api/feriados-sugeridos":
            return self.responder([
                {"fecha": f.fecha.isoformat(), "descripcion": f.descripcion,
                 "tipo": f.tipo.value}
                for f in solo_habiles(feriados_nacionales(anio))])

        self.error("Ruta no encontrada", 404)

    # ----- POST -------------------------------------------------------
    def post_api(self, ruta, params, cuerpo, con):
        # --- confirmaciones: se piden antes de aplicar el cambio ---
        if ruta == "/api/confirmar/valor-modulo":
            return self.responder(confirmacion_json(
                servicios.confirmacion_valor_modulo(
                    con, cuerpo["modulo"], int(cuerpo["anio"]),
                    int(cuerpo["mes"]), float(cuerpo["valor_sesion"]))))

        if ruta == "/api/confirmar/feriado":
            return self.responder(confirmacion_json(
                servicios.confirmacion_feriado(
                    con, date.fromisoformat(cuerpo["fecha"]),
                    cuerpo.get("descripcion", ""))))

        if ruta == "/api/confirmar/anulacion":
            return self.responder(confirmacion_json(
                servicios.confirmacion_anulacion(con, int(cuerpo["factura_id"]))))

        if ruta == "/api/confirmar/baja-paciente":
            return self.responder(confirmacion_json(
                servicios.confirmacion_baja_paciente(
                    con, int(cuerpo["paciente_id"]))))

        # --- configuracion ---
        if ruta == "/api/valor-modulo":
            db.guardar_valor_modulo(con, ValorModulo(
                modulo_codigo=cuerpo["modulo"], anio=int(cuerpo["anio"]),
                mes=int(cuerpo["mes"]),
                valor_sesion=float(cuerpo["valor_sesion"]),
                tope_sesiones=(int(cuerpo["tope_sesiones"])
                               if cuerpo.get("tope_sesiones") else None)))
            return self.responder({"ok": True})

        if ruta == "/api/obra-social":
            db.guardar_obra_social(con, ObraSocial(
                id=int(cuerpo.get("id") or 0), nombre=cuerpo["nombre"],
                cuit=cuerpo.get("cuit"),
                condicion_iva=CondicionIVA(cuerpo.get("condicion_iva", "EXENTO")),
                tiene_tope=bool(cuerpo.get("tiene_tope")),
                tope_sesiones_mes=(int(cuerpo["tope_sesiones_mes"])
                                   if cuerpo.get("tope_sesiones_mes") else None),
                dias_vencimiento=int(cuerpo.get("dias_vencimiento", 60))))
            return self.responder({"ok": True})

        if ruta == "/api/feriado":
            db.guardar_feriados(con, [Feriado(
                fecha=date.fromisoformat(cuerpo["fecha"]),
                descripcion=cuerpo.get("descripcion", ""))])
            return self.responder({"ok": True})

        if ruta == "/api/feriado/borrar":
            db.borrar_feriado(con, date.fromisoformat(cuerpo["fecha"]))
            return self.responder({"ok": True})

        if ruta == "/api/feriados-sugeridos/importar":
            anio = int(cuerpo.get("anio", date.today().year))
            db.guardar_feriados(con, solo_habiles(feriados_nacionales(anio)))
            return self.responder({"ok": True,
                                   "cargados": len(db.feriados(con, anio))})

        # --- padron ---
        if ruta == "/api/paciente":
            pid = db.guardar_paciente(con, Paciente(
                id=int(cuerpo.get("id") or 0),
                apellido_nombre=cuerpo["nombre"],
                obra_social_id=int(cuerpo["obra_social_id"]),
                modulo_codigo=cuerpo["modulo"],
                profesional_id=(int(cuerpo["profesional_id"])
                                if cuerpo.get("profesional_id") else None),
                dni=cuerpo.get("dni") or None, cuil=cuerpo.get("cuil") or None,
                nro_afiliado=cuerpo.get("nro_afiliado") or None))
            return self.responder({"ok": True, "paciente_id": pid})

        if ruta == "/api/paciente/baja":
            db.baja_paciente(con, int(cuerpo["paciente_id"]))
            return self.responder({"ok": True})

        if ruta == "/api/autorizacion":
            aid = db.guardar_autorizacion(con, Autorizacion(
                id=int(cuerpo.get("id") or 0),
                paciente_id=int(cuerpo["paciente_id"]),
                vigencia_desde=date.fromisoformat(cuerpo["vigencia_desde"]),
                vigencia_hasta=(date.fromisoformat(cuerpo["vigencia_hasta"])
                                if cuerpo.get("vigencia_hasta") else None),
                dias=[AutorizacionDia(int(d["dia_semana"]), d.get("hora"))
                      for d in cuerpo.get("dias", [])],
                tope_sesiones_mes=(int(cuerpo["tope_sesiones_mes"])
                                   if cuerpo.get("tope_sesiones_mes") else None),
                observaciones=cuerpo.get("observaciones", "")))
            return self.responder({"ok": True, "autorizacion_id": aid})

        if ruta == "/api/internacion":
            db.guardar_internacion(con, Internacion(
                paciente_id=int(cuerpo["paciente_id"]),
                desde=date.fromisoformat(cuerpo["desde"]),
                hasta=(date.fromisoformat(cuerpo["hasta"])
                       if cuerpo.get("hasta") else None),
                institucion=cuerpo.get("institucion", ""),
                observaciones=cuerpo.get("observaciones", "")))
            return self.responder({"ok": True})

        if ruta == "/api/excepcion":
            db.guardar_excepcion(
                con, int(cuerpo["paciente_id"]),
                date.fromisoformat(cuerpo["fecha"]),
                MotivoNoFacturable(cuerpo.get("motivo", "AUSENCIA")),
                cuerpo.get("detalle", ""))
            return self.responder({"ok": True})

        # --- facturacion ---
        if ruta == "/api/factura/emitir":
            f = servicios.emitir_factura(
                con, int(cuerpo["paciente_id"]), int(cuerpo["anio"]),
                int(cuerpo["mes"]), str(cuerpo["nro_factura"]),
                date.fromisoformat(cuerpo["fecha_emision"]))
            return self.responder({"ok": True, "factura_id": f.id,
                                   "monto": f.monto,
                                   "cantidad_sesiones": f.cantidad_sesiones})

        if ruta == "/api/factura/anular":
            res, nueva = servicios.anular_factura(
                con, int(cuerpo["factura_id"]), cuerpo.get("motivo", ""),
                date.fromisoformat(cuerpo.get("fecha", date.today().isoformat())),
                bool(cuerpo.get("reemplazar")))
            return self.responder({"ok": True,
                                   "reversiones": len(res.reversiones),
                                   "nueva_factura_id": nueva.id if nueva else None})

        if ruta == "/api/pago":
            p = servicios.registrar_cobro(
                con, int(cuerpo["factura_id"]),
                date.fromisoformat(cuerpo["fecha_pago"]),
                float(cuerpo["monto"]) if cuerpo.get("monto") else None,
                cuerpo.get("medio_pago", "DEPOSITO"),
                cuerpo.get("observaciones", ""),
                bool(cuerpo.get("permitir_exceso")))
            return self.responder({"ok": True, "pago_id": p.id, "monto": p.monto})

        if ruta == "/api/pago/revertir":
            servicios.revertir_cobro(
                con, int(cuerpo["pago_id"]),
                date.fromisoformat(cuerpo.get("fecha", date.today().isoformat())),
                cuerpo.get("motivo", ""))
            return self.responder({"ok": True})

        # --- APROSS ---
        if ruta == "/api/apross":
            c = ConvenioApross(
                anio=int(cuerpo["anio"]), mes=int(cuerpo["mes"]),
                cantidad_pacientes=int(cuerpo.get("cantidad_pacientes", 0)),
                cantidad_sesiones=int(cuerpo.get("cantidad_sesiones", 0)),
                valor_sesion=float(cuerpo.get("valor_sesion", 0)),
                porcentaje_descuento=float(cuerpo.get("porcentaje_descuento", 4)),
                total_bruto_manual=(float(cuerpo["total_bruto_manual"])
                                    if cuerpo.get("total_bruto_manual") else None),
                fecha_pago=(date.fromisoformat(cuerpo["fecha_pago"])
                            if cuerpo.get("fecha_pago") else None),
                medio_pago=(MedioPago(cuerpo["medio_pago"])
                            if cuerpo.get("medio_pago") else None))
            db.guardar_apross(con, c, cuerpo.get("estado", "BORRADOR"))
            return self.responder({"ok": True, "total_bruto": c.total_bruto,
                                   "monto_descuento": c.monto_descuento,
                                   "importe_abonado": c.importe_abonado})

        # --- usuarios (solo admin; el permiso ya se verifico arriba) ---
        if ruta == "/api/usuario":
            u = auth.crear_usuario(
                con, str(cuerpo.get("usuario", "")), str(cuerpo.get("clave", "")),
                str(cuerpo.get("rol", "operador")), str(cuerpo.get("nombre", "")),
                debe_cambiar_clave=True)
            return self.responder({"ok": True, "usuario": usuario_json(u)})

        if ruta == "/api/usuario/rol":
            destino = int(cuerpo["usuario_id"])
            if destino == self.usuario.id:
                return self.error(
                    "No podés cambiarte el rol a vos mismo: pedíselo a otro "
                    "administrador. Así nadie se deja afuera por error.", 400)
            auth.cambiar_rol(con, destino, str(cuerpo["rol"]))
            return self.responder({"ok": True})

        if ruta == "/api/usuario/activar":
            destino = int(cuerpo["usuario_id"])
            activo = bool(cuerpo.get("activo"))
            if destino == self.usuario.id and not activo:
                return self.error("No podés darte de baja a vos mismo.", 400)
            if not activo and ultimo_admin(con, destino):
                return self.error(
                    "Es el único administrador activo. Nombrá otro antes de "
                    "darlo de baja, o el sistema queda sin quien lo configure.",
                    400)
            auth.activar_usuario(con, destino, activo)
            return self.responder({"ok": True})

        if ruta == "/api/usuario/blanquear-clave":
            auth.cambiar_clave(con, int(cuerpo["usuario_id"]),
                               str(cuerpo["clave_nueva"]))
            con.execute("UPDATE usuario SET debe_cambiar_clave = 1 WHERE id = ?",
                        (int(cuerpo["usuario_id"]),))
            con.commit()
            return self.responder({"ok": True})

        self.error("Ruta no encontrada", 404)


def explicar_integridad(e: sqlite3.IntegrityError) -> str:
    """Un dato que la base rechaza es un error del pedido, no una falla del
    servidor: conviene decir qué falta en lugar de 'Error interno'."""
    texto = str(e)
    if "FOREIGN KEY" in texto:
        return ("Alguno de los datos referenciados no existe todavía: "
                "revisá que el módulo, la obra social y el profesional estén "
                "cargados en Configuración.")
    if "UNIQUE" in texto:
        return "Ese registro ya existe."
    if "CHECK" in texto:
        return "Algún valor está fuera del rango permitido."
    return f"La base rechazó los datos: {texto}"


def usuario_json(u) -> dict:
    return {"id": u.id, "usuario": u.nombre_usuario, "nombre": u.nombre,
            "rol": u.rol, "activo": u.activo,
            "debe_cambiar_clave": u.debe_cambiar_clave,
            "ultimo_acceso": u.ultimo_acceso.isoformat() if u.ultimo_acceso else None}


def ultimo_admin(con, usuario_id: int) -> bool:
    """True si dar de baja a ese usuario deja al sistema sin administrador."""
    otros = [u for u in auth.listar_usuarios(con)
             if u.rol == "admin" and u.activo and u.id != usuario_id]
    return not otros


def resumen(ruta: str, cuerpo: dict) -> str:
    """Una linea para la auditoria. Nunca incluye claves."""
    partes = []
    for clave in ("paciente_id", "factura_id", "pago_id", "nro_factura", "anio",
                  "mes", "modulo", "valor_sesion", "monto", "fecha", "fecha_pago",
                  "usuario_id", "rol", "nombre", "usuario", "motivo", "activo"):
        if clave in cuerpo and cuerpo[clave] not in (None, ""):
            partes.append(f"{clave}={cuerpo[clave]}")
    return " ".join(partes)[:500]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="facturacion.db")
    ap.add_argument("--puerto", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 por defecto: no se expone a la red")
    ap.add_argument("--detras-de-proxy-https", action="store_true",
                    help="el servidor corre detras de nginx/Caddy con TLS; "
                         "marca la cookie como Secure y confia en "
                         "X-Forwarded-For")
    args = ap.parse_args()

    expuesto = args.host not in HOSTS_LOCALES

    # Escuchar fuera de la propia maquina sin TLS manda las claves en claro.
    # Preferible no arrancar antes que arrancar inseguro sin que se note.
    if expuesto and not args.detras_de_proxy_https:
        print(
            f"No arranco escuchando en {args.host} sin HTTPS: la clave de cada\n"
            f"usuario viajaria en claro por la red.\n\n"
            f"Para publicarlo, poné un proxy con certificado (nginx o Caddy)\n"
            f"delante y arrancá con:\n\n"
            f"    python3 servidor.py --host 127.0.0.1 --detras-de-proxy-https\n\n"
            f"El proxy atiende el 443 y reenvía al 127.0.0.1:{args.puerto}.\n"
            f"Hay un ejemplo de configuración en facturacion/README.md.",
            file=sys.stderr)
        raise SystemExit(2)

    con = db.conectar(args.base)        # crea el esquema si no existe
    sin_usuarios = not auth.hay_usuarios(con)
    con.close()

    servidor = ThreadingHTTPServer(
        (args.host, args.puerto),
        partial(Handler, base=args.base, seguro=args.detras_de_proxy_https))

    print(f"Sistema de facturación en http://{args.host}:{args.puerto}")
    print(f"Base: {args.base}")
    if args.detras_de_proxy_https:
        print("Modo detrás de proxy HTTPS: cookie Secure y HSTS activados.")
    if sin_usuarios:
        print("\nNo hay ningún usuario todavía. Creá el primero con:")
        print("    python3 usuarios.py crear --rol admin")
    print("\n(Ctrl+C para cortar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")


if __name__ == "__main__":
    main()
