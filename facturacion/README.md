# Facturación de sesiones de kinesiología

Reemplazo de las dos planillas históricas por un sistema. Corre con Python solo,
**sin instalar nada**, y está armado para ser después una parte de un programa de
gestión administrativa web: el motor no sabe de pantallas, la base no sabe de reglas,
y la interfaz es un cliente de la misma API que va a consumir el sistema grande.

## Probarlo ahora

```bash
cd facturacion/motor
python3 datos_demo.py
python3 servidor.py
```

Y abrir http://localhost:8080. Los datos de prueba traen un año completo ya facturado
para que el semáforo tenga los cuatro colores desde el arranque.

> El control **"Ver al día"** de la barra superior es el que más sirve para la puesta a
> punto: el semáforo depende de la fecha, así que poniendo una fecha futura se ve cómo
> va a quedar el tablero en diciembre sin esperar a diciembre.

Los valores de módulo, CUIT y topes de los datos de prueba son los reales de las
planillas; los nombres de pacientes son ficticios.

## Las piezas

| | |
| --- | --- |
| `motor/modelo.py` | Las entidades, sin dependencias |
| `motor/sesiones.py` | Genera las sesiones del mes y las explica fecha por fecha |
| `motor/cobranza.py` | Semáforo, cuadro de montos y tablero anual |
| `motor/facturacion.py` | Emitir, anular, pagar, revertir |
| `motor/confirmaciones.py` | Los mensajes de confirmación, con el impacto calculado |
| `motor/feriados.py` | Feriados nacionales, con Pascua calculada |
| `motor/persistencia.py` | SQLite |
| `motor/servicios.py` | Une el motor con la base; es lo que consume la API |
| `motor/servidor.py` | API HTTP + sirve la página |
| `web/index.html` | La interfaz. Sin build, sin framework |

Las capas no se saltean: la página no tiene ninguna regla de negocio, pide todo a la
API; `servicios.py` es el único módulo que conoce a la vez el motor y la base. Por eso
el día que esto entre en el programa de gestión se cambia la capa de arriba y el motor
queda igual.

- **[DISENO_LOGICA.md](DISENO_LOGICA.md)** — las tres maneras posibles de armar la
  lógica, la recomendada, el modelo de datos y todas las reglas de negocio.
- **[motor/](motor/)** — etapas 2, 3 y 4 implementadas: el motor que genera las
  sesiones a partir de los días autorizados descontando feriados e internación, y las
  facturas, pagos y semáforo de cobranza. 79 tests.
- **[scripts/extraer_planillas.py](scripts/extraer_planillas.py)** — lee las dos
  planillas, las cruza y produce el modelo normalizado más un informe de conciliación.
- **datos/** — `seed.json` e `informe.md`, generados. No se versionan.

## Cómo correr

Extracción y conciliación de las dos planillas:

```bash
cd facturacion/scripts
python3 extraer_planillas.py SESIONES_A_FACTURAR_DISCAPACIDAD.xlsx Facturacion_2026.xlsx --anio 2026 --salida ../datos
```

Tests (sólo biblioteca estándar, 107 casos):

```bash
cd facturacion/motor
python3 -m unittest test_sesiones test_cobranza test_persistencia
```

Un año completo corrible, con los cuadros de cobranza:

```bash
cd facturacion/motor
python3 demo_tablero.py
```

Validación del motor contra lo que la planilla factura de verdad:

```bash
cd facturacion/motor
python3 validar_contra_planilla.py SESIONES_A_FACTURAR_DISCAPACIDAD.xlsx --anio 2026
```

Los feriados del año, con Pascua calculada:

```bash
cd facturacion/motor
python3 feriados.py 2026
```

`openpyxl` hace falta sólo para los dos scripts que leen planillas. Ninguno las
modifica: sólo leen.

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

2. **El valor por sesión está incrustado en cada fórmula de celda**, y quedó viejo.
   (`=E2*16753.78`). No existe en ningún lado una tabla de módulos. Por eso cada mes
   arrastra entre dos y cuatro valores distintos y aparecen errores de tipeo que
   nadie puede detectar: `12370,61` vs `12370,16`, `7422,27` vs `7422,37`.

   El valor correcto no sale de las fórmulas sino del campo **Monto** de la planilla
   de facturación, dividido por la cantidad de sesiones. Así quedan los cuatro módulos:

   | Módulo | Ene | Feb | Mar | Abr | May | Jun | Jul | Ago | Sep |
   | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
   | APE | 16.753,78 | 17.722,15 | 18.236,09 | 18.856,10 | 19.346,37 | 19.752,65 | 20.127,95 | 20.550,64 | 20.900,00 |
   | CAMI | 10.052,27 | 10.052,27 | 10.052,27 | 13.500,00 | 13.500,00 | 13.500,00 | 13.756,00 | 13.756,00 | s/d |
   | OSPEDYC | 14.740,98 | 15.593,00 | 16.045,20 | 16.590,74 | 17.022,10 | 17.379,56 | 17.709,77 | 18.081,68 | s/d |
   | OSPIA | s/d | s/d | s/d | s/d | s/d | 19.752,65 | 20.127,95 | ⚠️ | s/d |

   APE se actualiza todos los meses; CAMI, que siempre tiene valor propio, se mueve por
   escalones. **Las fórmulas de la planilla de sesiones quedaron en el valor de enero**,
   así que el devengado quedó 11.821.607,76 por debajo de lo facturado en nueve meses.
   Las facturas salieron bien; lo que se perdió es poder usar esa planilla como control.

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

10. **Los días autorizados por semana (`Hoja5`) también están viejos.** Al correr el
    motor contra los 414 meses-paciente facturados de 2026, 110 facturan **más**
    sesiones de las que permiten sus días autorizados: hay pacientes con 2 días en la
    hoja que facturan 20. La hoja misma lo insinúa, con anotaciones como "se habían
    pedido 5" y "esperando autorización". Es el dato que hay que reconfirmar paciente
    por paciente antes de migrar.

## Antes de usarlo con datos reales

Tres cosas que faltan y que no son del diseño sino del despliegue:

1. **No tiene usuarios ni contraseña.** `servidor.py` escucha sólo en `127.0.0.1`, o
   sea la propia máquina. Para que entre más de una persona desde la web hace falta
   autenticación y ponerlo detrás de un servidor real; `http.server` alcanza para la
   puesta a punto, no para publicar.
2. **La base es un archivo.** `facturacion.db` se copia para respaldar y se borra para
   empezar de nuevo. Para varios usuarios a la vez conviene pasar a PostgreSQL:
   `persistencia.py` es el único módulo que habría que tocar.
3. **Hay que reconfirmar los días autorizados de cada paciente** antes de migrar el
   histórico. Ver el hallazgo 10.

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
