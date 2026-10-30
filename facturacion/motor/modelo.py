"""Entidades del sistema de facturacion.

Son dataclasses sin dependencias: la misma estructura sirve para la base de
datos, para los tests y para el motor de sesiones. El orden sigue al de
DISENO_LOGICA.md seccion 2.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------
# Enumeraciones
# --------------------------------------------------------------------------

class CondicionIVA(str, Enum):
    EXENTO = "EXENTO"
    RESPONSABLE_INSCRIPTO = "RESPONSABLE_INSCRIPTO"
    MONOTRIBUTO = "MONOTRIBUTO"


class EstadoSesion(str, Enum):
    PREVISTA = "PREVISTA"
    REALIZADA = "REALIZADA"
    NO_FACTURABLE = "NO_FACTURABLE"


class MotivoNoFacturable(str, Enum):
    FERIADO = "FERIADO"
    INTERNACION = "INTERNACION"
    AUSENCIA = "AUSENCIA"
    FUERA_DE_TOPE = "FUERA_DE_TOPE"
    FUERA_DE_VIGENCIA = "FUERA_DE_VIGENCIA"


class EstadoFactura(str, Enum):
    BORRADOR = "BORRADOR"
    EMITIDA = "EMITIDA"
    ANULADA = "ANULADA"


class EstadoCobranza(str, Enum):
    """Los colores del tablero. Ver DISENO_LOGICA.md R3."""
    SIN_FACTURAR = "SIN_FACTURAR"   # gris
    COBRADO = "COBRADO"             # verde
    PARCIAL = "PARCIAL"             # azul
    PENDIENTE = "PENDIENTE"         # amarillo
    ATRASADO = "ATRASADO"           # rojo
    ANULADA = "ANULADA"


class MedioPago(str, Enum):
    DEPOSITO = "DEPOSITO"
    TRANSFERENCIA = "TRANSFERENCIA"
    CHEQUE = "CHEQUE"
    EFECTIVO = "EFECTIVO"


class TipoFeriado(str, Enum):
    NACIONAL = "NACIONAL"
    PROVINCIAL = "PROVINCIAL"
    INSTITUCIONAL = "INSTITUCIONAL"


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

@dataclass
class ObraSocial:
    id: int
    nombre: str
    cuit: Optional[str] = None
    condicion_iva: CondicionIVA = CondicionIVA.EXENTO
    # tiene_tope en si/no explicito: un tope sin cargar no es un tope de cero.
    tiene_tope: bool = False
    tope_sesiones_mes: Optional[int] = None
    dias_vencimiento: int = 60
    activa: bool = True

    def __post_init__(self):
        if self.tiene_tope and self.tope_sesiones_mes is None:
            raise ValueError(
                f"{self.nombre}: tiene_tope es True pero no se cargo el tope")


@dataclass
class Modulo:
    id: int
    codigo: str          # APE | CAMI | OSPEDYC | OSPIA
    nombre: str = ""
    activo: bool = True


@dataclass
class ValorModulo:
    """Valor por sesion de un modulo en un mes. Es la tabla que hoy no existe:
    en la planilla el valor esta incrustado en cada formula de celda."""
    modulo_codigo: str
    anio: int
    mes: int
    valor_sesion: float
    # Si se carga, pisa el tope de la obra social para ese modulo y ese mes.
    tope_sesiones: Optional[int] = None


@dataclass
class Profesional:
    id: int
    nombre: str
    matricula: Optional[str] = None
    activo: bool = True


@dataclass
class Feriado:
    fecha: date
    descripcion: str = ""
    tipo: TipoFeriado = TipoFeriado.NACIONAL


# --------------------------------------------------------------------------
# Padron
# --------------------------------------------------------------------------

@dataclass
class Paciente:
    id: int
    apellido_nombre: str
    obra_social_id: int
    modulo_codigo: str
    profesional_id: Optional[int] = None
    # DNI y CUIL los carga el usuario: no estan en ninguna de las dos planillas.
    dni: Optional[str] = None
    cuil: Optional[str] = None
    nro_afiliado: Optional[str] = None
    activo: bool = True

    @property
    def datos_fiscales_completos(self) -> bool:
        """Para el aviso suave de la ficha: no bloquea la facturacion."""
        return bool(self.dni and self.cuil)


@dataclass
class PacienteAlias:
    paciente_id: int
    alias: str


# --------------------------------------------------------------------------
# Movimiento
# --------------------------------------------------------------------------

@dataclass
class AutorizacionDia:
    """Un dia autorizado con su horario propio: el lunes puede ser a las 15:00
    y el miercoles a las 16:30."""
    dia_semana: int      # 1 = lunes ... 5 = viernes
    hora: Optional[str] = None

    def __post_init__(self):
        if not 1 <= self.dia_semana <= 5:
            raise ValueError(
                f"dia_semana debe ir de 1 (lunes) a 5 (viernes), llego {self.dia_semana}")


@dataclass
class Autorizacion:
    id: int
    paciente_id: int
    vigencia_desde: date
    vigencia_hasta: Optional[date] = None
    dias: list[AutorizacionDia] = field(default_factory=list)
    tope_sesiones_mes: Optional[int] = None
    observaciones: str = ""

    def __post_init__(self):
        vistos = [d.dia_semana for d in self.dias]
        if len(vistos) != len(set(vistos)):
            raise ValueError("hay dias de la semana repetidos en la autorizacion")
        if self.vigencia_hasta and self.vigencia_hasta < self.vigencia_desde:
            raise ValueError("vigencia_hasta es anterior a vigencia_desde")

    def hora_de(self, dia_semana: int) -> Optional[str]:
        for d in self.dias:
            if d.dia_semana == dia_semana:
                return d.hora
        return None

    def cubre(self, dia: date) -> bool:
        if dia < self.vigencia_desde:
            return False
        return self.vigencia_hasta is None or dia <= self.vigencia_hasta


@dataclass
class Internacion:
    paciente_id: int
    desde: date
    hasta: Optional[date] = None      # None = sigue internado
    institucion: str = ""
    observaciones: str = ""

    def contiene(self, dia: date) -> bool:
        if dia < self.desde:
            return False
        return self.hasta is None or dia <= self.hasta


@dataclass
class Sesion:
    """Una fecha concreta. La genera el sistema, no se tipea."""
    paciente_id: int
    autorizacion_id: int
    fecha: date
    hora: Optional[str] = None
    estado: EstadoSesion = EstadoSesion.PREVISTA
    motivo_no_facturable: Optional[MotivoNoFacturable] = None
    detalle_motivo: str = ""

    @property
    def facturable(self) -> bool:
        return self.estado != EstadoSesion.NO_FACTURABLE


@dataclass
class Factura:
    """Hecho inmutable: no se edita, se anula y se emite otra."""
    id: int
    paciente_id: int
    obra_social_id: int
    anio: int
    mes: int
    cantidad_sesiones: int
    valor_sesion: float          # copiado al emitir, no se recalcula despues
    monto: float
    profesional_id: Optional[int] = None
    nro_factura: Optional[str] = None
    fecha_emision: Optional[date] = None
    estado: EstadoFactura = EstadoFactura.BORRADOR
    anula_a_factura_id: Optional[int] = None


@dataclass
class Pago:
    """Hecho inmutable. Una factura admite varios: la suma define el color."""
    factura_id: int
    fecha_pago: date
    monto: float
    medio_pago: MedioPago = MedioPago.DEPOSITO
    observaciones: str = ""


@dataclass
class ConvenioApross:
    """APROSS no factura por paciente: total mensual con descuento."""
    anio: int
    mes: int
    cantidad_pacientes: int
    cantidad_sesiones: int
    valor_sesion: float
    porcentaje_descuento: float = 4.0
    total_bruto_manual: Optional[float] = None
    fecha_pago: Optional[date] = None
    medio_pago: Optional[MedioPago] = None

    @property
    def total_bruto(self) -> float:
        if self.total_bruto_manual is not None:
            return round(self.total_bruto_manual, 2)
        return round(self.cantidad_sesiones * self.valor_sesion, 2)

    @property
    def monto_descuento(self) -> float:
        return round(self.total_bruto * self.porcentaje_descuento / 100, 2)

    @property
    def importe_abonado(self) -> float:
        return round(self.total_bruto - self.monto_descuento, 2)
