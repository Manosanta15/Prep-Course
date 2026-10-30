# Facturación de sesiones de kinesiología — diseño del sistema

Este directorio contiene el diseño de la lógica para reemplazar las dos planillas
históricas por un programa, y el script que las fusiona en un modelo normalizado.

- **[DISENO_LOGICA.md](DISENO_LOGICA.md)** — las tres maneras posibles de armar la
  lógica, la recomendada, el modelo de datos y todas las reglas de negocio.
- **[scripts/extraer_planillas.py](scripts/extraer_planillas.py)** — lee las dos
  planillas, las cruza y produce el modelo normalizado más un informe de conciliación.
- **datos/seed.json** — modelo normalizado (generado).
- **datos/informe.md** — qué se pudo cruzar y qué quedó pendiente de revisión manual
  (generado).

## Cómo correr la extracción

```bash
cd facturacion/scripts
python3 extraer_planillas.py SESIONES_A_FACTURAR_DISCAPACIDAD.xlsx Facturacion_2026.xlsx --anio 2026 --salida ../datos
```

Requiere `openpyxl`. No modifica las planillas: sólo lee.

## Qué hay hoy en las planillas

Las dos planillas describen el mismo negocio desde dos momentos distintos:

| | `SESIONES_A_FACTURAR_DISCAPACIDAD` | `Facturacion_2026` |
| --- | --- | --- |
| Responde | ¿qué **hay que** facturar este mes? | ¿qué **se facturó** y qué **se cobró**? |
| Hojas | 95, una por mes desde 2020 | 120, una por mes desde 2017 |
| Columnas | Paciente, CUIL, Obra Social, Lic. que lo factura, SESIONES, Total por paciente, Total mensual | Obra Social, Profesional, Paciente, Cant. Sesiones, Nº de Factura, Monto, Fecha de Pago, Medio de pago |
| Hojas auxiliares | `Hoja5` (sesiones semanales), `Hoja8` (topes), `CUIT` (condición IVA), `LISTADO`, `Hoja9`, `Hoja10` (consentimientos) | bloques `PERIODO:` por obra social y cierre de APROSS |

Resultado de la fusión del año 2026 (9 meses cargados):

```
pacientes=80  devengado=672  facturas=838  cruces=395  dudosos=164
sin_factura=104  sin_devengado=443
```

### Hallazgos que condicionan el diseño

1. **La columna "CUIL" no es el CUIL del paciente: es el CUIT de la obra social.**
   Se repite idéntico en todas las filas de la misma obra social
   (`30590354798` en las 176 filas de SANCOR). Confirma que CUIT y condición frente
   al IVA son atributos de la **obra social**, no del paciente.

2. **El valor por sesión está incrustado en cada fórmula de celda**
   (`=E2*16753.78`). No existe en ningún lado una tabla de módulos. Por eso cada mes
   arrastra entre dos y cuatro valores distintos y aparecen errores de tipeo que
   nadie puede detectar: `12370,61` vs `12370,16`, `7422,27` vs `7422,37`.

   Separados por módulo, los cuatro quedan así:

   | Módulo | Enero | Febrero a julio | Agosto y septiembre |
   | --- | --- | --- | --- |
   | APE (general) | 16.753,78 | 16.753,78 y 17.722,15 a la vez ⚠️ | 20.550,64 |
   | CAMI | 10.052,27 | 10.052,27 | 13.756,00 |
   | OSPEDYC | 16.753,78 | 17.722,15 | 18.081,68 |
   | OSPIA | 16.753,78 | 17.722,15 | 20.550,64 |

3. **La cantidad de sesiones del mes es calculable, no es un dato.** Verificado
   contra enero 2026: el mes tiene 22 días hábiles y el 1º (jueves) es feriado.

   | Días autorizados por semana | Sesiones posibles en enero 2026 | Lo que dice la planilla |
   | --- | --- | --- |
   | 5 | 21 | 21, en los 5 pacientes con 5 días autorizados |
   | 4 | 17 ó 18 | 17 |
   | 3 | 12, 13 ó 14 | 12 y 13 |
   | 2 | 8, 9 ó 10 | 8 y 9 |

   La fórmula es `ocurrencias de los días autorizados − feriados − días internado`,
   y después se recorta por el tope, cuando la obra social tiene uno.

   > Los nombres de pacientes de este cuadro y de todos los ejemplos del diseño están
   > reemplazados por nombres ficticios. Ver *Datos personales* al final.

4. **Los CUIT tienen duplicados por tipeo.** SANCOR aparece como `30590354798` (176
   filas) y `30590354799` (2). OSSEG como `...3522`, `...3523` y `...3524`. CAMI con
   dos CUIT distintos: se adopta `30709435538` (88 filas) sobre `30677896090` (9), y el
   campo queda editable.

   Por eso el modelo guarda **un** CUIT por obra social, editable, en lugar de
   repetirlo en cada fila: un CUIT mal tipeado se corrige una vez y no 176.

5. **APROSS no se factura por paciente.** Entra como una sola fila agregada
   (`APROSS 31 pacientes`, monto fijo `5.584.500`) y se cierra aparte con un
   descuento del 4%:

   ```
   TOTAL 6.031.260 → Desc. 4% 241.250,40 → Imp. Abonado 5.790.009,60
   ```

6. **Los encabezados `PERIODO:` están desfasados.** La hoja `ENERO 2026` de la
   planilla de facturación tiene bloques rotulados `PERIODO: AGOSTO 2021`. El mes real
   es el de la hoja, no el del rótulo. Las 443 facturas sin devengado se explican
   sobre todo por las filas de APROSS (unas 20 por mes, que no tienen contraparte
   individual) y por estos rótulos arrastrados.

7. **Los topes de sesiones no están en las planillas.** La `Hoja8` parece una tabla de
   topes, pero no lo es: no es `sesiones semanales × 4` (coincide en 28 pacientes y
   difiere en 22) y hay 106 casos en 2026 donde se facturó por encima de ese número
   —SANCOR lo supera 66 veces—. Es la foto de un mes cualquiera.

   Lo único aprovechable es que **11 obras sociales nunca lo superan** (CAMIONEROS,
   LUZ Y FUERZA, OSECAC, OSFATUN, OSPE, OSSACRA, OSSEG, SUME, UOCRA): son las
   candidatas a tener tope real, pero hay que confirmarlo con cada una.

8. **Los nombres no coinciden entre planillas.** El mismo paciente aparece con el
   apellido solo en una y con nombre completo en la otra, con la prestación pegada al
   nombre (`… ACUATICA` / `… pileta`), o con el nombre mal tipeado. El cruce automático
   resuelve 395 de 672 filas; 164 quedan como sugerencia a confirmar. Por eso el
   modelo necesita un **ID de paciente** y una tabla de alias, no el nombre como clave.

9. **El DNI y el CUIL de los pacientes no existen en ninguna de las dos planillas.**
   Los carga el usuario al dar de alta a cada paciente.

## Datos personales

Las planillas contienen nombres de pacientes con discapacidad, o sea datos de salud.
Por eso:

- **Los nombres de pacientes de estos documentos son ficticios.** Los montos, valores
  de módulo, CUIT de obras sociales y cantidades de sesiones son los reales, porque son
  los que hacen falta para validar la lógica y no identifican a nadie.
- **`datos/` está en `.gitignore`.** `seed.json` e `informe.md` se generan en la
  máquina de quien corre el script y no se versionan.
- **Las planillas `.xlsx` tampoco se versionan.**

Si el sistema se va a desplegar, conviene definir antes quién accede a qué: hoy toda
la información está en dos archivos que se mandan por mail.
