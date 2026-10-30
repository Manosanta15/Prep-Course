"""Feriados argentinos: semilla para la pantalla de Configuracion.

El motor no depende de este archivo: recibe los feriados que le pasen. Esto es
una ayuda para no cargar 15 fechas a mano, y **hay que confirmarla** antes de
usarla, por dos razones:

1. Los feriados trasladables (Guemes, San Martin, Diversidad Cultural y
   Soberania Nacional) se mueven segun la ley 27.399 y el decreto del año. Aca
   van en su fecha nominal.
2. Los puentes turisticos los fija el Poder Ejecutivo año por año y no se
   pueden calcular.

Los de fecha fija y los que dependen de Pascua si son exactos.
"""

from datetime import date, timedelta

from modelo import Feriado, TipoFeriado

# Feriados nacionales de fecha fija: (mes, dia, descripcion)
FIJOS = [
    (1, 1, "Año Nuevo"),
    (3, 24, "Dia de la Memoria"),
    (4, 2, "Dia del Veterano y de los Caidos en Malvinas"),
    (5, 1, "Dia del Trabajador"),
    (5, 25, "Dia de la Revolucion de Mayo"),
    (6, 20, "Paso a la Inmortalidad del Gral. Belgrano"),
    (7, 9, "Dia de la Independencia"),
    (12, 8, "Inmaculada Concepcion de Maria"),
    (12, 25, "Navidad"),
]

# Trasladables, en su fecha nominal: confirmar contra el decreto del año.
TRASLADABLES = [
    (6, 17, "Paso a la Inmortalidad del Gral. Guemes"),
    (8, 17, "Paso a la Inmortalidad del Gral. San Martin"),
    (10, 12, "Dia del Respeto a la Diversidad Cultural"),
    (11, 20, "Dia de la Soberania Nacional"),
]


def pascua(anio: int) -> date:
    """Domingo de Pascua por el algoritmo gregoriano anonimo."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def feriados_nacionales(anio: int, incluir_trasladables: bool = True) -> list[Feriado]:
    """Devuelve los feriados calculables del año, ordenados por fecha."""
    dom_pascua = pascua(anio)
    lista = [Feriado(date(anio, m, d), txt) for m, d, txt in FIJOS]
    lista += [
        Feriado(dom_pascua - timedelta(days=48), "Carnaval (lunes)"),
        Feriado(dom_pascua - timedelta(days=47), "Carnaval (martes)"),
        Feriado(dom_pascua - timedelta(days=2), "Viernes Santo"),
    ]
    if incluir_trasladables:
        lista += [Feriado(date(anio, m, d), f"{txt} (trasladable: confirmar)")
                  for m, d, txt in TRASLADABLES]
    return sorted(lista, key=lambda f: f.fecha)


def solo_habiles(feriados: list[Feriado]) -> list[Feriado]:
    """Los que caen sabado o domingo no afectan a ninguna sesion."""
    return [f for f in feriados if f.fecha.weekday() < 5]


if __name__ == "__main__":
    import sys
    anio = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    todos = feriados_nacionales(anio)
    habiles = solo_habiles(todos)
    print(f"Feriados nacionales {anio}: {len(todos)}, "
          f"de los cuales {len(habiles)} caen de lunes a viernes\n")
    DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes",
            "sabado", "domingo"]
    for f in todos:
        marca = "" if f.fecha.weekday() < 5 else "   (fin de semana, no afecta)"
        print(f"  {f.fecha.isoformat()}  {DIAS[f.fecha.weekday()]:10} "
              f"{f.descripcion}{marca}")
