"""Semaforo de cobranza y los cuadros del tablero. Etapa 4.

El color no se guarda en ningun campo: se calcula cada vez que se mira, con la
fecha en que se mira. Asi nunca queda desactualizado, que es el problema de
guardar un estado que depende del paso del tiempo.

Ver DISENO_LOGICA.md, regla R3 y pantallas P5 y P6.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from modelo import (
    EstadoCobranza,
    EstadoFactura,
    Factura,
    ObraSocial,
    Pago,
)

# Lo que la pantalla usa para pintar. Un solo lugar donde cambiarlo.
COLOR = {
    EstadoCobranza.COBRADO: "verde",
    EstadoCobranza.PARCIAL: "azul",
    EstadoCobranza.PENDIENTE: "amarillo",
    EstadoCobranza.ATRASADO: "rojo",
    EstadoCobranza.SIN_FACTURAR: "gris",
    EstadoCobranza.ANULADA: "gris",
}


# --------------------------------------------------------------------------
# Estado de una factura
# --------------------------------------------------------------------------

def cobrado(factura: Factura, pagos: Iterable[Pago]) -> float:
    """Suma de los pagos de la factura. Las reversiones son negativas, asi que
    entran en la misma suma sin ningun caso especial."""
    return round(sum(p.monto for p in pagos if p.factura_id == factura.id), 2)


def saldo(factura: Factura, pagos: Iterable[Pago]) -> float:
    return round(factura.monto - cobrado(factura, pagos), 2)


def dias_desde_emision(factura: Factura, hoy: date) -> Optional[int]:
    if factura.fecha_emision is None:
        return None
    return (hoy - factura.fecha_emision).days


def estado_cobranza(factura: Factura,
                    pagos: Iterable[Pago],
                    obra_social: ObraSocial,
                    hoy: date) -> EstadoCobranza:
    """El color de la fila. R3.

    El azul gana sobre el amarillo y el rojo a proposito: una factura cobrada a
    medias no es lo mismo que una que no pagaron nada, y conviene que se
    distinga de un vistazo aunque este vencida.
    """
    if factura.estado == EstadoFactura.ANULADA:
        return EstadoCobranza.ANULADA
    if factura.estado == EstadoFactura.BORRADOR or factura.fecha_emision is None:
        return EstadoCobranza.SIN_FACTURAR

    pagado = cobrado(factura, pagos)
    if pagado >= factura.monto:
        return EstadoCobranza.COBRADO
    if pagado > 0:
        return EstadoCobranza.PARCIAL

    dias = dias_desde_emision(factura, hoy)
    if dias is not None and dias >= obra_social.dias_vencimiento:
        return EstadoCobranza.ATRASADO
    return EstadoCobranza.PENDIENTE


@dataclass
class FilaCobranza:
    """Una factura lista para mostrar, con todo lo que la pantalla necesita."""
    factura: Factura
    estado: EstadoCobranza
    cobrado: float
    saldo: float
    dias: Optional[int]

    @property
    def color(self) -> str:
        return COLOR[self.estado]

    @property
    def leyenda(self) -> str:
        """El texto de la celda: monto y, si hace falta, por que ese color."""
        if self.estado == EstadoCobranza.PARCIAL:
            return f"{self.factura.monto:,.2f} · saldo {self.saldo:,.2f}"
        if self.estado == EstadoCobranza.ATRASADO:
            return f"{self.factura.monto:,.2f} · atrasado {self.dias} días"
        return f"{self.factura.monto:,.2f}"


def fila(factura: Factura, pagos: Iterable[Pago], obra_social: ObraSocial,
         hoy: date) -> FilaCobranza:
    return FilaCobranza(
        factura=factura,
        estado=estado_cobranza(factura, pagos, obra_social, hoy),
        cobrado=cobrado(factura, pagos),
        saldo=saldo(factura, pagos),
        dias=dias_desde_emision(factura, hoy),
    )


# --------------------------------------------------------------------------
# El cuadro de montos (lo que se facturo, se cobro, esta pendiente y atrasado)
# --------------------------------------------------------------------------

@dataclass
class ResumenCobranza:
    facturado: float = 0.0
    cobrado: float = 0.0
    parcial_pendiente: float = 0.0   # saldo de las que estan a medio cobrar
    pendiente: float = 0.0
    atrasado: float = 0.0
    cantidad_por_estado: dict = field(default_factory=dict)

    @property
    def por_cobrar(self) -> float:
        """Todo lo que todavia no entro, sin importar el color."""
        return round(self.parcial_pendiente + self.pendiente + self.atrasado, 2)


def resumen(facturas: Iterable[Factura], pagos: Iterable[Pago],
            obras_sociales: dict[int, ObraSocial], hoy: date) -> ResumenCobranza:
    """El cuadro de montos en pesos: facturado, cobrado, pendiente y atrasado."""
    r = ResumenCobranza()
    conteo: dict[EstadoCobranza, int] = {}

    for f in facturas:
        if not f.esta_vigente or f.estado == EstadoFactura.BORRADOR:
            continue
        os_ = obras_sociales[f.obra_social_id]
        est = estado_cobranza(f, pagos, os_, hoy)
        conteo[est] = conteo.get(est, 0) + 1

        r.facturado += f.monto
        r.cobrado += cobrado(f, pagos)
        if est == EstadoCobranza.PARCIAL:
            r.parcial_pendiente += saldo(f, pagos)
        elif est == EstadoCobranza.PENDIENTE:
            r.pendiente += f.monto
        elif est == EstadoCobranza.ATRASADO:
            r.atrasado += f.monto

    for campo in ("facturado", "cobrado", "parcial_pendiente", "pendiente", "atrasado"):
        setattr(r, campo, round(getattr(r, campo), 2))
    r.cantidad_por_estado = conteo
    return r


# --------------------------------------------------------------------------
# Tablero anual (P6): meses en las columnas, pacientes en las filas
# --------------------------------------------------------------------------

@dataclass
class TableroAnual:
    anio: int
    # celdas[paciente_id][mes] = FilaCobranza
    celdas: dict[int, dict[int, FilaCobranza]] = field(default_factory=dict)
    total_por_paciente: dict[int, float] = field(default_factory=dict)
    total_por_mes: dict[int, float] = field(default_factory=dict)
    total_por_profesional: dict[Optional[int], float] = field(default_factory=dict)

    @property
    def total_anual(self) -> float:
        return round(sum(self.total_por_mes.values()), 2)

    def celda(self, paciente_id: int, mes: int) -> Optional[FilaCobranza]:
        return self.celdas.get(paciente_id, {}).get(mes)

    def color(self, paciente_id: int, mes: int) -> str:
        c = self.celda(paciente_id, mes)
        return c.color if c else COLOR[EstadoCobranza.SIN_FACTURAR]


def tablero_anual(facturas: Iterable[Factura],
                  pagos: Iterable[Pago],
                  obras_sociales: dict[int, ObraSocial],
                  anio: int,
                  hoy: date) -> TableroAnual:
    """Arma el tablero del año. Las facturas anuladas no se muestran, pero la
    que las reemplaza si: por eso se filtra por vigencia y no por fecha."""
    t = TableroAnual(anio=anio)

    for f in facturas:
        if f.anio != anio or not f.esta_vigente:
            continue
        if f.estado == EstadoFactura.BORRADOR:
            continue
        os_ = obras_sociales[f.obra_social_id]
        t.celdas.setdefault(f.paciente_id, {})[f.mes] = fila(f, pagos, os_, hoy)

        t.total_por_paciente[f.paciente_id] = round(
            t.total_por_paciente.get(f.paciente_id, 0.0) + f.monto, 2)
        t.total_por_mes[f.mes] = round(t.total_por_mes.get(f.mes, 0.0) + f.monto, 2)
        t.total_por_profesional[f.profesional_id] = round(
            t.total_por_profesional.get(f.profesional_id, 0.0) + f.monto, 2)

    return t
