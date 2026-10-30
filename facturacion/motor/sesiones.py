"""Motor de generacion de sesiones. Etapa 2 del plan.

La idea central: la cantidad de sesiones de un mes no es un dato que alguien
tipea, es un resultado que el sistema calcula y puede explicar fecha por fecha.

    ocurrencias de los dias autorizados
      - feriados
      - dias internado
      - lo que exceda el tope
    = sesiones facturables

Ver DISENO_LOGICA.md, reglas R1 (generacion), R2 (monto) y R6 (topes).
"""

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from modelo import (
    Autorizacion,
    EstadoSesion,
    Feriado,
    Internacion,
    MotivoNoFacturable,
    ObraSocial,
    Sesion,
    ValorModulo,
)


class ValorModuloFaltante(Exception):
    """Se levanta cuando no hay valor cargado para ese modulo y ese mes.

    Es deliberado que corte la facturacion en lugar de arrastrar el valor del
    mes anterior: arrastrar en silencio es lo que produjo los desfasajes de la
    planilla, donde el devengado quedo clavado en el valor de enero mientras
    las facturas ya iban con el valor actualizado.
    """


# --------------------------------------------------------------------------
# Calendario
# --------------------------------------------------------------------------

def dias_del_mes(anio: int, mes: int) -> list[date]:
    ultimo = calendar.monthrange(anio, mes)[1]
    return [date(anio, mes, d) for d in range(1, ultimo + 1)]


def dia_semana_1a5(dia: date) -> Optional[int]:
    """Lunes=1 ... viernes=5. Sabado y domingo devuelven None."""
    iso = dia.weekday()          # lunes=0 ... domingo=6
    return iso + 1 if iso < 5 else None


# --------------------------------------------------------------------------
# Tope efectivo (R6)
# --------------------------------------------------------------------------

def tope_efectivo(autorizacion: Autorizacion,
                  valor_modulo: Optional[ValorModulo],
                  obra_social: ObraSocial) -> Optional[int]:
    """Precedencia: autorizacion > modulo/mes > obra social > sin tope.

    Devuelve None cuando no hay tope, y entonces se factura el calendario
    completo. Los topes no se pueden deducir de las planillas (ver R6), asi
    que todos salen de la configuracion.
    """
    if autorizacion.tope_sesiones_mes is not None:
        return autorizacion.tope_sesiones_mes
    if valor_modulo is not None and valor_modulo.tope_sesiones is not None:
        return valor_modulo.tope_sesiones
    if obra_social.tiene_tope:
        return obra_social.tope_sesiones_mes
    return None


# --------------------------------------------------------------------------
# Generacion (R1)
# --------------------------------------------------------------------------

@dataclass
class ResultadoMes:
    """Lo que el sistema muestra antes de confirmar: el numero y su porque."""
    anio: int
    mes: int
    sesiones: list[Sesion]
    tope_aplicado: Optional[int] = None

    @property
    def facturables(self) -> list[Sesion]:
        return [s for s in self.sesiones if s.facturable]

    @property
    def cantidad(self) -> int:
        return len(self.facturables)

    @property
    def descartadas(self) -> list[Sesion]:
        return [s for s in self.sesiones if not s.facturable]

    def descartadas_por(self, motivo: MotivoNoFacturable) -> list[Sesion]:
        return [s for s in self.descartadas if s.motivo_no_facturable == motivo]

    def transcurridas(self, al_dia: date) -> int:
        """Sesiones facturables con fecha menor o igual a hoy. Se calcula al
        mirar, nunca queda vieja."""
        return sum(1 for s in self.facturables if s.fecha <= al_dia)

    def explicacion(self) -> str:
        """El texto de la ⓘ al lado de la cantidad, que responde '¿por que 21?'."""
        partes = [f"{self.cantidad} sesiones"]
        for motivo in MotivoNoFacturable:
            caidas = self.descartadas_por(motivo)
            if not caidas:
                continue
            fechas = ", ".join(s.fecha.strftime("%d/%m") for s in caidas)
            etiqueta = motivo.value.replace("_", " ").lower()
            partes.append(f"no se cuentan {len(caidas)} por {etiqueta}: {fechas}")
        return " · ".join(partes)


def generar_sesiones(autorizacion: Autorizacion,
                     anio: int,
                     mes: int,
                     obra_social: ObraSocial,
                     feriados: Iterable[Feriado] = (),
                     internaciones: Iterable[Internacion] = (),
                     valor_modulo: Optional[ValorModulo] = None) -> ResultadoMes:
    """Genera una fila por fecha del mes que caiga en un dia autorizado.

    Deterministica: los mismos datos dan siempre el mismo resultado. Y
    explicable: cada fecha descartada dice por que, asi que el sistema nunca
    muestra un numero pelado.
    """
    por_fecha = {f.fecha: f for f in feriados}
    propias = [i for i in internaciones if i.paciente_id == autorizacion.paciente_id]

    sesiones: list[Sesion] = []
    for dia in dias_del_mes(anio, mes):
        dsem = dia_semana_1a5(dia)
        if dsem is None:
            continue
        if not any(d.dia_semana == dsem for d in autorizacion.dias):
            continue

        sesion = Sesion(
            paciente_id=autorizacion.paciente_id,
            autorizacion_id=autorizacion.id,
            fecha=dia,
            hora=autorizacion.hora_de(dsem),
        )

        if not autorizacion.cubre(dia):
            sesion.estado = EstadoSesion.NO_FACTURABLE
            sesion.motivo_no_facturable = MotivoNoFacturable.FUERA_DE_VIGENCIA
        elif dia in por_fecha:
            sesion.estado = EstadoSesion.NO_FACTURABLE
            sesion.motivo_no_facturable = MotivoNoFacturable.FERIADO
            sesion.detalle_motivo = por_fecha[dia].descripcion
        else:
            internado = next((i for i in propias if i.contiene(dia)), None)
            if internado:
                sesion.estado = EstadoSesion.NO_FACTURABLE
                sesion.motivo_no_facturable = MotivoNoFacturable.INTERNACION
                sesion.detalle_motivo = internado.institucion

        sesiones.append(sesion)

    # El tope se aplica al final, sobre lo que quedo facturable, descartando
    # las ultimas del mes: las primeras ya se prestaron.
    tope = tope_efectivo(autorizacion, valor_modulo, obra_social)
    if tope is not None:
        facturables = [s for s in sesiones if s.facturable]
        for sobrante in facturables[tope:]:
            sobrante.estado = EstadoSesion.NO_FACTURABLE
            sobrante.motivo_no_facturable = MotivoNoFacturable.FUERA_DE_TOPE
            sobrante.detalle_motivo = f"tope {tope} del mes ya alcanzado"

    return ResultadoMes(anio=anio, mes=mes, sesiones=sesiones, tope_aplicado=tope)


def generar_anio(autorizacion: Autorizacion,
                 anio: int,
                 obra_social: ObraSocial,
                 feriados: Iterable[Feriado] = (),
                 internaciones: Iterable[Internacion] = (),
                 valores: Optional[dict[int, ValorModulo]] = None) -> dict[int, ResultadoMes]:
    """Los 12 meses de una vez: es la carga que se hace en enero."""
    valores = valores or {}
    return {
        mes: generar_sesiones(autorizacion, anio, mes, obra_social,
                              feriados, internaciones, valores.get(mes))
        for mes in range(1, 13)
    }


# --------------------------------------------------------------------------
# Monto (R2)
# --------------------------------------------------------------------------

def buscar_valor(valores: Iterable[ValorModulo], modulo_codigo: str,
                 anio: int, mes: int) -> ValorModulo:
    for v in valores:
        if v.modulo_codigo == modulo_codigo and v.anio == anio and v.mes == mes:
            return v
    raise ValorModuloFaltante(
        f"Falta el valor del modulo {modulo_codigo} para {mes:02d}/{anio}. "
        f"Cargalo en Configuracion -> Valores por modulo.")


def calcular_monto(cantidad: int, valor: ValorModulo) -> float:
    return round(cantidad * valor.valor_sesion, 2)
