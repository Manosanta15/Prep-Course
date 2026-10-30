#!/usr/bin/env python3
"""Recorre un año completo de un paciente y muestra los cuadros de la etapa 4.

Usa los valores reales del modulo APE de 2026 y nombres ficticios. Sirve para
ver el semaforo funcionando sin tener la base cargada:

    python3 demo_tablero.py
"""

from datetime import date

import cobranza
import confirmaciones
from facturacion import anular, emitir, preparar_factura, registrar_pago
from feriados import feriados_nacionales, solo_habiles
from modelo import (
    Autorizacion,
    AutorizacionDia,
    MedioPago,
    ObraSocial,
    Paciente,
    ValorModulo,
)
from sesiones import generar_anio

HOY = date(2026, 10, 30)

# Valor del modulo APE mes a mes, deducido de Monto / Cant. Sesiones de la
# planilla de facturacion. Aumenta todos los meses.
APE_2026 = {1: 16753.78, 2: 17722.15, 3: 18236.09, 4: 18856.10, 5: 19346.37,
            6: 19752.65, 7: 20127.95, 8: 20550.64, 9: 20900.00}

SANCOR = ObraSocial(id=1, nombre="SANCOR", cuit="30590354798",
                    tiene_tope=False, dias_vencimiento=60)
OSES = {1: SANCOR}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep",
         "oct", "nov", "dic"]


def main():
    paciente = Paciente(id=1, apellido_nombre="Paciente ejemplo 1",
                        obra_social_id=1, modulo_codigo="APE", profesional_id=7,
                        dni="12345678", cuil="27123456784")

    autorizacion = Autorizacion(
        id=1, paciente_id=1,
        vigencia_desde=date(2026, 1, 1), vigencia_hasta=date(2026, 12, 31),
        dias=[AutorizacionDia(1, "15:00"), AutorizacionDia(2, "15:00"),
              AutorizacionDia(3, "16:30"), AutorizacionDia(4, "16:30"),
              AutorizacionDia(5, "16:30")])

    feriados = solo_habiles(feriados_nacionales(2026))
    valores = {m: ValorModulo("APE", 2026, m, v) for m, v in APE_2026.items()}
    anio = generar_anio(autorizacion, 2026, SANCOR, feriados=feriados,
                        valores=valores)

    print(f"{paciente.apellido_nombre} · {SANCOR.nombre} · modulo APE")
    print(f"Autorizacion: lunes a viernes · vigencia 2026 · "
          f"{len(feriados)} feriados cargados\n")

    print(f"{'Mes':5} {'Prev':>5} {'Fer':>4} {'A fact':>7} {'Valor':>11} "
          f"{'Monto':>14}  Explicacion")
    facturas, pagos = [], []
    for mes in range(1, 10):
        r = anio[mes]
        valor = valores[mes]
        f = preparar_factura(mes, paciente, SANCOR, r, valor)

        # Emitimos a los 10 dias del mes siguiente, como en la planilla.
        emision = date(2026, mes + 1, 10)
        emitir(f, str(660 + mes), emision)
        facturas.append(f)

        # Cobranza simulada: los primeros meses cobrados, uno parcial, los
        # ultimos sin cobrar para que se vea el amarillo y el rojo.
        if mes <= 5:
            pagos.append(registrar_pago(len(pagos) + 1, f, pagos,
                                        date(2026, mes + 2, 6),
                                        medio_pago=MedioPago.DEPOSITO))
        elif mes == 6:
            pagos.append(registrar_pago(len(pagos) + 1, f, pagos,
                                        date(2026, 8, 6), monto=200000))

        print(f"{MESES[mes - 1]:5} {len(r.sesiones):>5} "
              f"{len(r.descartadas):>4} {r.cantidad:>7} "
              f"{valor.valor_sesion:>11,.2f} {f.monto:>14,.2f}  "
              f"{r.explicacion()[:58]}")

    print("\n--- Tablero anual (P6)")
    t = cobranza.tablero_anual(facturas, pagos, OSES, 2026, HOY)
    print(f"{'':16}" + "".join(f"{MESES[m - 1]:>12}" for m in range(1, 10)))
    celdas = []
    for mes in range(1, 10):
        c = t.celda(1, mes)
        celdas.append(f"{c.color[:11]:>12}" if c else f"{'gris':>12}")
    print(f"{'color':16}" + "".join(celdas))
    montos = [f"{t.celda(1, m).factura.monto / 1000:>11,.0f}k" for m in range(1, 10)]
    print(f"{'monto':16}" + "".join(montos))
    print(f"\nTotal del paciente: {t.total_por_paciente[1]:,.2f}")
    print(f"Total anual:        {t.total_anual:,.2f}")

    print("\n--- Cuadro de montos (al 30/10/2026)")
    r = cobranza.resumen(facturas, pagos, OSES, HOY)
    print(f"  Facturado                      {r.facturado:>16,.2f}")
    print(f"  Cobrado          (verde)       {r.cobrado:>16,.2f}")
    print(f"  Saldo de parciales (azul)      {r.parcial_pendiente:>16,.2f}")
    print(f"  Pendiente        (amarillo)    {r.pendiente:>16,.2f}")
    print(f"  Atrasado +60 dias (rojo)       {r.atrasado:>16,.2f}")
    print(f"  {'-' * 48}")
    print(f"  Por cobrar                     {r.por_cobrar:>16,.2f}")
    print("\n  Facturas por color: " + ", ".join(
        f"{e.value.lower()}={n}" for e, n in r.cantidad_por_estado.items()))

    print("\n--- Sesiones transcurridas al 30/10/2026 (ficha del paciente, P5)")
    total = sum(anio[m].cantidad for m in range(1, 13))
    corridas = sum(anio[m].transcurridas(HOY) for m in range(1, 13))
    print(f"  {total} totales · {corridas} transcurridas · "
          f"{total - corridas} por transcurrir")

    print("\n--- Ejemplos de confirmacion (seccion 6)")
    c = confirmaciones.cambiar_valor_modulo(valores[9], 22000.0, facturas)
    print(f"  nivel {c.nivel}: {c.mensaje}")
    c = confirmaciones.anular_factura(facturas[0], pagos, paciente.apellido_nombre)
    print(f"  nivel {c.nivel}: {c.mensaje}")
    print(f"           hay que escribir: {c.texto_a_escribir!r}")


if __name__ == "__main__":
    main()
