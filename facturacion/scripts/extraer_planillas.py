#!/usr/bin/env python3
"""Extrae y fusiona las dos planillas historicas en un unico modelo normalizado.

Uso:
    python3 extraer_planillas.py SESIONES.xlsx FACTURACION.xlsx --anio 2026 --salida ../datos

Produce en el directorio de salida:
    seed.json      modelo normalizado listo para cargar en la base
    informe.md     conciliacion: que se cruzo, que quedo sin cruzar y por que
"""

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl

MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO",
         "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
MES_NUM = {m: i + 1 for i, m in enumerate(MESES)}

# Prefijos que la planilla pega delante del CUIT para marcar condicion/modulo.
PREFIJOS_CUIT = {
    "RI": "RESPONSABLE_INSCRIPTO",
    "LC": "LIBRETA_CIVICA",
    "APE": "EXENTO",  # APE = modulo de apoyo, factura "A"; la condicion sale de la hoja CUIT
}

# Los cuatro modulos. El codigo se deduce de la obra social, salvo APE que viene
# como prefijo del CUIT. 'APE' hace de modulo general para el resto.
MODULOS = ["APE", "CAMI", "OSPEDYC", "OSPIA"]


def deducir_modulo(obra_social, prefijo=None):
    """Devuelve el codigo de modulo de una fila.

    CAMI tiene valor propio todo el año, siempre distinto del APE general.
    Dentro de CAMI, el prefijo APE marca los pacientes que van al modulo
    general en lugar del de CAMI.
    """
    os_norm = normalizar(obra_social)
    for codigo in ("OSPEDYC", "OSPIA"):
        if codigo in os_norm:
            return codigo
    if os_norm.startswith("CAMI") and "CAMIONEROS" not in os_norm:
        return "APE" if prefijo == "APE" else "CAMI"
    if os_norm.startswith("APROSS"):
        return None
    return "APE"


# --------------------------------------------------------------------------
# Normalizacion de nombres
# --------------------------------------------------------------------------

RUIDO = {
    "LIC", "LIC.", "SRA", "SR", "PACIENTE",
    # sufijos que describen la prestacion, no a la persona
    "FKT", "KINE", "PILETA", "ACUATICA", "NEURO", "INTENSIVO", "OCT",
}


def sin_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def normalizar(nombre):
    """'  Rinaldi Juan Carlos ' -> 'RINALDI JUAN CARLOS' (sin acentos ni ruido)."""
    if nombre is None:
        return ""
    t = sin_acentos(str(nombre)).upper()
    t = re.sub(r"[^A-Z0-9 ]", " ", t)
    palabras = [p for p in t.split() if p and p not in RUIDO]
    return " ".join(palabras)


def clave_tokens(nombre):
    """Clave insensible al orden: 'PABLOS MONICA' == 'MONICA PABLOS'."""
    return frozenset(normalizar(nombre).split())


def similitud(a, b):
    """0..1 combinando solapamiento de tokens y parecido literal."""
    ta, tb = clave_tokens(a), clave_tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    literal = SequenceMatcher(None, normalizar(a), normalizar(b)).ratio()
    return max(jaccard, 0.5 * jaccard + 0.5 * literal)


# --------------------------------------------------------------------------
# Lectura de celdas
# --------------------------------------------------------------------------

def texto(valor):
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


def numero(valor):
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace("$", "").replace(" ", "")
    if not s:
        return None
    # 1.234.567,89 -> 1234567.89
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def fecha_iso(valor):
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return None


def hojas_del_anio(libro, anio):
    """Devuelve [(nombre_hoja, mes_num)] de las hojas mensuales de ese anio."""
    encontradas = []
    for nombre in libro.sheetnames:
        u = normalizar(nombre)
        if str(anio) not in u:
            continue
        for mes in MESES:
            if u.startswith(mes):
                encontradas.append((nombre, MES_NUM[mes]))
                break
    encontradas.sort(key=lambda par: par[1])
    return encontradas


def partir_cuit(crudo):
    """'RI 30500053522' -> ('30500053522', 'RESPONSABLE_INSCRIPTO', 'RI')."""
    s = texto(crudo)
    if not s:
        return None, None, None
    partes = sin_acentos(s).upper().replace(".", "").split()
    prefijo = None
    digitos = []
    for parte in partes:
        if parte in PREFIJOS_CUIT:
            prefijo = parte
        elif parte.isdigit():
            digitos.append(parte)
    cuit = max(digitos, key=len) if digitos else None
    return cuit, PREFIJOS_CUIT.get(prefijo), prefijo


# --------------------------------------------------------------------------
# Planilla de SESIONES (lo que hay que facturar)
# --------------------------------------------------------------------------

def leer_sesiones(ruta, anio):
    """Hojas '<MES> <ANIO>': Paciente | CUIL | Obra Social | Lic. | SESIONES | Total."""
    valores = openpyxl.load_workbook(ruta, data_only=True)
    formulas = openpyxl.load_workbook(ruta, data_only=False)

    devengado = []
    valores_modulo = defaultdict(Counter)            # mes -> {valor_sesion: veces}
    valores_por_modulo = defaultdict(lambda: defaultdict(Counter))  # modulo -> mes -> {valor: veces}
    cuits_os = defaultdict(Counter)                  # obra_social -> {cuit: veces}

    for hoja, mes in hojas_del_anio(valores, anio):
        hv, hf = valores[hoja], formulas[hoja]
        for fila in range(2, hv.max_row + 1):
            paciente = texto(hv.cell(fila, 1).value)
            if not paciente or normalizar(paciente) in {"PACIENTE", "TOTAL"}:
                continue

            obra_social = texto(hv.cell(fila, 3).value)
            cuit, condicion, prefijo = partir_cuit(hv.cell(fila, 2).value)

            # El valor por sesion esta incrustado en la formula '=E7*16753.78'.
            formula = hf.cell(fila, 6).value
            valor_sesion = None
            if isinstance(formula, str) and formula.startswith("="):
                mult = re.findall(r"\*\s*([0-9]+(?:\.[0-9]+)?)", formula)
                if mult:
                    valor_sesion = float(mult[-1])
                    valores_modulo[mes][valor_sesion] += 1

            # Fila agregada tipo "APROSS 31 pacientes": es un convenio, no un paciente.
            agregado = bool(re.search(r"\d+\s*PACIENTES", normalizar(paciente)))

            if obra_social and cuit:
                cuits_os[normalizar(obra_social)][cuit] += 1

            modulo = deducir_modulo(obra_social, prefijo)
            if valor_sesion is not None:
                valores_por_modulo[modulo][mes][valor_sesion] += 1

            devengado.append({
                "hoja": hoja,
                "mes": mes,
                "paciente": paciente,
                "paciente_norm": normalizar(paciente),
                "obra_social": obra_social,
                "modulo": modulo,
                "profesional": texto(hv.cell(fila, 4).value),
                "cuit_obra_social": cuit,
                "condicion_iva": condicion,
                "prefijo": prefijo,
                "sesiones": numero(hv.cell(fila, 5).value),
                "valor_sesion": valor_sesion,
                "total_calculado": numero(hv.cell(fila, 6).value),
                "es_convenio_agregado": agregado,
            })

    return devengado, valores_modulo, valores_por_modulo, cuits_os


def valores_desde_facturas(facturas):
    """Deduce el valor por sesion de cada modulo dividiendo Monto / Cant. Sesiones.

    Esta es la fuente correcta: el Monto de la planilla de facturacion es lo que
    realmente se facturo. Las formulas de la planilla de sesiones quedaron
    clavadas en el valor de meses anteriores, asi que no sirven para esto.
    """
    deducidos = defaultdict(lambda: defaultdict(Counter))
    for f in facturas:
        modulo = deducir_modulo(f["obra_social"])
        if not modulo:
            continue
        ses, monto = f["sesiones"], f["monto"]
        if not ses or not monto:
            continue
        deducidos[modulo][f["mes"]][round(monto / ses, 2)] += 1
    return deducidos


def leer_hoja_simple(ruta, hoja, columnas):
    """Lee una hoja auxiliar plana (Hoja5 semanales, Hoja8 topes, CUIT, LISTADO)."""
    libro = openpyxl.load_workbook(ruta, data_only=True)
    if hoja not in libro.sheetnames:
        return []
    ws = libro[hoja]
    salida = []
    for fila in ws.iter_rows(min_row=2, values_only=True):
        if all(v is None for v in fila):
            continue
        registro = {}
        for nombre, idx in columnas.items():
            registro[nombre] = fila[idx] if idx < len(fila) else None
        if not texto(registro.get("paciente")):
            continue
        salida.append(registro)
    return salida


# --------------------------------------------------------------------------
# Planilla de FACTURACION (lo emitido y lo cobrado)
# --------------------------------------------------------------------------

ENCABEZADO_FACT = {"OBRA SOCIAL", "PROFESIONAL", "PACIENTE"}


def leer_facturacion(ruta, anio):
    """Hojas '<MES> <ANIO>' en bloques: 'PERIODO: ...', encabezado, filas, totales."""
    libro = openpyxl.load_workbook(ruta, data_only=True)
    facturas, cierres_apross = [], []

    for hoja, mes in hojas_del_anio(libro, anio):
        ws = libro[hoja]
        periodo_declarado = None

        for fila in ws.iter_rows(values_only=True):
            if all(v is None for v in fila):
                continue
            celdas = [texto(v) for v in fila]
            primera = normalizar(celdas[0]) if celdas[0] else ""

            if primera.startswith("PERIODO"):
                periodo_declarado = celdas[0]
                continue
            if primera in ENCABEZADO_FACT:
                continue

            # Cierre del convenio APROSS: TOTAL | monto | desc 4% | importe abonado
            texto_fila = " ".join(normalizar(c) for c in celdas if c)
            if "IMP ABONADO" in texto_fila or ("TOTAL" in texto_fila and "DESC 4" in texto_fila):
                montos = [numero(v) for v in fila]
                montos = [m for m in montos if m]
                cierres_apross.append({
                    "hoja": hoja, "mes": mes,
                    "montos_en_fila": montos,
                    "fila_cruda": [c for c in celdas if c],
                })
                continue

            paciente = celdas[2] if len(celdas) > 2 else None
            obra_social = celdas[0]
            if not paciente or not obra_social:
                continue

            nro = celdas[4] if len(celdas) > 4 else None
            emitida = bool(nro) and normalizar(nro) != "NO"

            facturas.append({
                "hoja": hoja,
                "mes": mes,
                "periodo_declarado": periodo_declarado,
                "obra_social": obra_social,
                "profesional": celdas[1] if len(celdas) > 1 else None,
                "paciente": paciente,
                "paciente_norm": normalizar(paciente),
                "sesiones": numero(fila[3]) if len(fila) > 3 else None,
                "nro_factura": nro,
                "emitida": emitida,
                "monto": numero(fila[5]) if len(fila) > 5 else None,
                "fecha_pago": fecha_iso(fila[6]) if len(fila) > 6 else None,
                "medio_pago": celdas[7] if len(celdas) > 7 else None,
            })

    return facturas, cierres_apross


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------

UMBRAL_AUTOMATICO = 0.80
UMBRAL_SUGERENCIA = 0.55


def fusionar(devengado, facturas):
    """Cruza devengado contra facturas por (mes, obra social, paciente parecido)."""
    indice = defaultdict(list)
    for i, f in enumerate(facturas):
        indice[(f["mes"], normalizar(f["obra_social"]))].append(i)

    usadas = set()
    cruces, dudosos, sin_factura = [], [], []

    for d in devengado:
        if d["es_convenio_agregado"]:
            continue

        candidatos = indice.get((d["mes"], normalizar(d["obra_social"])), [])
        # Si la obra social no coincide, probamos contra todo el mes.
        if not candidatos:
            candidatos = [i for i, f in enumerate(facturas) if f["mes"] == d["mes"]]

        mejor, mejor_puntaje = None, 0.0
        for i in candidatos:
            if i in usadas:
                continue
            p = similitud(d["paciente"], facturas[i]["paciente"])
            if p > mejor_puntaje:
                mejor, mejor_puntaje = i, p

        if mejor is not None and mejor_puntaje >= UMBRAL_AUTOMATICO:
            usadas.add(mejor)
            cruces.append({"devengado": d, "factura": facturas[mejor],
                           "puntaje": round(mejor_puntaje, 3)})
        elif mejor is not None and mejor_puntaje >= UMBRAL_SUGERENCIA:
            dudosos.append({"devengado": d, "sugerencia": facturas[mejor],
                            "puntaje": round(mejor_puntaje, 3)})
        else:
            sin_factura.append(d)

    sin_devengado = [f for i, f in enumerate(facturas) if i not in usadas]
    return cruces, dudosos, sin_factura, sin_devengado


def consolidar_pacientes(devengado, semanales, topes, condiciones):
    """Un registro por persona, uniendo las cuatro hojas por nombre normalizado."""
    pacientes = {}

    def buscar(coleccion, nombre, campo_nombre="paciente"):
        mejor, puntaje = None, 0.0
        for reg in coleccion:
            p = similitud(nombre, reg.get(campo_nombre))
            if p > puntaje:
                mejor, puntaje = reg, p
        return mejor if puntaje >= UMBRAL_AUTOMATICO else None

    for d in devengado:
        if d["es_convenio_agregado"]:
            continue
        clave = d["paciente_norm"]
        if clave not in pacientes:
            sem = buscar(semanales, d["paciente"])
            tope = buscar(topes, d["paciente"])
            cond = buscar(condiciones, d["paciente"])
            pacientes[clave] = {
                "nombre": d["paciente"],
                "nombre_norm": clave,
                "obra_social": d["obra_social"],
                "profesional": d["profesional"],
                "cuit_obra_social": d["cuit_obra_social"],
                "condicion_iva": d["condicion_iva"] or (
                    normalizar(cond.get("condicion")) if cond else None),
                "sesiones_semanales": numero(sem.get("sesiones_semanales")) if sem else None,
                "tope_sesiones_mes": numero(tope.get("sesiones")) if tope else None,
                "meses_con_devengado": [],
            }
        pacientes[clave]["meses_con_devengado"].append(d["mes"])

    for p in pacientes.values():
        p["meses_con_devengado"] = sorted(set(p["meses_con_devengado"]))
    return list(pacientes.values())


# --------------------------------------------------------------------------
# Informe
# --------------------------------------------------------------------------

def escribir_informe(ruta, anio, pacientes, valores_modulo, valores_por_modulo,
                     valores_facturados, cuits_os, cruces, dudosos, sin_factura,
                     sin_devengado, cierres_apross):
    L = [f"# Informe de conciliacion {anio}", ""]
    L.append(f"- Pacientes consolidados: **{len(pacientes)}**")
    L.append(f"- Cruces automaticos (devengado <-> factura): **{len(cruces)}**")
    L.append(f"- Cruces dudosos, requieren confirmacion manual: **{len(dudosos)}**")
    L.append(f"- Devengado sin factura: **{len(sin_factura)}**")
    L.append(f"- Facturas sin devengado: **{len(sin_devengado)}**")
    L.append(f"- Cierres de convenio APROSS detectados: **{len(cierres_apross)}**")
    L.append("")

    L += ["## Valores de sesion por mes (extraidos de las formulas)", "",
          "Cada mes usa dos o tres valores distintos: el modulo general y el de CAMI.",
          "Las variantes con centavos distintos son errores de tipeo arrastrados.", "",
          "| Mes | Valores encontrados (veces) |", "| --- | --- |"]
    for mes in sorted(valores_modulo):
        detalle = ", ".join(f"{v:,.2f} ({n})"
                            for v, n in valores_modulo[mes].most_common())
        L.append(f"| {MESES[mes - 1].title()} | {detalle} |")
    L.append("")

    L += ["## Valor por sesion de cada modulo", "",
          "Si un modulo tiene mas de un valor en el mismo mes, hay que decidir cual",
          "es el correcto antes de cargar la configuracion.", "",
          "| Modulo | " + " | ".join(MESES[m - 1].title()[:3]
                                     for m in sorted(valores_modulo)) + " |",
          "| --- |" + " --- |" * len(valores_modulo)]
    for codigo in MODULOS:
        if codigo not in valores_por_modulo:
            continue
        celdas = []
        for mes in sorted(valores_modulo):
            cnt = valores_por_modulo[codigo].get(mes)
            if not cnt:
                celdas.append("--")
            else:
                marca = " !" if len(cnt) > 1 else ""
                celdas.append(f"{cnt.most_common(1)[0][0]:,.2f}{marca}")
        L.append(f"| {codigo} | " + " | ".join(celdas) + " |")
    L += ["", "`!` = el modulo tiene mas de un valor ese mes.", ""]

    L += ["## Valor por sesion segun los montos facturados", "",
          "Deducido como Monto / Cant. Sesiones sobre la planilla de facturacion,",
          "que es lo que realmente se facturo. El valor mas frecuente de cada mes.", "",
          "| Modulo | " + " | ".join(MESES[m - 1].title()[:3]
                                     for m in sorted(valores_modulo)) + " |",
          "| --- |" + " --- |" * len(valores_modulo)]
    for codigo in MODULOS:
        if codigo not in valores_facturados:
            continue
        celdas = []
        for mes in sorted(valores_modulo):
            cnt = valores_facturados[codigo].get(mes)
            celdas.append(f"{cnt.most_common(1)[0][0]:,.2f}" if cnt else "--")
        L.append(f"| {codigo} | " + " | ".join(celdas) + " |")
    L.append("")

    # Desfasaje entre lo que la planilla de sesiones calcula y lo facturado.
    filas_desf = []
    for mes in sorted(valores_modulo):
        for codigo in MODULOS:
            formula = valores_por_modulo.get(codigo, {}).get(mes)
            facturado = valores_facturados.get(codigo, {}).get(mes)
            if not formula or not facturado:
                continue
            vf = formula.most_common(1)[0][0]
            vr = facturado.most_common(1)[0][0]
            if abs(vf - vr) > 1:
                filas_desf.append(
                    f"| {MESES[mes - 1].title()} | {codigo} | {vf:,.2f} "
                    f"| {vr:,.2f} | {vr - vf:+,.2f} |")
    if filas_desf:
        L += ["### Desfasaje entre las dos planillas", "",
              "Donde el valor incrustado en las formulas de la planilla de sesiones",
              "no coincide con el que se uso al facturar. Manda el facturado.", "",
              "| Mes | Modulo | En la formula | Facturado | Diferencia |",
              "| --- | --- | --- | --- | --- |"] + filas_desf + [""]

    L += ["## CUIT por obra social", "",
          "La columna rotulada *CUIL* contiene en realidad el CUIT de la obra social.", "",
          "| Obra social | CUIT (veces) |", "| --- | --- |"]
    for os_nombre in sorted(cuits_os):
        detalle = ", ".join(f"{c} ({n})" for c, n in cuits_os[os_nombre].most_common())
        L.append(f"| {os_nombre} | {detalle} |")
    L.append("")

    if dudosos:
        L += ["## Cruces a confirmar a mano", "",
              "| Mes | Devengado | Factura sugerida | Puntaje |", "| --- | --- | --- | --- |"]
        for d in sorted(dudosos, key=lambda x: -x["puntaje"])[:60]:
            L.append(f"| {d['devengado']['mes']} | {d['devengado']['paciente']} "
                     f"| {d['sugerencia']['paciente']} | {d['puntaje']} |")
        L.append("")

    if sin_factura:
        L += ["## Devengado sin factura", "",
              "| Mes | Paciente | Obra social | Sesiones |", "| --- | --- | --- | --- |"]
        for d in sin_factura[:60]:
            L.append(f"| {d['mes']} | {d['paciente']} | {d['obra_social']} "
                     f"| {d['sesiones'] or ''} |")
        L.append("")

    if sin_devengado:
        L += ["## Facturas sin devengado", "",
              "| Mes | Paciente | Obra social | Nro | Monto |",
              "| --- | --- | --- | --- | --- |"]
        for f in sin_devengado[:60]:
            L.append(f"| {f['mes']} | {f['paciente']} | {f['obra_social']} "
                     f"| {f['nro_factura'] or ''} | {f['monto'] or ''} |")
        L.append("")

    ruta.write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sesiones", help="planilla SESIONES_A_FACTURAR_DISCAPACIDAD")
    ap.add_argument("facturacion", help="planilla Facturacion_<anio>")
    ap.add_argument("--anio", type=int, default=date.today().year)
    ap.add_argument("--salida", default="../datos")
    args = ap.parse_args()

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)

    devengado, valores_modulo, valores_por_modulo, cuits_os = leer_sesiones(
        args.sesiones, args.anio)
    facturas, cierres_apross = leer_facturacion(args.facturacion, args.anio)

    semanales = leer_hoja_simple(args.sesiones, "Hoja5", {
        "paciente": 0, "obra_social": 1, "profesional": 2, "sesiones_semanales": 3})
    topes = leer_hoja_simple(args.sesiones, "Hoja8", {
        "paciente": 0, "obra_social": 1, "profesional": 2, "sesiones": 3})
    condiciones = leer_hoja_simple(args.sesiones, "CUIT", {
        "paciente": 0, "cuit": 1, "condicion": 2})

    valores_facturados = valores_desde_facturas(facturas)
    pacientes = consolidar_pacientes(devengado, semanales, topes, condiciones)
    cruces, dudosos, sin_factura, sin_devengado = fusionar(devengado, facturas)

    seed = {
        "anio": args.anio,
        "generado": datetime.now().isoformat(timespec="seconds"),
        "obras_sociales": [
            {"nombre": nombre,
             "cuit": cnt.most_common(1)[0][0],
             "cuits_alternativos": [c for c, _ in cnt.most_common()[1:]]}
            for nombre, cnt in sorted(cuits_os.items())
        ],
        "valores_modulo_por_mes": {
            str(mes): [{"valor": v, "filas": n} for v, n in cnt.most_common()]
            for mes, cnt in sorted(valores_modulo.items())
        },
        "modulos": [
            {"codigo": codigo,
             # Lo que dicen las formulas de la planilla de sesiones.
             "valores_por_mes": {
                 str(mes): [{"valor": v, "filas": n} for v, n in cnt.most_common()]
                 for mes, cnt in sorted(valores_por_modulo[codigo].items())},
             # Lo que realmente se facturo: esta es la serie a cargar.
             "valores_facturados_por_mes": {
                 str(mes): cnt.most_common(1)[0][0]
                 for mes, cnt in sorted(valores_facturados.get(codigo, {}).items())}}
            for codigo in MODULOS if codigo in valores_por_modulo
        ],
        "pacientes": pacientes,
        "devengado": devengado,
        "facturas": facturas,
        "cierres_apross": cierres_apross,
        "conciliacion": {
            "cruzados": [{"mes": c["devengado"]["mes"],
                          "paciente_sesiones": c["devengado"]["paciente"],
                          "paciente_factura": c["factura"]["paciente"],
                          "nro_factura": c["factura"]["nro_factura"],
                          "puntaje": c["puntaje"]} for c in cruces],
            "dudosos": [{"mes": d["devengado"]["mes"],
                         "paciente_sesiones": d["devengado"]["paciente"],
                         "paciente_factura": d["sugerencia"]["paciente"],
                         "puntaje": d["puntaje"]} for d in dudosos],
            "devengado_sin_factura": [
                {"mes": d["mes"], "paciente": d["paciente"],
                 "obra_social": d["obra_social"]} for d in sin_factura],
            "facturas_sin_devengado": [
                {"mes": f["mes"], "paciente": f["paciente"],
                 "obra_social": f["obra_social"],
                 "nro_factura": f["nro_factura"]} for f in sin_devengado],
        },
    }

    (salida / "seed.json").write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    escribir_informe(salida / "informe.md", args.anio, pacientes, valores_modulo,
                     valores_por_modulo, valores_facturados, cuits_os, cruces,
                     dudosos, sin_factura, sin_devengado, cierres_apross)

    print(f"pacientes={len(pacientes)} devengado={len(devengado)} "
          f"facturas={len(facturas)} cruces={len(cruces)} dudosos={len(dudosos)} "
          f"sin_factura={len(sin_factura)} sin_devengado={len(sin_devengado)}")
    print(f"-> {salida / 'seed.json'}")
    print(f"-> {salida / 'informe.md'}")


if __name__ == "__main__":
    main()
