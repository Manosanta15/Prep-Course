"""Une el motor con la base. Es lo que consume la API web.

El motor no sabe nada de SQLite y la base no sabe nada de reglas de negocio;
este modulo es el unico que conoce a los dos.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

import cobranza
import confirmaciones
import persistencia as db
from facturacion import (
    OperacionNoPermitida,
    anular,
    emitir,
    preparar_factura,
    reemplazar,
    registrar_pago,
    revertir_pago,
)
from modelo import (
    EstadoCobranza,
    EstadoFactura,
    EstadoSesion,
    Feriado,
    MedioPago,
    Paciente,
)
from sesiones import ValorModuloFaltante, generar_sesiones

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


@dataclass
class FilaFacturacion:
    """Una fila de la pantalla de facturacion del mes (P3)."""
    paciente: Paciente
    obra_social: str
    profesional: Optional[str]
    modulo: str
    sesiones: int
    explicacion: str
    valor_sesion: Optional[float]
    monto: Optional[float]
    tope_aplicado: Optional[int]
    factura_id: Optional[int] = None
    nro_factura: Optional[str] = None
    estado: EstadoCobranza = EstadoCobranza.SIN_FACTURAR
    color: str = "gris"
    cobrado: float = 0.0
    saldo: float = 0.0
    dias: Optional[int] = None
    aviso: str = ""


def _sesiones_de(con, paciente: Paciente, anio: int, mes: int, oses, fers,
                 inters, excs, valor):
    aut = db.autorizacion_vigente(con, paciente.id, date(anio, mes, 1))
    if aut is None:
        return None, "Sin autorizacion vigente para ese mes"

    r = generar_sesiones(aut, anio, mes, oses[paciente.obra_social_id],
                         feriados=fers, internaciones=inters, valor_modulo=valor)

    # Las excepciones cargadas a mano se aplican sobre lo generado.
    for s in r.sesiones:
        clave = (paciente.id, s.fecha)
        if clave in excs and s.facturable:
            motivo, detalle = excs[clave]
            s.estado = EstadoSesion.NO_FACTURABLE
            s.motivo_no_facturable = motivo
            s.detalle_motivo = detalle
    return r, ""


def facturacion_del_mes(con, anio: int, mes: int,
                        hoy: Optional[date] = None) -> list[FilaFacturacion]:
    """Arma la pantalla de facturacion: una fila por paciente, todo precargado."""
    hoy = hoy or date.today()
    oses = db.obras_sociales(con)
    profs = db.profesionales(con)
    fers = db.feriados(con, anio)
    inters = db.internaciones(con)
    excs = db.excepciones(con)
    todas_facturas = db.facturas(con, anio=anio, mes=mes)
    todos_pagos = db.pagos(con)

    filas = []
    for p in db.pacientes(con):
        valor = db.valor_de(con, p.modulo_codigo, anio, mes)
        r, aviso = _sesiones_de(con, p, anio, mes, oses, fers, inters, excs, valor)

        vigentes = [f for f in todas_facturas
                    if f.paciente_id == p.id and f.esta_vigente]
        factura = vigentes[-1] if vigentes else None

        fila = FilaFacturacion(
            paciente=p,
            obra_social=oses[p.obra_social_id].nombre,
            profesional=profs[p.profesional_id].nombre if p.profesional_id else None,
            modulo=p.modulo_codigo,
            sesiones=r.cantidad if r else 0,
            explicacion=r.explicacion() if r else aviso,
            valor_sesion=valor.valor_sesion if valor else None,
            monto=round(r.cantidad * valor.valor_sesion, 2) if (r and valor) else None,
            tope_aplicado=r.tope_aplicado if r else None,
            aviso=aviso,
        )

        if valor is None:
            fila.aviso = (f"Falta el valor del modulo {p.modulo_codigo} para "
                          f"{mes:02d}/{anio}: cargalo en Configuracion")

        if factura:
            f_cob = cobranza.fila(factura, todos_pagos,
                                  oses[factura.obra_social_id], hoy)
            fila.factura_id = factura.id
            fila.nro_factura = factura.nro_factura
            fila.estado = f_cob.estado
            fila.color = f_cob.color
            fila.cobrado = f_cob.cobrado
            fila.saldo = f_cob.saldo
            fila.dias = f_cob.dias
            # Si lo generado ya no coincide con lo facturado, hay que avisarlo.
            if (factura.estado == EstadoFactura.EMITIDA and r
                    and factura.cantidad_sesiones != r.cantidad):
                fila.aviso = (f"La factura dice {factura.cantidad_sesiones} sesiones "
                              f"y hoy el calendario da {r.cantidad}. Para corregirla "
                              f"hay que anularla y re-emitirla.")
        filas.append(fila)

    return filas


def emitir_factura(con, paciente_id: int, anio: int, mes: int,
                   nro_factura: str, fecha_emision: date):
    """Genera, calcula y emite. Levanta si falta el valor del modulo."""
    p = db.paciente_por_id(con, paciente_id)
    if p is None:
        raise OperacionNoPermitida("El paciente no existe.")

    oses = db.obras_sociales(con)
    valor = db.valor_de(con, p.modulo_codigo, anio, mes)
    if valor is None:
        raise ValorModuloFaltante(
            f"Falta el valor del modulo {p.modulo_codigo} para {mes:02d}/{anio}. "
            f"Cargalo en Configuracion -> Valores por modulo.")

    r, aviso = _sesiones_de(con, p, anio, mes, oses, db.feriados(con, anio),
                            db.internaciones(con), db.excepciones(con), valor)
    if r is None:
        raise OperacionNoPermitida(aviso)

    ya = [f for f in db.facturas(con, anio=anio, mes=mes, paciente_id=paciente_id)
          if f.esta_vigente and f.estado == EstadoFactura.EMITIDA]
    if ya:
        raise OperacionNoPermitida(
            f"{p.apellido_nombre} ya tiene la factura {ya[-1].nro_factura} emitida "
            f"para {MESES[mes - 1]}. Para cambiarla hay que anularla.")

    f = preparar_factura(0, p, oses[p.obra_social_id], r, valor)
    emitir(f, nro_factura, fecha_emision)
    db.guardar_factura(con, f)
    return f


def anular_factura(con, factura_id: int, motivo: str, fecha: date,
                   reemplazar_tambien: bool = False):
    f = db.factura_por_id(con, factura_id)
    if f is None:
        raise OperacionNoPermitida("La factura no existe.")

    res = anular(f, db.pagos(con, factura_id), motivo, fecha,
                 proximo_id_pago=db.proximo_id_pago(con))
    db.guardar_factura(con, res.factura_anulada)
    for rev in res.reversiones:
        rev.id = None            # lo asigna la base
        db.guardar_pago(con, rev)

    nueva = None
    if reemplazar_tambien:
        nueva = reemplazar(0, res.factura_anulada)
        db.guardar_factura(con, nueva)
    return res, nueva


def registrar_cobro(con, factura_id: int, fecha_pago: date,
                    monto: Optional[float] = None,
                    medio_pago: str = "DEPOSITO", observaciones: str = "",
                    permitir_exceso: bool = False):
    f = db.factura_por_id(con, factura_id)
    if f is None:
        raise OperacionNoPermitida("La factura no existe.")
    p = registrar_pago(0, f, db.pagos(con, factura_id), fecha_pago, monto,
                       MedioPago(medio_pago), observaciones, permitir_exceso)
    p.id = None
    db.guardar_pago(con, p)
    return p


def revertir_cobro(con, pago_id: int, fecha: date, motivo: str):
    original = next((p for p in db.pagos(con) if p.id == pago_id), None)
    if original is None:
        raise OperacionNoPermitida("El pago no existe.")
    rev = revertir_pago(0, original, fecha, motivo)
    rev.id = None
    db.guardar_pago(con, rev)
    return rev


# --------------------------------------------------------------------------
# Vistas
# --------------------------------------------------------------------------

def tablero(con, anio: int, hoy: Optional[date] = None):
    hoy = hoy or date.today()
    return cobranza.tablero_anual(db.facturas(con, anio=anio), db.pagos(con),
                                  db.obras_sociales(con), anio, hoy)


def resumen_anual(con, anio: int, hoy: Optional[date] = None):
    hoy = hoy or date.today()
    return cobranza.resumen(db.facturas(con, anio=anio), db.pagos(con),
                            db.obras_sociales(con), hoy)


def ficha_paciente(con, paciente_id: int, anio: int,
                   hoy: Optional[date] = None) -> dict:
    """Lo que muestra P5: totales del año, transcurridas y el mes por mes."""
    hoy = hoy or date.today()
    p = db.paciente_por_id(con, paciente_id)
    if p is None:
        return {}

    oses = db.obras_sociales(con)
    profs = db.profesionales(con)
    fers = db.feriados(con, anio)
    inters = db.internaciones(con)
    excs = db.excepciones(con, paciente_id)
    todas = db.facturas(con, anio=anio, paciente_id=paciente_id)
    pgs = db.pagos(con)

    meses, total, corridas = [], 0, 0
    for mes in range(1, 13):
        valor = db.valor_de(con, p.modulo_codigo, anio, mes)
        r, _ = _sesiones_de(con, p, anio, mes, oses, fers, inters, excs, valor)
        if r is None:
            meses.append({"mes": mes, "sesiones": 0, "transcurridas": 0,
                          "monto": None, "estado": "SIN_AUTORIZACION",
                          "color": "gris", "fechas": []})
            continue

        total += r.cantidad
        tr = r.transcurridas(hoy)
        corridas += tr

        vigentes = [f for f in todas if f.mes == mes and f.esta_vigente
                    and f.estado == EstadoFactura.EMITIDA]
        if vigentes:
            fc = cobranza.fila(vigentes[-1], pgs, oses[p.obra_social_id], hoy)
            estado, color, monto = fc.estado.value, fc.color, vigentes[-1].monto
        else:
            estado, color = "SIN_FACTURAR", "gris"
            monto = round(r.cantidad * valor.valor_sesion, 2) if valor else None

        meses.append({
            "mes": mes, "sesiones": r.cantidad, "transcurridas": tr,
            "monto": monto, "estado": estado, "color": color,
            "explicacion": r.explicacion(),
            "fechas": [{"fecha": s.fecha.isoformat(), "hora": s.hora,
                        "facturable": s.facturable,
                        "motivo": s.motivo_no_facturable.value
                        if s.motivo_no_facturable else None}
                       for s in r.sesiones],
        })

    aut = db.autorizacion_vigente(con, paciente_id, hoy) or \
        (db.autorizaciones(con, paciente_id) or [None])[-1]
    nombres_dias = ["lunes", "martes", "miercoles", "jueves", "viernes"]

    return {
        "paciente": {
            "id": p.id, "nombre": p.apellido_nombre, "dni": p.dni,
            "cuil": p.cuil, "nro_afiliado": p.nro_afiliado,
            "datos_fiscales_completos": p.datos_fiscales_completos,
            "obra_social": oses[p.obra_social_id].nombre,
            "cuit_obra_social": oses[p.obra_social_id].cuit,
            "condicion_iva": oses[p.obra_social_id].condicion_iva.value,
            "modulo": p.modulo_codigo,
            "profesional": profs[p.profesional_id].nombre
            if p.profesional_id else None,
        },
        "autorizacion": {
            "vigencia_desde": aut.vigencia_desde.isoformat() if aut else None,
            "vigencia_hasta": aut.vigencia_hasta.isoformat()
            if aut and aut.vigencia_hasta else None,
            "dias": [{"dia": nombres_dias[d.dia_semana - 1], "hora": d.hora}
                     for d in aut.dias] if aut else [],
            "tope": aut.tope_sesiones_mes if aut else None,
        } if aut else None,
        "anio": anio,
        "sesiones_totales": total,
        "sesiones_transcurridas": corridas,
        "sesiones_por_transcurrir": total - corridas,
        "al_dia": hoy.isoformat(),
        "meses": meses,
    }


# --------------------------------------------------------------------------
# Confirmaciones: la API las pide antes de aplicar el cambio
# --------------------------------------------------------------------------

def confirmacion_valor_modulo(con, modulo_codigo: str, anio: int, mes: int,
                              valor_nuevo: float):
    actual = db.valor_de(con, modulo_codigo, anio, mes)
    return confirmaciones.cambiar_valor_modulo(actual, valor_nuevo,
                                               db.facturas(con, anio=anio, mes=mes))


def confirmacion_feriado(con, fecha: date, descripcion: str = ""):
    return confirmaciones.agregar_feriado(
        Feriado(fecha, descripcion), db.autorizaciones(con),
        db.facturas(con, anio=fecha.year, mes=fecha.month))


def confirmacion_anulacion(con, factura_id: int):
    f = db.factura_por_id(con, factura_id)
    if f is None:
        raise OperacionNoPermitida("La factura no existe.")
    p = db.paciente_por_id(con, f.paciente_id)
    return confirmaciones.anular_factura(
        f, db.pagos(con, factura_id),
        p.apellido_nombre if p else f"paciente {f.paciente_id}")


def confirmacion_baja_paciente(con, paciente_id: int):
    p = db.paciente_por_id(con, paciente_id)
    if p is None:
        raise OperacionNoPermitida("El paciente no existe.")
    return confirmaciones.baja_paciente(paciente_id, p.apellido_nombre,
                                        db.facturas(con))
