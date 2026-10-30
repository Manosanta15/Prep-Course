"""Los mensajes de confirmacion, calculados. Etapa 4.

El pedido fue que toda modificacion tenga confirmacion. Pero si todo confirma
igual, la gente aprende a apretar "Si" sin leer y la proteccion desaparece. Asi
que hay tres niveles segun lo que esta en juego, y los mensajes dicen **que va
a pasar**, con numeros, en lugar de "¿esta seguro?".

Este modulo calcula el impacto; la pantalla lo muestra.

Ver DISENO_LOGICA.md seccion 6.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable, Optional

from cobranza import cobrado
from modelo import (
    Autorizacion,
    EstadoFactura,
    Factura,
    Feriado,
    ObraSocial,
    Pago,
    ValorModulo,
)
from sesiones import dia_semana_1a5


def plural(n, singular, plural_):
    """'1 factura' / '3 facturas': los mensajes los lee una persona."""
    return f"{n} {singular if n == 1 else plural_}"


class Nivel(int, Enum):
    AVISAR_DESPUES = 1    # se guarda y aparece "Guardado. [Deshacer]"
    CONFIRMAR_ANTES = 2   # dialogo con el impacto en numeros
    ESCRIBIR_PARA_CONFIRMAR = 3   # hay que tipear algo que obligue a leer


@dataclass
class Confirmacion:
    nivel: Nivel
    titulo: str
    detalle: list[str] = field(default_factory=list)
    # Solo en nivel 3: lo que el usuario tiene que escribir.
    texto_a_escribir: Optional[str] = None

    @property
    def mensaje(self) -> str:
        return " ".join([self.titulo] + self.detalle)


# --------------------------------------------------------------------------
# Nivel 2: cambiar el valor de un modulo
# --------------------------------------------------------------------------

def cambiar_valor_modulo(valor_actual: Optional[ValorModulo],
                         valor_nuevo: float,
                         facturas: Iterable[Factura]) -> Confirmacion:
    """Cuantas facturas se recalculan y cuantas no se tocan.

    Las emitidas no se modifican nunca: copiaron su valor al emitirse. El
    mensaje lo dice en lugar de dejar que el usuario lo suponga.
    """
    if valor_actual is None:
        return Confirmacion(
            nivel=Nivel.CONFIRMAR_ANTES,
            titulo=f"Cargar el valor en {valor_nuevo:,.2f}.",
            detalle=["Es la primera carga para ese modulo y ese mes."])

    delmes = [f for f in facturas
              if f.anio == valor_actual.anio and f.mes == valor_actual.mes
              and f.esta_vigente]
    borradores = [f for f in delmes if f.estado == EstadoFactura.BORRADOR]
    emitidas = [f for f in delmes if f.estado == EstadoFactura.EMITIDA]

    sesiones = sum(f.cantidad_sesiones for f in borradores)
    antes = round(sum(f.monto for f in borradores), 2)
    despues = round(sesiones * valor_nuevo, 2)

    detalle = []
    if borradores:
        detalle.append(
            f"Se recalculan {plural(len(borradores), 'facturacion', 'facturaciones')} "
            f"sin emitir: {antes:,.2f} pasa a {despues:,.2f} "
            f"({despues - antes:+,.2f}).")
    else:
        detalle.append("No hay facturaciones sin emitir para recalcular.")
    if len(emitidas) == 1:
        detalle.append("La factura ya emitida de ese mes no se modifica.")
    elif emitidas:
        detalle.append(f"Las {len(emitidas)} facturas ya emitidas de ese mes no "
                       f"se modifican.")

    return Confirmacion(
        nivel=Nivel.CONFIRMAR_ANTES,
        titulo=(f"Cambiar el valor del modulo {valor_actual.modulo_codigo} de "
                f"{valor_actual.mes:02d}/{valor_actual.anio} de "
                f"{valor_actual.valor_sesion:,.2f} a {valor_nuevo:,.2f}."),
        detalle=detalle)


# --------------------------------------------------------------------------
# Nivel 2: cargar o quitar un feriado
# --------------------------------------------------------------------------

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def agregar_feriado(feriado: Feriado,
                    autorizaciones: Iterable[Autorizacion],
                    facturas: Iterable[Factura]) -> Confirmacion:
    """A cuantos pacientes les cae en dia autorizado, y que pasa con lo ya emitido."""
    dsem = dia_semana_1a5(feriado.fecha)
    if dsem is None:
        return Confirmacion(
            nivel=Nivel.AVISAR_DESPUES,
            titulo=(f"El {feriado.fecha.strftime('%d/%m/%Y')} cae "
                    f"{DIAS[feriado.fecha.weekday()]}: no afecta ninguna sesion."))

    alcanzados = [a for a in autorizaciones
                  if a.cubre(feriado.fecha)
                  and any(d.dia_semana == dsem for d in a.dias)]

    emitidas = [f for f in facturas
                if f.anio == feriado.fecha.year and f.mes == feriado.fecha.month
                and f.estado == EstadoFactura.EMITIDA]

    detalle = [
        f"Va a quitar "
        f"{plural(len(alcanzados), 'sesion prevista', 'sesiones previstas')} de "
        f"{feriado.fecha.strftime('%m/%Y')}."]
    if emitidas:
        detalle.append(
            f"Ese mes ya tiene "
            f"{plural(len(emitidas), 'factura emitida', 'facturas emitidas')} con "
            f"las sesiones anteriores. El feriado afecta solo las no facturadas; "
            f"para corregir las emitidas hay que anularlas y re-emitirlas.")

    return Confirmacion(
        nivel=Nivel.CONFIRMAR_ANTES,
        titulo=(f"El {feriado.fecha.strftime('%d/%m/%Y')} "
                f"({DIAS[feriado.fecha.weekday()]}) es dia autorizado de "
                f"{len(alcanzados)} pacientes."),
        detalle=detalle)


# --------------------------------------------------------------------------
# Nivel 3: anular una factura
# --------------------------------------------------------------------------

def anular_factura(factura: Factura,
                   pagos: Iterable[Pago],
                   nombre_paciente: str) -> Confirmacion:
    """Hay que escribir el numero de factura: obliga a leer cual se esta anulando."""
    pagado = cobrado(factura, pagos)
    detalle = []
    if pagado > 0:
        fechas = sorted(p.fecha_pago for p in pagos
                        if p.factura_id == factura.id and not p.es_reversion)
        cuando = fechas[-1].strftime("%d/%m/%Y") if fechas else "s/f"
        detalle.append(f"Ya figura cobrada {pagado:,.2f} el {cuando}. "
                       f"Esto tambien revierte el pago registrado.")

    return Confirmacion(
        nivel=Nivel.ESCRIBIR_PARA_CONFIRMAR,
        titulo=(f"Vas a anular la factura {factura.nro_factura} de "
                f"{nombre_paciente} por {factura.monto:,.2f}."),
        detalle=detalle,
        texto_a_escribir=str(factura.nro_factura))


# --------------------------------------------------------------------------
# Nivel 3: dar de baja un paciente con movimiento
# --------------------------------------------------------------------------

def baja_paciente(paciente_id: int,
                  nombre_paciente: str,
                  facturas: Iterable[Factura]) -> Confirmacion:
    """El sistema nunca borra en serio: baja logica, con el historial intacto."""
    propias = [f for f in facturas if f.paciente_id == paciente_id and f.esta_vigente]
    emitidas = [f for f in propias if f.estado == EstadoFactura.EMITIDA]

    if not propias:
        return Confirmacion(
            nivel=Nivel.CONFIRMAR_ANTES,
            titulo=f"Dar de baja a {nombre_paciente}.",
            detalle=["No tiene facturacion cargada."])

    total = round(sum(f.monto for f in emitidas), 2)
    return Confirmacion(
        nivel=Nivel.ESCRIBIR_PARA_CONFIRMAR,
        titulo=f"Vas a dar de baja a {nombre_paciente}.",
        detalle=[f"Tiene "
                 f"{plural(len(emitidas), 'factura emitida', 'facturas emitidas')} "
                 f"por {total:,.2f}. "
                 f"Deja de aparecer en la facturacion pero su historial queda "
                 f"completo: el sistema no borra."],
        texto_a_escribir=nombre_paciente)


# --------------------------------------------------------------------------
# Nivel 3: cerrar el convenio APROSS
# --------------------------------------------------------------------------

def cerrar_convenio_apross(anio: int, mes: int, total_bruto: float,
                           monto_descuento: float,
                           importe_abonado: float) -> Confirmacion:
    return Confirmacion(
        nivel=Nivel.ESCRIBIR_PARA_CONFIRMAR,
        titulo=f"Vas a cerrar el convenio APROSS de {mes:02d}/{anio}.",
        detalle=[f"Total {total_bruto:,.2f}, descuento {monto_descuento:,.2f}, "
                 f"importe abonado {importe_abonado:,.2f}. "
                 f"Una vez cerrado, para cambiarlo hay que reabrirlo."],
        texto_a_escribir=f"{mes:02d}/{anio}")


# --------------------------------------------------------------------------
# Nivel 1: lo que se guarda y avisa despues
# --------------------------------------------------------------------------

def cambio_menor(que_cambio: str) -> Confirmacion:
    """Para lo facil de deshacer: un telefono, un horario, una observacion."""
    return Confirmacion(nivel=Nivel.AVISAR_DESPUES,
                        titulo=f"{que_cambio} guardado.",
                        detalle=["[Deshacer]"])
