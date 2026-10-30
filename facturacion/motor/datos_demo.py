#!/usr/bin/env python3
"""Carga datos de prueba para poner a punto el sistema.

    python3 datos_demo.py                      # crea facturacion.db
    python3 datos_demo.py --base prueba.db --anio 2026

Los **valores de modulo, CUIT y topes son los reales** de las planillas, porque
son los que hay que probar. Los **nombres de pacientes son ficticios**: las
planillas tienen datos de salud y no van a un repositorio.

Carga un año completo: configuracion, 10 pacientes con sus autorizaciones, los
feriados, una internacion, y facturas y pagos de los primeros meses para que el
semaforo tenga los cuatro colores desde el arranque.
"""

import argparse
from datetime import date

import persistencia as db
from feriados import feriados_nacionales, solo_habiles
from modelo import (
    Autorizacion,
    AutorizacionDia,
    CondicionIVA,
    ConvenioApross,
    Internacion,
    MedioPago,
    Modulo,
    MotivoNoFacturable,
    ObraSocial,
    Paciente,
    Profesional,
    ValorModulo,
)
from servicios import emitir_factura, registrar_cobro

# Valores reales 2026, deducidos de Monto / Cant. Sesiones de la planilla de
# facturacion. APE sube todos los meses; CAMI, por escalones.
VALORES = {
    "APE":     {1: 16753.78, 2: 17722.15, 3: 18236.09, 4: 18856.10, 5: 19346.37,
                6: 19752.65, 7: 20127.95, 8: 20550.64, 9: 20900.00},
    "CAMI":    {1: 10052.27, 2: 10052.27, 3: 10052.27, 4: 13500.00, 5: 13500.00,
                6: 13500.00, 7: 13756.00, 8: 13756.00, 9: 13756.00},
    "OSPEDYC": {1: 14740.98, 2: 15593.00, 3: 16045.20, 4: 16590.74, 5: 17022.10,
                6: 17379.56, 7: 17709.77, 8: 18081.68, 9: 18081.68},
    # OSPIA tiene un solo paciente y arranca en junio: la serie se completa a mano.
    "OSPIA":   {6: 19752.65, 7: 20127.95},
}

# CUIT reales (son datos de empresa, no de personas). El tope sale de la
# validacion del motor: las obras sociales que nunca superan su numero.
OBRAS_SOCIALES = [
    # nombre,     cuit,          condicion,  tiene_tope, tope, dias_venc
    ("SANCOR",    "30590354798", "EXENTO",   False, None, 60),
    ("CAMI",      "30709435538", "EXENTO",   False, None, 60),
    ("OSSACRA",   "30696375557", "EXENTO",   True,  20,   60),
    ("OSSEG",     "30500053522", "RESPONSABLE_INSCRIPTO", True, 20, 60),
    ("OSPE",      "30661876715", "RESPONSABLE_INSCRIPTO", True, 20, 60),
    ("UOM",       "30585207760", "EXENTO",   False, None, 60),
    ("OSPEDYC",   "30688339541", "EXENTO",   False, None, 90),
    ("OSPIA",     "30643997734", "EXENTO",   False, None, 60),
    ("PREVENCION", "30713045000", "RESPONSABLE_INSCRIPTO", False, None, 60),
    ("APROSS",    "30999999999", "EXENTO",   False, None, 60),
]

PROFESIONALES = ["Lic. Paste", "Lic. Borello", "Lic. Butignol", "Lic. Ampudia",
                 "Lic. Martin"]

# nombre ficticio, obra social, modulo, profesional, dias (1=lunes..5=viernes)
PACIENTES = [
    ("Paciente ejemplo 1",  "SANCOR",     "APE",     "Lic. Paste",    [1, 2, 3, 5]),
    ("Paciente ejemplo 2",  "SANCOR",     "APE",     "Lic. Paste",    [1, 2, 3, 4, 5]),
    ("Paciente ejemplo 3",  "SANCOR",     "APE",     "Lic. Martin",   [2, 4]),
    ("Paciente ejemplo 4",  "CAMI",       "CAMI",    "Lic. Butignol", [1, 3, 5]),
    ("Paciente ejemplo 5",  "CAMI",       "APE",     "Lic. Ampudia",  [2, 4]),
    ("Paciente ejemplo 6",  "OSSACRA",    "APE",     "Lic. Butignol", [1, 2, 3, 4, 5]),
    ("Paciente ejemplo 7",  "OSSEG",      "APE",     "Lic. Ampudia",  [1, 3]),
    ("Paciente ejemplo 8",  "OSPEDYC",    "OSPEDYC", "Lic. Ampudia",  [1, 2, 4, 5]),
    ("Paciente ejemplo 9",  "UOM",        "APE",     "Lic. Martin",   [3, 4, 5]),
    ("Paciente ejemplo 10", "PREVENCION", "APE",     "Lic. Butignol", [1, 2, 3, 4, 5]),
]

HORARIOS = {1: "15:00", 2: "15:00", 3: "16:30", 4: "16:30", 5: "09:00"}


def cargar(con, anio: int) -> None:
    for codigo in ("APE", "CAMI", "OSPEDYC", "OSPIA"):
        db.guardar_modulo(con, Modulo(id=0, codigo=codigo, nombre=codigo))

    for nombre, cuit, cond, tiene, tope, venc in OBRAS_SOCIALES:
        db.guardar_obra_social(con, ObraSocial(
            id=0, nombre=nombre, cuit=cuit, condicion_iva=CondicionIVA(cond),
            tiene_tope=tiene, tope_sesiones_mes=tope, dias_vencimiento=venc))

    for nombre in PROFESIONALES:
        db.guardar_profesional(con, Profesional(id=0, nombre=nombre))

    for codigo, meses in VALORES.items():
        for mes, valor in meses.items():
            db.guardar_valor_modulo(con, ValorModulo(codigo, anio, mes, valor))

    db.guardar_feriados(con, solo_habiles(feriados_nacionales(anio)))

    oses = {o.nombre: o.id for o in db.obras_sociales(con).values()}
    profs = {p.nombre: p.id for p in db.profesionales(con).values()}

    for nombre, os_nombre, modulo, prof, dias in PACIENTES:
        pid = db.guardar_paciente(con, Paciente(
            id=0, apellido_nombre=nombre, obra_social_id=oses[os_nombre],
            modulo_codigo=modulo, profesional_id=profs[prof]))
        db.guardar_autorizacion(con, Autorizacion(
            id=0, paciente_id=pid,
            vigencia_desde=date(anio, 1, 1), vigencia_hasta=date(anio, 12, 31),
            dias=[AutorizacionDia(d, HORARIOS[d]) for d in dias]))

    # Una internacion, para ver el motivo en el detalle de fechas.
    db.guardar_internacion(con, Internacion(
        paciente_id=3, desde=date(anio, 4, 6), hasta=date(anio, 4, 17),
        institucion="Sanatorio (ejemplo)"))

    # Una ausencia puntual cargada a mano.
    db.guardar_excepcion(con, 1, date(anio, 5, 12),
                         MotivoNoFacturable.AUSENCIA, "no concurrio")

    # El convenio APROSS de enero, con los numeros reales de la planilla.
    db.guardar_apross(con, ConvenioApross(
        anio=anio, mes=1, cantidad_pacientes=31, cantidad_sesiones=0,
        valor_sesion=0, total_bruto_manual=6031260.0,
        fecha_pago=date(anio, 3, 20), medio_pago=MedioPago.DEPOSITO), "COBRADO")


def facturar_meses(con, anio: int) -> None:
    """Emite enero a agosto y cobra parte, para que el semaforo tenga colores."""
    nro = 660
    for mes in range(1, 9):
        for p in db.pacientes(con):
            emision = date(anio, mes + 1, 10) if mes < 12 else date(anio, 12, 28)
            try:
                nro += 1
                f = emitir_factura(con, p.id, anio, mes, str(nro), emision)
            except Exception:
                nro -= 1
                continue

            # Enero a mayo cobrados; junio uno parcial; julio y agosto sin cobrar.
            if mes <= 5:
                registrar_cobro(con, f.id, date(anio, mes + 2, 6))
            elif mes == 6 and p.id % 3 == 0:
                registrar_cobro(con, f.id, date(anio, 8, 6),
                                monto=round(f.monto * 0.4, 2),
                                observaciones="pago parcial")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="facturacion.db")
    ap.add_argument("--anio", type=int, default=2026)
    ap.add_argument("--solo-configuracion", action="store_true",
                    help="no emite facturas ni pagos")
    args = ap.parse_args()

    con = db.conectar(args.base)
    cargar(con, args.anio)
    if not args.solo_configuracion:
        facturar_meses(con, args.anio)

    print(f"Base lista: {args.base}")
    print(f"  obras sociales {len(db.obras_sociales(con))}, "
          f"modulos {len(db.modulos(con))}, "
          f"profesionales {len(db.profesionales(con))}")
    print(f"  pacientes {len(db.pacientes(con))}, "
          f"feriados {len(db.feriados(con, args.anio))}, "
          f"facturas {len(db.facturas(con, anio=args.anio))}, "
          f"pagos {len(db.pagos(con))}")
    print(f"\nArranca el servidor con:  python3 servidor.py --base {args.base}")
    con.close()


if __name__ == "__main__":
    main()
