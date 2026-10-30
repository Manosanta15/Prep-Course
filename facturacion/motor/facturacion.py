"""Emision de facturas, anulacion y registro de pagos. Etapa 4.

Facturas y pagos son los unicos datos que salen de la organizacion hacia la
obra social, asi que son los unicos que no se editan: se anulan o se revierten,
y el registro anterior queda. Todo lo demas (autorizaciones, sesiones, feriados,
internaciones) se corrige con normalidad.

Ver DISENO_LOGICA.md seccion 5.
"""

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from cobranza import cobrado, saldo
from modelo import (
    EstadoFactura,
    Factura,
    MedioPago,
    ObraSocial,
    Paciente,
    Pago,
    ValorModulo,
)
from sesiones import ResultadoMes, calcular_monto


class OperacionNoPermitida(Exception):
    """Un cambio que el diseño no permite, con el camino correcto en el mensaje."""


# --------------------------------------------------------------------------
# Emision
# --------------------------------------------------------------------------

def preparar_factura(id_nueva: int,
                     paciente: Paciente,
                     obra_social: ObraSocial,
                     resultado: ResultadoMes,
                     valor: ValorModulo) -> Factura:
    """Arma el borrador a partir de las sesiones generadas.

    La cantidad y el valor se copian aca y no se vuelven a calcular: si manana
    cambia el valor del modulo o se carga un feriado nuevo, esta factura sigue
    diciendo lo que decia cuando se emitio.
    """
    if valor.anio != resultado.anio or valor.mes != resultado.mes:
        raise OperacionNoPermitida(
            f"El valor es de {valor.mes:02d}/{valor.anio} y las sesiones de "
            f"{resultado.mes:02d}/{resultado.anio}")

    return Factura(
        id=id_nueva,
        paciente_id=paciente.id,
        obra_social_id=obra_social.id,
        profesional_id=paciente.profesional_id,
        anio=resultado.anio,
        mes=resultado.mes,
        cantidad_sesiones=resultado.cantidad,
        valor_sesion=valor.valor_sesion,
        monto=calcular_monto(resultado.cantidad, valor),
        estado=EstadoFactura.BORRADOR,
    )


def emitir(factura: Factura, nro_factura: str, fecha_emision: date) -> Factura:
    """Pasa el borrador a emitida. Desde aca la factura ya no se edita."""
    if factura.estado == EstadoFactura.EMITIDA:
        raise OperacionNoPermitida(
            f"La factura {factura.nro_factura} ya esta emitida. Para corregirla "
            f"hay que anularla y emitir una nueva.")
    if factura.estado == EstadoFactura.ANULADA:
        raise OperacionNoPermitida(
            f"La factura {factura.nro_factura} esta anulada: no se puede reemitir.")
    if not str(nro_factura).strip():
        raise OperacionNoPermitida("Falta el numero de factura.")
    if factura.cantidad_sesiones <= 0:
        raise OperacionNoPermitida(
            "No hay sesiones facturables en el mes: no corresponde emitir.")

    factura.nro_factura = str(nro_factura).strip()
    factura.fecha_emision = fecha_emision
    factura.estado = EstadoFactura.EMITIDA
    return factura


# --------------------------------------------------------------------------
# Anulacion
# --------------------------------------------------------------------------

@dataclass
class ResultadoAnulacion:
    factura_anulada: Factura
    reversiones: list[Pago]


def anular(factura: Factura,
           pagos: Iterable[Pago],
           motivo: str,
           fecha: date,
           proximo_id_pago: int = 1) -> ResultadoAnulacion:
    """Anula una factura emitida y revierte los pagos que tenga.

    No borra nada: la factura queda con estado ANULADA y cada pago recibe su
    reversion. Es lo que avisa el mensaje de confirmacion de nivel 3.
    """
    if factura.estado != EstadoFactura.EMITIDA:
        raise OperacionNoPermitida(
            f"Solo se anulan facturas emitidas; esta esta en {factura.estado.value}.")
    if not motivo.strip():
        raise OperacionNoPermitida("La anulacion necesita un motivo.")

    reversiones = []
    propios = [p for p in pagos if p.factura_id == factura.id and not p.es_reversion]
    for p in propios:
        reversiones.append(Pago(
            id=proximo_id_pago + len(reversiones),
            factura_id=factura.id,
            fecha_pago=fecha,
            monto=-p.monto,
            medio_pago=p.medio_pago,
            observaciones=f"Reversion por anulacion de la factura {factura.nro_factura}",
            revierte_a_pago_id=p.id,
        ))

    factura.estado = EstadoFactura.ANULADA
    factura.motivo_anulacion = motivo.strip()
    return ResultadoAnulacion(factura_anulada=factura, reversiones=reversiones)


def reemplazar(id_nueva: int, anulada: Factura) -> Factura:
    """El borrador que reemplaza a una factura anulada, apuntando a ella."""
    if anulada.estado != EstadoFactura.ANULADA:
        raise OperacionNoPermitida(
            "Primero hay que anular la factura anterior.")
    return Factura(
        id=id_nueva,
        paciente_id=anulada.paciente_id,
        obra_social_id=anulada.obra_social_id,
        profesional_id=anulada.profesional_id,
        anio=anulada.anio,
        mes=anulada.mes,
        cantidad_sesiones=anulada.cantidad_sesiones,
        valor_sesion=anulada.valor_sesion,
        monto=anulada.monto,
        estado=EstadoFactura.BORRADOR,
        anula_a_factura_id=anulada.id,
    )


# --------------------------------------------------------------------------
# Pagos
# --------------------------------------------------------------------------

def registrar_pago(id_nuevo: int,
                   factura: Factura,
                   pagos_previos: Iterable[Pago],
                   fecha_pago: date,
                   monto: Optional[float] = None,
                   medio_pago: MedioPago = MedioPago.DEPOSITO,
                   observaciones: str = "",
                   permitir_exceso: bool = False) -> Pago:
    """Registra un pago. Si no se pasa monto, se toma el saldo pendiente.

    Admite varios pagos por factura: la factura queda azul hasta que la suma
    cubra el total.
    """
    if factura.estado != EstadoFactura.EMITIDA:
        raise OperacionNoPermitida(
            f"No se puede registrar un pago de una factura en "
            f"{factura.estado.value}.")

    pendiente = saldo(factura, pagos_previos)
    if monto is None:
        monto = pendiente
    if monto <= 0:
        raise OperacionNoPermitida("El monto del pago tiene que ser mayor a cero.")
    if monto > pendiente and not permitir_exceso:
        raise OperacionNoPermitida(
            f"El pago de {monto:,.2f} supera el saldo de {pendiente:,.2f}. "
            f"Si el excedente es correcto, confirmalo explicitamente.")

    return Pago(
        id=id_nuevo,
        factura_id=factura.id,
        fecha_pago=fecha_pago,
        monto=round(monto, 2),
        medio_pago=medio_pago,
        observaciones=observaciones,
    )


def revertir_pago(id_nuevo: int, pago: Pago, fecha: date,
                  motivo: str) -> Pago:
    """Deshace un pago con un contraasiento. El original queda intacto."""
    if pago.es_reversion:
        raise OperacionNoPermitida("No se revierte una reversion.")
    if pago.id is None:
        raise OperacionNoPermitida("El pago a revertir no tiene id asignado.")
    if not motivo.strip():
        raise OperacionNoPermitida("La reversion necesita un motivo.")

    return Pago(
        id=id_nuevo,
        factura_id=pago.factura_id,
        fecha_pago=fecha,
        monto=-pago.monto,
        medio_pago=pago.medio_pago,
        observaciones=motivo.strip(),
        revierte_a_pago_id=pago.id,
    )


def esta_cobrada(factura: Factura, pagos: Iterable[Pago]) -> bool:
    return cobrado(factura, pagos) >= factura.monto
