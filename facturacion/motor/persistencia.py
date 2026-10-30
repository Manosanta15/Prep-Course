"""Guardado en SQLite. Sin dependencias: sqlite3 viene con Python.

Una decision que se aparta del diseño original, a proposito:

El documento dice guardar una fila por sesion (unas 19.000 al año). Aca se
guardan **las entradas** (autorizacion, dias, feriados, internaciones) y las
sesiones se generan cuando se piden. Son deterministicas, asi que el resultado
es el mismo, y se evita el problema de tener 19.000 filas que hay que
regenerar cada vez que se corrige un feriado o una internacion.

Lo unico que se guarda a nivel sesion son las **excepciones**: cuando alguien
marca a mano que una sesion puntual no se presto (una ausencia). Son pocas y no
se pueden calcular.

Las facturas y los pagos si se guardan siempre: son hechos, no calculos.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from modelo import (
    Autorizacion,
    AutorizacionDia,
    CondicionIVA,
    ConvenioApross,
    EstadoFactura,
    Factura,
    Feriado,
    Internacion,
    MedioPago,
    Modulo,
    MotivoNoFacturable,
    ObraSocial,
    Paciente,
    Pago,
    Profesional,
    TipoFeriado,
    ValorModulo,
)

ESQUEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS obra_social (
    id                INTEGER PRIMARY KEY,
    nombre            TEXT NOT NULL UNIQUE,
    cuit              TEXT,
    condicion_iva     TEXT NOT NULL DEFAULT 'EXENTO',
    tiene_tope        INTEGER NOT NULL DEFAULT 0,
    tope_sesiones_mes INTEGER,
    dias_vencimiento  INTEGER NOT NULL DEFAULT 60,
    activa            INTEGER NOT NULL DEFAULT 1,
    CHECK (tiene_tope = 0 OR tope_sesiones_mes IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS modulo (
    id     INTEGER PRIMARY KEY,
    codigo TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL DEFAULT '',
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS valor_modulo (
    id             INTEGER PRIMARY KEY,
    modulo_codigo  TEXT NOT NULL REFERENCES modulo(codigo),
    anio           INTEGER NOT NULL,
    mes            INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    valor_sesion   REAL NOT NULL CHECK (valor_sesion > 0),
    tope_sesiones  INTEGER,
    UNIQUE (modulo_codigo, anio, mes)
);

CREATE TABLE IF NOT EXISTS profesional (
    id        INTEGER PRIMARY KEY,
    nombre    TEXT NOT NULL UNIQUE,
    matricula TEXT,
    activo    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS feriado (
    fecha       TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL DEFAULT '',
    tipo        TEXT NOT NULL DEFAULT 'NACIONAL'
);

CREATE TABLE IF NOT EXISTS paciente (
    id               INTEGER PRIMARY KEY,
    apellido_nombre  TEXT NOT NULL,
    obra_social_id   INTEGER NOT NULL REFERENCES obra_social(id),
    modulo_codigo    TEXT NOT NULL REFERENCES modulo(codigo),
    profesional_id   INTEGER REFERENCES profesional(id),
    dni              TEXT,
    cuil             TEXT,
    nro_afiliado     TEXT,
    activo           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS paciente_alias (
    id          INTEGER PRIMARY KEY,
    paciente_id INTEGER NOT NULL REFERENCES paciente(id),
    alias       TEXT NOT NULL,
    UNIQUE (paciente_id, alias)
);

CREATE TABLE IF NOT EXISTS autorizacion (
    id                INTEGER PRIMARY KEY,
    paciente_id       INTEGER NOT NULL REFERENCES paciente(id),
    vigencia_desde    TEXT NOT NULL,
    vigencia_hasta    TEXT,
    tope_sesiones_mes INTEGER,
    observaciones     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS autorizacion_dia (
    id              INTEGER PRIMARY KEY,
    autorizacion_id INTEGER NOT NULL REFERENCES autorizacion(id) ON DELETE CASCADE,
    dia_semana      INTEGER NOT NULL CHECK (dia_semana BETWEEN 1 AND 5),
    hora            TEXT,
    UNIQUE (autorizacion_id, dia_semana)
);

CREATE TABLE IF NOT EXISTS internacion (
    id            INTEGER PRIMARY KEY,
    paciente_id   INTEGER NOT NULL REFERENCES paciente(id),
    desde         TEXT NOT NULL,
    hasta         TEXT,
    institucion   TEXT NOT NULL DEFAULT '',
    observaciones TEXT NOT NULL DEFAULT ''
);

-- Solo las sesiones que alguien marco a mano: no se pueden calcular.
CREATE TABLE IF NOT EXISTS sesion_excepcion (
    id          INTEGER PRIMARY KEY,
    paciente_id INTEGER NOT NULL REFERENCES paciente(id),
    fecha       TEXT NOT NULL,
    motivo      TEXT NOT NULL,
    detalle     TEXT NOT NULL DEFAULT '',
    UNIQUE (paciente_id, fecha)
);

CREATE TABLE IF NOT EXISTS factura (
    id                  INTEGER PRIMARY KEY,
    paciente_id         INTEGER NOT NULL REFERENCES paciente(id),
    obra_social_id      INTEGER NOT NULL REFERENCES obra_social(id),
    profesional_id      INTEGER REFERENCES profesional(id),
    anio                INTEGER NOT NULL,
    mes                 INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    cantidad_sesiones   INTEGER NOT NULL,
    valor_sesion        REAL NOT NULL,
    monto               REAL NOT NULL,
    nro_factura         TEXT,
    fecha_emision       TEXT,
    estado              TEXT NOT NULL DEFAULT 'BORRADOR',
    anula_a_factura_id  INTEGER REFERENCES factura(id),
    motivo_anulacion    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_factura_periodo ON factura (anio, mes);
CREATE INDEX IF NOT EXISTS ix_factura_paciente ON factura (paciente_id, anio);

CREATE TABLE IF NOT EXISTS pago (
    id                  INTEGER PRIMARY KEY,
    factura_id          INTEGER NOT NULL REFERENCES factura(id),
    fecha_pago          TEXT NOT NULL,
    monto               REAL NOT NULL,
    medio_pago          TEXT NOT NULL DEFAULT 'DEPOSITO',
    observaciones       TEXT NOT NULL DEFAULT '',
    revierte_a_pago_id  INTEGER REFERENCES pago(id)
);

CREATE INDEX IF NOT EXISTS ix_pago_factura ON pago (factura_id);

CREATE TABLE IF NOT EXISTS convenio_apross (
    id                   INTEGER PRIMARY KEY,
    anio                 INTEGER NOT NULL,
    mes                  INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    cantidad_pacientes   INTEGER NOT NULL DEFAULT 0,
    cantidad_sesiones    INTEGER NOT NULL DEFAULT 0,
    valor_sesion         REAL NOT NULL DEFAULT 0,
    porcentaje_descuento REAL NOT NULL DEFAULT 4.0,
    total_bruto_manual   REAL,
    fecha_pago           TEXT,
    medio_pago           TEXT,
    estado               TEXT NOT NULL DEFAULT 'BORRADOR',
    UNIQUE (anio, mes)
);
"""


# --------------------------------------------------------------------------
# Conexion
# --------------------------------------------------------------------------

def conectar(ruta: str = "facturacion.db") -> sqlite3.Connection:
    """Abre la base y crea el esquema si hace falta. ':memory:' para tests."""
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    con.commit()
    return con


def _f(texto: Optional[str]) -> Optional[date]:
    return date.fromisoformat(texto) if texto else None


def _t(fecha: Optional[date]) -> Optional[str]:
    return fecha.isoformat() if fecha else None


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

def guardar_obra_social(con, os_: ObraSocial) -> int:
    cur = con.execute(
        """INSERT INTO obra_social
             (id, nombre, cuit, condicion_iva, tiene_tope, tope_sesiones_mes,
              dias_vencimiento, activa)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             nombre=excluded.nombre, cuit=excluded.cuit,
             condicion_iva=excluded.condicion_iva,
             tiene_tope=excluded.tiene_tope,
             tope_sesiones_mes=excluded.tope_sesiones_mes,
             dias_vencimiento=excluded.dias_vencimiento, activa=excluded.activa""",
        (os_.id or None, os_.nombre, os_.cuit, os_.condicion_iva.value,
         int(os_.tiene_tope), os_.tope_sesiones_mes, os_.dias_vencimiento,
         int(os_.activa)))
    con.commit()
    return os_.id or cur.lastrowid


def obras_sociales(con) -> dict[int, ObraSocial]:
    return {r["id"]: ObraSocial(
        id=r["id"], nombre=r["nombre"], cuit=r["cuit"],
        condicion_iva=CondicionIVA(r["condicion_iva"]),
        tiene_tope=bool(r["tiene_tope"]),
        tope_sesiones_mes=r["tope_sesiones_mes"],
        dias_vencimiento=r["dias_vencimiento"], activa=bool(r["activa"]))
        for r in con.execute("SELECT * FROM obra_social ORDER BY nombre")}


def guardar_modulo(con, m: Modulo) -> None:
    con.execute("""INSERT INTO modulo (codigo, nombre, activo) VALUES (?, ?, ?)
                   ON CONFLICT(codigo) DO UPDATE SET
                     nombre=excluded.nombre, activo=excluded.activo""",
                (m.codigo, m.nombre, int(m.activo)))
    con.commit()


def modulos(con) -> list[Modulo]:
    return [Modulo(id=r["id"], codigo=r["codigo"], nombre=r["nombre"],
                   activo=bool(r["activo"]))
            for r in con.execute("SELECT * FROM modulo ORDER BY codigo")]


def guardar_valor_modulo(con, v: ValorModulo) -> None:
    con.execute(
        """INSERT INTO valor_modulo
             (modulo_codigo, anio, mes, valor_sesion, tope_sesiones)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(modulo_codigo, anio, mes) DO UPDATE SET
             valor_sesion=excluded.valor_sesion,
             tope_sesiones=excluded.tope_sesiones""",
        (v.modulo_codigo, v.anio, v.mes, v.valor_sesion, v.tope_sesiones))
    con.commit()


def valores_modulo(con, anio: Optional[int] = None) -> list[ValorModulo]:
    sql = "SELECT * FROM valor_modulo"
    args: tuple = ()
    if anio is not None:
        sql += " WHERE anio = ?"
        args = (anio,)
    return [ValorModulo(modulo_codigo=r["modulo_codigo"], anio=r["anio"],
                        mes=r["mes"], valor_sesion=r["valor_sesion"],
                        tope_sesiones=r["tope_sesiones"])
            for r in con.execute(sql + " ORDER BY modulo_codigo, anio, mes", args)]


def valor_de(con, modulo_codigo: str, anio: int, mes: int) -> Optional[ValorModulo]:
    r = con.execute(
        "SELECT * FROM valor_modulo WHERE modulo_codigo=? AND anio=? AND mes=?",
        (modulo_codigo, anio, mes)).fetchone()
    return ValorModulo(r["modulo_codigo"], r["anio"], r["mes"],
                       r["valor_sesion"], r["tope_sesiones"]) if r else None


def guardar_profesional(con, p: Profesional) -> int:
    cur = con.execute(
        """INSERT INTO profesional (id, nombre, matricula, activo)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET nombre=excluded.nombre,
             matricula=excluded.matricula, activo=excluded.activo""",
        (p.id or None, p.nombre, p.matricula, int(p.activo)))
    con.commit()
    return p.id or cur.lastrowid


def profesionales(con) -> dict[int, Profesional]:
    return {r["id"]: Profesional(id=r["id"], nombre=r["nombre"],
                                 matricula=r["matricula"], activo=bool(r["activo"]))
            for r in con.execute("SELECT * FROM profesional ORDER BY nombre")}


def guardar_feriados(con, lista: Iterable[Feriado]) -> None:
    con.executemany(
        """INSERT INTO feriado (fecha, descripcion, tipo) VALUES (?, ?, ?)
           ON CONFLICT(fecha) DO UPDATE SET
             descripcion=excluded.descripcion, tipo=excluded.tipo""",
        [(f.fecha.isoformat(), f.descripcion, f.tipo.value) for f in lista])
    con.commit()


def borrar_feriado(con, fecha: date) -> None:
    con.execute("DELETE FROM feriado WHERE fecha = ?", (fecha.isoformat(),))
    con.commit()


def feriados(con, anio: Optional[int] = None) -> list[Feriado]:
    sql, args = "SELECT * FROM feriado", ()
    if anio is not None:
        sql += " WHERE fecha LIKE ?"
        args = (f"{anio}-%",)
    return [Feriado(fecha=_f(r["fecha"]), descripcion=r["descripcion"],
                    tipo=TipoFeriado(r["tipo"]))
            for r in con.execute(sql + " ORDER BY fecha", args)]


# --------------------------------------------------------------------------
# Padron
# --------------------------------------------------------------------------

def guardar_paciente(con, p: Paciente) -> int:
    cur = con.execute(
        """INSERT INTO paciente
             (id, apellido_nombre, obra_social_id, modulo_codigo, profesional_id,
              dni, cuil, nro_afiliado, activo)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             apellido_nombre=excluded.apellido_nombre,
             obra_social_id=excluded.obra_social_id,
             modulo_codigo=excluded.modulo_codigo,
             profesional_id=excluded.profesional_id,
             dni=excluded.dni, cuil=excluded.cuil,
             nro_afiliado=excluded.nro_afiliado, activo=excluded.activo""",
        (p.id or None, p.apellido_nombre, p.obra_social_id, p.modulo_codigo,
         p.profesional_id, p.dni, p.cuil, p.nro_afiliado, int(p.activo)))
    con.commit()
    return p.id or cur.lastrowid


def pacientes(con, solo_activos: bool = True) -> list[Paciente]:
    sql = "SELECT * FROM paciente"
    if solo_activos:
        sql += " WHERE activo = 1"
    return [Paciente(
        id=r["id"], apellido_nombre=r["apellido_nombre"],
        obra_social_id=r["obra_social_id"], modulo_codigo=r["modulo_codigo"],
        profesional_id=r["profesional_id"], dni=r["dni"], cuil=r["cuil"],
        nro_afiliado=r["nro_afiliado"], activo=bool(r["activo"]))
        for r in con.execute(sql + " ORDER BY apellido_nombre")]


def paciente_por_id(con, paciente_id: int) -> Optional[Paciente]:
    return next((p for p in pacientes(con, solo_activos=False)
                 if p.id == paciente_id), None)


def baja_paciente(con, paciente_id: int) -> None:
    """Baja logica: el sistema no borra."""
    con.execute("UPDATE paciente SET activo = 0 WHERE id = ?", (paciente_id,))
    con.commit()


# --------------------------------------------------------------------------
# Autorizaciones e internaciones
# --------------------------------------------------------------------------

def guardar_autorizacion(con, a: Autorizacion) -> int:
    cur = con.execute(
        """INSERT INTO autorizacion
             (id, paciente_id, vigencia_desde, vigencia_hasta, tope_sesiones_mes,
              observaciones)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             vigencia_desde=excluded.vigencia_desde,
             vigencia_hasta=excluded.vigencia_hasta,
             tope_sesiones_mes=excluded.tope_sesiones_mes,
             observaciones=excluded.observaciones""",
        (a.id or None, a.paciente_id, _t(a.vigencia_desde), _t(a.vigencia_hasta),
         a.tope_sesiones_mes, a.observaciones))
    aid = a.id or cur.lastrowid
    con.execute("DELETE FROM autorizacion_dia WHERE autorizacion_id = ?", (aid,))
    con.executemany(
        "INSERT INTO autorizacion_dia (autorizacion_id, dia_semana, hora) "
        "VALUES (?, ?, ?)",
        [(aid, d.dia_semana, d.hora) for d in a.dias])
    con.commit()
    return aid


def autorizaciones(con, paciente_id: Optional[int] = None) -> list[Autorizacion]:
    sql, args = "SELECT * FROM autorizacion", ()
    if paciente_id is not None:
        sql += " WHERE paciente_id = ?"
        args = (paciente_id,)
    salida = []
    for r in con.execute(sql + " ORDER BY vigencia_desde", args):
        dias = [AutorizacionDia(dia_semana=d["dia_semana"], hora=d["hora"])
                for d in con.execute(
                    "SELECT * FROM autorizacion_dia WHERE autorizacion_id = ? "
                    "ORDER BY dia_semana", (r["id"],))]
        salida.append(Autorizacion(
            id=r["id"], paciente_id=r["paciente_id"],
            vigencia_desde=_f(r["vigencia_desde"]),
            vigencia_hasta=_f(r["vigencia_hasta"]),
            dias=dias, tope_sesiones_mes=r["tope_sesiones_mes"],
            observaciones=r["observaciones"]))
    return salida


def autorizacion_vigente(con, paciente_id: int,
                         al_dia: date) -> Optional[Autorizacion]:
    return next((a for a in autorizaciones(con, paciente_id) if a.cubre(al_dia)),
                None)


def guardar_internacion(con, i: Internacion) -> int:
    cur = con.execute(
        """INSERT INTO internacion
             (paciente_id, desde, hasta, institucion, observaciones)
           VALUES (?, ?, ?, ?, ?)""",
        (i.paciente_id, _t(i.desde), _t(i.hasta), i.institucion, i.observaciones))
    con.commit()
    return cur.lastrowid


def internaciones(con, paciente_id: Optional[int] = None) -> list[Internacion]:
    sql, args = "SELECT * FROM internacion", ()
    if paciente_id is not None:
        sql += " WHERE paciente_id = ?"
        args = (paciente_id,)
    return [Internacion(paciente_id=r["paciente_id"], desde=_f(r["desde"]),
                        hasta=_f(r["hasta"]), institucion=r["institucion"],
                        observaciones=r["observaciones"])
            for r in con.execute(sql + " ORDER BY desde", args)]


def guardar_excepcion(con, paciente_id: int, fecha: date,
                      motivo: MotivoNoFacturable, detalle: str = "") -> None:
    """Una sesion que alguien marco a mano como no prestada."""
    con.execute(
        """INSERT INTO sesion_excepcion (paciente_id, fecha, motivo, detalle)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(paciente_id, fecha) DO UPDATE SET
             motivo=excluded.motivo, detalle=excluded.detalle""",
        (paciente_id, fecha.isoformat(), motivo.value, detalle))
    con.commit()


def excepciones(con, paciente_id: Optional[int] = None) -> dict[tuple, tuple]:
    """{(paciente_id, fecha): (motivo, detalle)}"""
    sql, args = "SELECT * FROM sesion_excepcion", ()
    if paciente_id is not None:
        sql += " WHERE paciente_id = ?"
        args = (paciente_id,)
    return {(r["paciente_id"], _f(r["fecha"])):
            (MotivoNoFacturable(r["motivo"]), r["detalle"])
            for r in con.execute(sql, args)}


# --------------------------------------------------------------------------
# Facturas y pagos
# --------------------------------------------------------------------------

def guardar_factura(con, f: Factura) -> int:
    cur = con.execute(
        """INSERT INTO factura
             (id, paciente_id, obra_social_id, profesional_id, anio, mes,
              cantidad_sesiones, valor_sesion, monto, nro_factura, fecha_emision,
              estado, anula_a_factura_id, motivo_anulacion)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             cantidad_sesiones=excluded.cantidad_sesiones,
             valor_sesion=excluded.valor_sesion, monto=excluded.monto,
             nro_factura=excluded.nro_factura,
             fecha_emision=excluded.fecha_emision, estado=excluded.estado,
             anula_a_factura_id=excluded.anula_a_factura_id,
             motivo_anulacion=excluded.motivo_anulacion""",
        (f.id or None, f.paciente_id, f.obra_social_id, f.profesional_id,
         f.anio, f.mes, f.cantidad_sesiones, f.valor_sesion, f.monto,
         f.nro_factura, _t(f.fecha_emision), f.estado.value,
         f.anula_a_factura_id, f.motivo_anulacion))
    con.commit()
    if not f.id:
        f.id = cur.lastrowid
    return f.id


def facturas(con, anio: Optional[int] = None, mes: Optional[int] = None,
             paciente_id: Optional[int] = None) -> list[Factura]:
    sql, args = "SELECT * FROM factura WHERE 1=1", []
    if anio is not None:
        sql += " AND anio = ?"
        args.append(anio)
    if mes is not None:
        sql += " AND mes = ?"
        args.append(mes)
    if paciente_id is not None:
        sql += " AND paciente_id = ?"
        args.append(paciente_id)
    return [Factura(
        id=r["id"], paciente_id=r["paciente_id"],
        obra_social_id=r["obra_social_id"], profesional_id=r["profesional_id"],
        anio=r["anio"], mes=r["mes"], cantidad_sesiones=r["cantidad_sesiones"],
        valor_sesion=r["valor_sesion"], monto=r["monto"],
        nro_factura=r["nro_factura"], fecha_emision=_f(r["fecha_emision"]),
        estado=EstadoFactura(r["estado"]),
        anula_a_factura_id=r["anula_a_factura_id"],
        motivo_anulacion=r["motivo_anulacion"])
        for r in con.execute(sql + " ORDER BY anio, mes, id", args)]


def factura_por_id(con, factura_id: int) -> Optional[Factura]:
    return next((f for f in facturas(con) if f.id == factura_id), None)


def guardar_pago(con, p: Pago) -> int:
    cur = con.execute(
        """INSERT INTO pago
             (id, factura_id, fecha_pago, monto, medio_pago, observaciones,
              revierte_a_pago_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (p.id or None, p.factura_id, _t(p.fecha_pago), p.monto,
         p.medio_pago.value, p.observaciones, p.revierte_a_pago_id))
    con.commit()
    if not p.id:
        p.id = cur.lastrowid
    return p.id


def pagos(con, factura_id: Optional[int] = None) -> list[Pago]:
    sql, args = "SELECT * FROM pago", ()
    if factura_id is not None:
        sql += " WHERE factura_id = ?"
        args = (factura_id,)
    return [Pago(id=r["id"], factura_id=r["factura_id"],
                 fecha_pago=_f(r["fecha_pago"]), monto=r["monto"],
                 medio_pago=MedioPago(r["medio_pago"]),
                 observaciones=r["observaciones"],
                 revierte_a_pago_id=r["revierte_a_pago_id"])
            for r in con.execute(sql + " ORDER BY fecha_pago, id", args)]


def proximo_id_pago(con) -> int:
    r = con.execute("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM pago").fetchone()
    return r["n"]


# --------------------------------------------------------------------------
# APROSS
# --------------------------------------------------------------------------

def guardar_apross(con, c: ConvenioApross, estado: str = "BORRADOR") -> None:
    con.execute(
        """INSERT INTO convenio_apross
             (anio, mes, cantidad_pacientes, cantidad_sesiones, valor_sesion,
              porcentaje_descuento, total_bruto_manual, fecha_pago, medio_pago,
              estado)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(anio, mes) DO UPDATE SET
             cantidad_pacientes=excluded.cantidad_pacientes,
             cantidad_sesiones=excluded.cantidad_sesiones,
             valor_sesion=excluded.valor_sesion,
             porcentaje_descuento=excluded.porcentaje_descuento,
             total_bruto_manual=excluded.total_bruto_manual,
             fecha_pago=excluded.fecha_pago, medio_pago=excluded.medio_pago,
             estado=excluded.estado""",
        (c.anio, c.mes, c.cantidad_pacientes, c.cantidad_sesiones, c.valor_sesion,
         c.porcentaje_descuento, c.total_bruto_manual, _t(c.fecha_pago),
         c.medio_pago.value if c.medio_pago else None, estado))
    con.commit()


def apross(con, anio: int, mes: Optional[int] = None) -> list[tuple]:
    """Devuelve [(ConvenioApross, estado)]."""
    sql, args = "SELECT * FROM convenio_apross WHERE anio = ?", [anio]
    if mes is not None:
        sql += " AND mes = ?"
        args.append(mes)
    salida = []
    for r in con.execute(sql + " ORDER BY mes", args):
        salida.append((ConvenioApross(
            anio=r["anio"], mes=r["mes"],
            cantidad_pacientes=r["cantidad_pacientes"],
            cantidad_sesiones=r["cantidad_sesiones"],
            valor_sesion=r["valor_sesion"],
            porcentaje_descuento=r["porcentaje_descuento"],
            total_bruto_manual=r["total_bruto_manual"],
            fecha_pago=_f(r["fecha_pago"]),
            medio_pago=MedioPago(r["medio_pago"]) if r["medio_pago"] else None),
            r["estado"]))
    return salida
