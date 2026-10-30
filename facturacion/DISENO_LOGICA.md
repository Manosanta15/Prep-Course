# Diseño de la lógica

> Objetivo: que una persona pueda cargar en enero la facturación de todo el año,
> ver en cualquier momento cuánto se facturó, cuánto se cobró y qué está atrasado,
> y no poder equivocarse sin que el sistema le avise.

---

## 1. La decisión de fondo: ¿qué es "una sesión"?

Todo lo demás se acomoda detrás de esta respuesta. Hay tres maneras de contestarla,
de menos a más fina. Conviene leerlas en orden porque cada una resuelve algo que la
anterior no puede.

### Manera A — Espejo de la planilla: la sesión es un número

Se guarda, por paciente y por mes, **la cantidad** de sesiones. Es lo que hacen las
planillas hoy: una celda con `17`.

```
FacturacionMensual
  paciente, mes, cantidad_sesiones, valor_sesion, total
```

- **A favor:** se entiende sin explicación, la migración es directa, una pantalla
  tipo grilla alcanza.
- **En contra:** la cantidad es un dato que alguien tipea, así que nadie puede
  responder *¿por qué 17 y no 18?*. Y no hay dónde apoyar los pedidos del usuario:
  "fechas de las sesiones ya generadas", "cuántas van transcurridas al día de la
  fecha", "los feriados no se facturan", "el paciente estuvo internado". Todo eso
  necesita saber **qué días** eran, no cuántos.

**Conclusión: insuficiente.** Es el punto de partida, no el destino.

### Manera B — Calendario primero: la sesión es una fecha generada

El dato que se carga es la **autorización**: qué días de la semana, a qué hora, desde
cuándo y hasta cuándo. El sistema genera las fechas y cuenta.

```
Autorizacion ──genera──> Sesion (una fila por fecha concreta)
  lunes 15:00                2026-01-05 15:00  prevista
  miércoles 16:30            2026-01-07 16:30  prevista
                             2026-01-01       no_facturable (feriado)
```

- **A favor:** la cantidad deja de ser un dato y pasa a ser un resultado, que siempre
  se puede justificar mostrando el calendario. Resuelve de una sola vez los cuatro
  pedidos que la Manera A no podía: horario distinto por día, feriados, internación
  y "sesiones transcurridas al día de hoy" (las que tienen fecha anterior a hoy).
- **En contra:** hay que generar y guardar unas 20 filas por paciente por mes
  (80 pacientes × 12 meses ≈ 19.000 filas al año: nada para una base de datos, pero
  sí algo que hay que poder regenerar cuando cambia una autorización).

**Esta es la recomendada.** Es la única que sostiene todo lo pedido sin inventar
datos, y sigue siendo explicable: *"estas son las fechas, estas no se cuentan y por
qué, el total es la multiplicación"*.

### Manera C — Eventos primero: la sesión es un hecho inmutable

Nada se corrige: todo cambio es un asiento nuevo que anula o ajusta el anterior, como
en contabilidad. La cantidad de un mes es la suma de los asientos vigentes.

- **A favor:** auditoría perfecta, nada se pierde, se puede ver el estado a cualquier
  fecha pasada.
- **En contra:** para corregir un error de tipeo hay que entender el concepto de
  contraasiento. Es exactamente lo contrario de "intuitivo y entendible para el ser
  humano".

**Conclusión: demasiado para esta escala.** Pero su mejor idea se puede tomar prestada:
**las facturas y los pagos sí se tratan como hechos inmutables** (ver §5). Una factura
emitida no se edita, se anula y se emite otra. Una sesión prevista sí se puede corregir.

### Resumen de la decisión

| | A · número | **B · calendario** | C · eventos |
| --- | --- | --- | --- |
| Horario distinto por día | no | **sí** | sí |
| Feriados e internación explicables | no | **sí** | sí |
| Sesiones transcurridas a hoy | no | **sí** | sí |
| Fácil de entender | sí | **sí** | no |
| Auditoría total | no | parcial | sí |

**Diseño elegido: B para sesiones, C acotado para facturas y pagos.**

---

## 2. Modelo de datos

Ocho entidades. Las tres primeras son configuración que se carga una vez; las demás
son el movimiento del año.

### Configuración (sección "Configuración" de la app)

```
ObraSocial
  id, nombre
  cuit                       ← hoy está en la columna "CUIL" de la planilla; editable
  condicion_iva              EXENTO | RESPONSABLE_INSCRIPTO | MONOTRIBUTO
  tiene_tope                 sí / no — hay obras sociales que no topean (ver R6)
  tope_sesiones_mes          sólo si tiene_tope; si no, se ignora
  dias_vencimiento           default 60, para el semáforo
  activa

Modulo
  id, codigo                 APE | CAMI | OSPEDYC | OSPIA
  nombre, activo

Profesional
  id, nombre                 PASTE | BORELLO | BUTIGNOL | AMPUDIA | MARTIN
  matricula, activo

ValorModulo                  ← el corazón de "configurable mes a mes"
  modulo_id, anio, mes
  valor_sesion
  tope_sesiones              opcional: si se carga, pisa el tope de la obra social
                             para ese módulo y ese mes
  vigente_desde
  UNIQUE (modulo_id, anio, mes)

Feriado
  fecha, descripcion
  tipo                       NACIONAL | PROVINCIAL | INSTITUCIONAL
```

`ValorModulo` es lo que hoy no existe y causa los errores de tipeo: en lugar de
escribir `16753.78` dentro de 60 fórmulas distintas, el valor se carga **una vez por
módulo y por mes**. Si en agosto el módulo general pasa a 20.550,64, se cambia en un
solo lugar y todos los meses de agosto en adelante quedan bien.

### Padrón

```
Paciente
  id, apellido_nombre
  dni, cuil                  ← los carga el usuario al dar de alta al paciente:
                             no están en ninguna de las dos planillas
  obra_social_id, modulo_id, profesional_id
  nro_afiliado
  activo

PacienteAlias                ← resuelve los 164 cruces dudosos
  paciente_id, alias
  'PEREZ ACUATICA' y 'Perez pileta' → mismo paciente
```

### Movimiento del año

```
Autorizacion                 lo que la obra social autorizó
  paciente_id
  vigencia_desde, vigencia_hasta
  tope_sesiones_mes          si es null, se usa el de la obra social
  observaciones

AutorizacionDia              un día autorizado con su horario propio
  autorizacion_id
  dia_semana                 1=lunes … 5=viernes
  hora                       puede diferir entre días: lunes 15:00, miércoles 16:30
  UNIQUE (autorizacion_id, dia_semana)

Sesion                       una fila por fecha concreta, generada por el sistema
  paciente_id, autorizacion_id
  fecha, hora
  estado                     PREVISTA | REALIZADA | NO_FACTURABLE
  motivo_no_facturable       FERIADO | INTERNACION | AUSENCIA | FUERA_DE_TOPE
  facturable                 booleano derivado

Internacion                  los días que el paciente estuvo internado
  paciente_id
  desde, hasta               hasta null = sigue internado
  institucion, observaciones

Factura                      hecho inmutable
  id, paciente_id, obra_social_id, profesional_id
  anio, mes                  período que factura
  nro_factura                null mientras no se emitió
  cantidad_sesiones          copiada al emitir, no recalculada después
  valor_sesion               copiado al emitir
  monto
  fecha_emision
  estado                     BORRADOR | EMITIDA | ANULADA
  anula_a_factura_id

Pago                         hecho inmutable
  factura_id
  fecha_pago                 el dato que el usuario pidió guardar
  monto
  medio_pago                 DEPOSITO | TRANSFERENCIA | CHEQUE | EFECTIVO
  observaciones
```

### El caso APROSS, aparte

APROSS no factura por paciente: es un convenio con un total mensual y un descuento.

```
ConvenioApross
  anio, mes
  cantidad_pacientes         31 en 2026
  cantidad_sesiones
  valor_sesion
  total_bruto                cantidad_sesiones × valor_sesion, o lo carga el usuario
  porcentaje_descuento       4,00 configurable
  monto_descuento            = total_bruto × porcentaje / 100   (calculado)
  importe_abonado            = total_bruto − monto_descuento     (calculado)
  fecha_pago                 el usuario pidió que el importe abonado se guarde con fecha
  medio_pago
  estado                     BORRADOR | CERRADO | COBRADO

ConvenioAprossPaciente       el padrón del convenio, para saber a quiénes cubre
  convenio_id, paciente_id, sesiones
```

Verificado contra la planilla de enero 2026:
`6.031.260 × 4% = 241.250,40` y `6.031.260 − 241.250,40 = 5.790.009,60`. Coincide.

---

## 3. Las reglas, una por una

### R1 · Generación de sesiones del mes

```
sesiones_del_mes(paciente, año, mes):
    fechas = todas las fechas del mes que caen en un día autorizado
             y están dentro de la vigencia de la autorización
    para cada fecha:
        si es feriado            → NO_FACTURABLE, motivo FERIADO
        si cae en internación    → NO_FACTURABLE, motivo INTERNACION
        si no                    → PREVISTA, facturable
    tope = autorización.tope ?? obra_social.tope
    si facturables > tope:
        las que exceden         → NO_FACTURABLE, motivo FUERA_DE_TOPE
    devolver fechas con su estado y motivo
```

Dos propiedades importantes: es **determinística** (mismos datos, mismo resultado) y
es **explicable** (cada fecha descartada dice por qué). Nunca muestra un número
pelado.

### R2 · Monto

```
valor = ValorModulo(modulo_del_paciente, año, mes).valor_sesion
monto = cantidad_facturable × valor
```

#### De dónde sale el valor correcto

Las fórmulas de la planilla de sesiones **no sirven** para esto: quedaron clavadas en
el valor de enero. La fuente correcta es el campo **Monto** de la planilla de
facturación, que es lo que realmente se facturó:

```
valor_sesion = Monto / Cant. Sesiones
```

Aplicado sobre las filas de SANCOR, que facturan al módulo APE, da la serie exacta.
Los cuatro módulos de 2026 quedan así:

| Módulo | Ene | Feb | Mar | Abr | May | Jun | Jul | Ago | Sep |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **APE** | 16.753,78 | 17.722,15 | 18.236,09 | 18.856,10 | 19.346,37 | 19.752,65 | 20.127,95 | 20.550,64 | 20.900,00 |
| **CAMI** | 10.052,27 | 10.052,27 | 10.052,27 | 13.500,00 | 13.500,00 | 13.500,00 | 13.756,00 | 13.756,00 | s/d |
| **OSPEDYC** | 14.740,98 | 15.593,00 | 16.045,20 | 16.590,74 | 17.022,10 | 17.379,56 | 17.709,77 | 18.081,68 | s/d |
| **OSPIA** | s/d | s/d | s/d | s/d | s/d | 19.752,65 | 20.127,95 | ⚠️ | s/d |

Tres cosas quedan claras con esta serie:

- **APE aumenta todos los meses.** No es un valor que se fija una vez: es una
  actualización mensual. Esto es exactamente lo que justifica que `ValorModulo` sea una
  fila por módulo y por mes, y no un campo en el módulo.
- **CAMI siempre tiene valor propio, distinto del APE general**, y se mueve por
  escalones (tres meses en 10.052,27, tres en 13.500,00, dos en 13.756,00) en lugar de
  todos los meses.
- **OSPEDYC también tiene serie propia**, más baja que APE todo el año.
- **OSPIA tiene muy pocos datos** (un paciente, y recién desde junio). El valor de
  agosto que aparece, 12.370,61, es el valor de 2025: es una fila vieja, no un valor
  de agosto. Hay que cargarlo a mano.

#### El desfasaje entre las dos planillas

Como las fórmulas de la planilla de sesiones nunca se actualizaron, el devengado quedó
por debajo de lo facturado en siete de los nueve meses:

| | Devengado según la planilla | Con el valor realmente facturado | Diferencia |
| --- | --- | --- | --- |
| Enero | 10.290.171,82 | 10.290.171,82 | 0,00 |
| Marzo | 15.348.594,26 | 16.436.247,63 | +1.087.653,37 |
| Julio | 16.648.104,68 | 19.832.331,45 | +3.184.226,77 |
| **Total 9 meses** | **140.435.115,50** | **152.256.723,26** | **+11.821.607,76** |

No es plata perdida: las facturas salieron con el valor correcto, el que está en la
planilla de facturación. Lo que se perdió es la capacidad de usar la planilla de
sesiones como control, porque dejó de coincidir con la realidad. Si alguien hubiera
facturado siguiéndola, habría facturado 11,8 millones de menos en nueve meses.

El `informe.md` que genera el script lista mes por mes y módulo por módulo dónde las
dos planillas discrepan.

Si falta el valor del módulo para ese mes, el sistema **no inventa ni arrastra el del
mes anterior**: bloquea la facturación y avisa
*"Falta el valor del módulo CAMI para marzo 2026. Cargalo en Configuración → Valores por módulo."*
Esto es deliberado: arrastrar valores en silencio es justamente lo que produjo los
`12370,61` / `12370,16` de la planilla.

### R3 · Semáforo de cobranza

El color no se guarda: se calcula al mirar. Así nunca queda desactualizado.

| Estado | Condición | Color |
| --- | --- | --- |
| **Cobrado** | los pagos registrados cubren el monto de la factura | 🟩 verde |
| **Parcial** | hay pagos registrados pero suman menos que la factura | 🟦 azul |
| **Pendiente** | emitida, sin pago, y hace menos de 60 días de la emisión | 🟨 amarillo |
| **Atrasado** | emitida, sin pago, y pasaron 60 días o más desde la emisión | 🟥 rojo |
| Sin facturar | no hay factura emitida para ese mes y paciente | gris |

```
estado_cobranza(factura, hoy):
    si factura.estado == ANULADA            → anulada
    cobrado = suma de los pagos de la factura
    si cobrado >= factura.monto             → COBRADO
    si cobrado > 0                          → PARCIAL
    dias = hoy − factura.fecha_emision
    si dias >= obra_social.dias_vencimiento → ATRASADO
    si no                                   → PENDIENTE
```

El azul gana sobre el amarillo y el rojo a propósito: una factura cobrada a medias no
es lo mismo que una que no pagaron nada, y conviene que se distinga de un vistazo. El
saldo pendiente se muestra al lado del monto (`284.814,26 · saldo 84.814,26`).

Los 60 días salen de `obra_social.dias_vencimiento`, no de una constante, porque cada
obra social paga a su ritmo y conviene poder ajustarlo sin tocar el código.

### R4 · Feriados

Se cargan una vez al año en Configuración. Un feriado afecta a **todos** los
pacientes cuyo día autorizado caiga en esa fecha. Al cargar o borrar uno, el sistema
avisa antes de confirmar:

> *"El 1 de mayo de 2026 (viernes) es día autorizado de 14 pacientes. Agregarlo como
> feriado va a quitar 14 sesiones previstas de mayo. ¿Confirmás?"*

Hay un caso que conviene decidir de entrada: **qué pasa si el feriado se agrega después
de emitir la factura.** La respuesta del diseño es que no la toca — la factura es un
hecho inmutable (§5) —, pero sí lo informa:
*"Mayo ya tiene 14 facturas emitidas con las sesiones anteriores. El feriado afecta
sólo las no facturadas; para corregir las emitidas hay que anularlas y re-emitirlas."*

### R5 · Internación

Mismo mecanismo, pero por paciente. Se carga un rango de fechas y las sesiones que
caen dentro quedan no facturables. Si `hasta` es null el paciente sigue internado y
las sesiones futuras se marcan a medida que pasan. Al cerrar la internación se
regeneran las sesiones del rango.

### R6 · Topes de sesiones

> **Corrección sobre la versión anterior de este documento.** Yo había leído la
> `Hoja8` de la planilla como una tabla de topes por obra social. Al verificarla, no
> lo es. Abajo está lo que realmente dicen los datos.

Tres comprobaciones sobre la `Hoja8`:

1. No es `sesiones semanales × 4`: coincide en 28 pacientes y difiere en 22.
2. No es un tope que se respete: en 2026 hay **106 casos** en los que se facturaron
   más sesiones que el número de esa hoja. SANCOR lo supera 66 veces.
3. Pero **11 obras sociales nunca lo superan**: CAMIONEROS, LUZ Y FUERZA, OSECAC,
   OSFATUN, OSPE, OSSACRA, OSSEG, SUME y UOCRA.

La conclusión es que la `Hoja8` es **la foto de un mes cualquiera**, no una tabla de
topes. Para algunas obras sociales ese número coincide con su tope real; para otras es
sólo lo que se facturó ese mes. **No se puede deducir el tope de las planillas.**

Por eso el tope es configurable en tres niveles, con esta precedencia:

```
tope_efectivo(paciente, año, mes):
    1. autorización.tope_sesiones_mes      si el paciente tiene uno propio
    2. ValorModulo(módulo, año, mes).tope  si se cargó para ese módulo y mes
    3. obra_social.tope_sesiones_mes       si obra_social.tiene_tope
    4. sin tope                            se factura el calendario completo
```

El nivel 2 es el que pediste para OSPIA: valor y tope cargables mes a mes. Sirve para
cualquier módulo, no sólo para ese.

El nivel 3 tiene el `tiene_tope` en sí/no explícito en lugar de usar "tope = 0" o
"tope vacío" para decir "no topea". Que haya que elegir obliga a pensarlo una vez por
obra social, y evita que un campo sin cargar se interprete como un tope de cero.

Al cargar los topes conviene arrancar por las 11 obras sociales de la lista, que son
las que probablemente sí topean, y dejar las demás en "sin tope" hasta confirmarlo.

---

## 4. Las pantallas

### P1 · Configuración (se carga una vez, se ajusta cuando cambia)

Cinco pestañas, todas con la misma forma: tabla + alta + edición con confirmación.

1. **Obras sociales** — nombre, CUIT **editable**, condición IVA, tiene tope sí/no,
   tope de sesiones, días de vencimiento.
2. **Módulos** — APE, CAMI, OSPEDYC, OSPIA.
3. **Valores y topes por módulo** — la grilla clave: módulos en las filas, los 12 meses
   en las columnas, valor y tope en cada celda. Se ve de un vistazo qué falta cargar.

   | Módulo | Ene | Feb | … | Ago | Sep |
   | --- | --- | --- | --- | --- | --- |
   | APE | 16.753,78 | 17.722,15 ⚠️ | … | 20.550,64 | 20.550,64 |
   | CAMI | 10.052,27 | 10.052,27 | … | 13.756,00 | 13.756,00 |
   | OSPEDYC | 16.753,78 | 17.722,15 | … | 18.081,68 | 18.081,68 |
   | OSPIA | 16.753,78 | 17.722,15 | … | 20.550,64 | 20.550,64 |

   El ⚠️ de febrero marca el mes donde la planilla tiene dos valores conviviendo para
   el módulo general: el sistema lo señala pero no elige por su cuenta.

   Con un botón **"Copiar del mes anterior"** para cuando no hay aumento, que es lo
   habitual. Explícito, no automático.
4. **Profesionales**.
5. **Feriados del año**.

### P2 · Carga anual (la tarea de enero)

El usuario carga **autorizaciones**, no sesiones. Por paciente: días de la semana con
su horario, vigencia y tope. Al confirmar, el sistema genera los 12 meses de una vez
y muestra el resultado antes de guardar:

```
PACIENTE EJEMPLO 1 · SANCOR · módulo APE · Lic. PASTE
Días: lunes 15:00 · martes 15:00 · miércoles 16:30 · jueves 16:30
Vigencia: 01/01/2026 – 31/12/2026

Mes        Previstas  Feriados  Tope  A facturar   Monto
Enero          18        -1      --      17      284.814,26
Febrero        16         0      --      16      283.554,40
…
TOTAL         204        -9              195    3.456.789,00

[Revisar mes por mes]   [Confirmar y generar]
```

Nada se guarda hasta que el usuario ve ese resumen y confirma.

### P3 · Facturación del mes

Una fila por paciente, con todo pre-cargado y nada que tipear salvo el número de
factura:

| ✓ | Paciente | O. Social | Prof. | Sesiones | Valor | Monto | Nº Factura | Estado |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ☑ | Paciente ejemplo 1 | SANCOR | PASTE | 17 ⓘ | 16.753,78 | 284.814,26 | `661` | 🟩 Cobrado |
| ☑ | Paciente ejemplo 2 | SANCOR | PASTE | 21 ⓘ | 16.753,78 | 351.829,38 | `662` | 🟨 Pendiente |
| ☐ | Paciente ejemplo 3 | SANCOR | MARTIN | 0 ⓘ | 16.753,78 | 0,00 | — | ⬜ No factura |

La ⓘ al lado de la cantidad abre el detalle de fechas: *"21 sesiones: 5/1, 6/1, 7/1…
No se cuentan: 1/1 (feriado)"*. Ahí está la respuesta a *¿por qué 21?*.

Filtros por obra social, profesional, módulo y estado — todos los campos
seleccionables, como se pidió.

### P4 · Confirmar un pago

Desde cualquier fila amarilla, roja o azul: fecha de pago (obligatoria), monto (viene
precargado con el saldo pendiente, editable), medio de pago y observaciones.

Al guardar, la fila pasa a **verde** si el pago completa el monto de la factura, o a
**azul** si queda saldo. Una factura puede recibir varios pagos: cada uno queda
registrado con su fecha, y el azul se mantiene hasta que la suma cubra el total.

### P5 · Ficha del paciente

Lo que se pidió ver completo al seleccionar un paciente:

```
PACIENTE EJEMPLO 1
DNI 12.345.678 · CUIL 27-12345678-4          [editar]
SANCOR · CUIT 30-59035479-8 · EXENTO · módulo APE · Lic. PASTE
Autorización: lunes 15:00 · martes 15:00 · miércoles 16:30 · jueves 16:30

Sesiones 2026:  204 totales  ·  178 transcurridas  ·  26 por transcurrir
                                      (al 30/10/2026)

Mes      Sesiones  Transcurridas  Monto        Estado
Enero       17          17        284.814,26   🟩 Cobrado 06/03
Febrero     16          16        283.554,40   🟩 Cobrado 26/03
…
Octubre     18           9        310.000,00   🟥 Atrasado (67 días)

[Ver todas las fechas generadas]
```

"Transcurridas" = sesiones con fecha menor o igual a hoy. Se recalcula al mirar, así
que nunca queda vieja.

El DNI y el CUIL los carga el usuario al dar de alta al paciente y se pueden editar
después: no están en ninguna de las dos planillas, así que el sistema arranca con esos
campos vacíos y los va completando a medida que se usan. Conviene que la ficha muestre
un aviso suave mientras falten, sin bloquear la facturación, porque hoy se factura sin
ese dato.

### P6 · Tablero anual

La vista que pidió el usuario: meses en las columnas, pacientes en las filas, el color
del semáforo en cada celda, totales por paciente a la derecha y totales por mes abajo.

| Paciente | Ene | Feb | Mar | … | Dic | Total paciente |
| --- | --- | --- | --- | --- | --- | --- |
| Paciente ejemplo 1 | 🟩 284.814 | 🟩 283.554 | 🟨 283.554 | … | | 3.456.789 |
| Paciente ejemplo 2 | 🟩 351.829 | 🟩 351.829 | 🟥 351.829 | … | | 4.221.952 |
| **Total mensual** | **1.189.518** | **1.205.000** | … | | | **—** |

Con el corte por profesional, porque la planilla hoy agrupa por "Lic. que lo factura"
y calcula un total mensual por profesional (`=SUM(F2:F8)`).

### P7 · APROSS

Botón y pantalla propios, como se pidió, porque la lógica es distinta:

```
APROSS · Convenio marzo 2026

Pacientes:           31
Cantidad sesiones:   [  372 ]
Valor por sesión:    [ 16.753,78 ]
                     ─────────────
Total:                6.232.406,16
Descuento 4%:          -249.296,25      ← % configurable
                     ─────────────
Importe abonado:      5.983.109,91

Fecha de pago:  [ __/__/____ ]
Medio de pago:  [ Depósito ▾ ]

[Guardar borrador]   [Cerrar convenio]   [Registrar pago]
```

El descuento y el importe abonado son calculados, nunca editables a mano: así no
pueden quedar inconsistentes con el total.

---

## 5. Qué se puede corregir y qué no

Esta es la regla que hace que el sistema sea confiable sin ser rígido:

| Dato | ¿Editable? | Cómo |
| --- | --- | --- |
| Autorización, días, horarios | sí | se edita y se regeneran las sesiones futuras |
| Sesión prevista | sí | se puede marcar no facturable con motivo |
| Feriados, internaciones | sí | se regeneran las sesiones afectadas no facturadas |
| Valor de módulo de un mes | sí, con aviso | si hay facturas emitidas de ese mes, avisa que no las toca |
| **Factura emitida** | **no** | se **anula** (queda el registro) y se emite una nueva |
| **Pago registrado** | **no** | se **revierte** (queda el registro) y se registra de nuevo |

Las facturas y los pagos son los únicos datos que salen de la organización hacia la
obra social: ahí no puede haber "¿quién cambió esto y cuándo?" sin respuesta.
Todo lo demás es interno y se corrige con normalidad.

---

## 6. Las confirmaciones

El pedido fue que **toda modificación posible tenga mensaje de confirmación**. Pero si
todo pide confirmación de la misma manera, la gente aprende a apretar "Sí" sin leer y
la protección desaparece. Entonces: tres niveles según lo que está en juego.

**Nivel 1 — Avisar después (sin interrumpir).** Para lo que es fácil de deshacer:
editar un teléfono, cambiar un horario. Se guarda y aparece un cartelito
*"Guardado. [Deshacer]"* que dura unos segundos.

**Nivel 2 — Confirmar antes, mostrando el impacto.** Para lo que afecta a varios
registros. El mensaje dice **qué va a pasar**, con números, no "¿está seguro?":

> *"Cambiar el valor del módulo APE de marzo 2026 a $18.500,00 va a recalcular 37
> facturaciones no emitidas. Las 12 facturas ya emitidas de marzo no se modifican.
> Total del mes: 1.205.000 → 1.332.000. ¿Confirmás?"*
>
> `[Cancelar]` `[Confirmar el cambio]`

**Nivel 3 — Confirmar escribiendo.** Para lo irreversible o lo que sale hacia afuera:
anular una factura, borrar un paciente con movimiento, cerrar un convenio. Hay que
escribir algo que obligue a leer:

> *"Vas a anular la factura 661 de Paciente ejemplo 1 por $284.814,26, que ya figura
> cobrada el 06/03/2026. Esto también revierte el pago registrado.*
> *Escribí el número de factura para confirmar:* `[______]`"

Y una regla transversal: **el sistema nunca borra en serio.** Baja lógica siempre, con
quién y cuándo. Un paciente que se da de baja deja de aparecer en la facturación pero
su historial sigue completo.

---

## 7. Plan de implementación

Cada etapa deja algo usable; no hace falta terminar todo para empezar a usarlo.

| # | Etapa | Qué habilita |
| --- | --- | --- |
| 1 | Configuración + padrón de pacientes | Los datos que hoy están repartidos en 6 hojas quedan en un solo lugar |
| 2 | Autorizaciones + generación de sesiones (R1) | Se termina de tipear cantidades: el sistema las calcula y las explica |
| 3 | Feriados e internaciones (R4, R5) | Los dos motivos de no facturación quedan cubiertos |
| 4 | Facturas + pagos + semáforo (R3) | Reemplaza la planilla de facturación completa |
| 5 | Tablero anual + ficha de paciente (P5, P6) | Las vistas que hoy no existen en ninguna planilla |
| 6 | APROSS (P7) | El convenio con su descuento del 4% |
| 7 | Migración con `extraer_planillas.py` | Entra el histórico; los 164 cruces dudosos se confirman a mano una sola vez |

La migración va última a propósito: conviene tener el modelo funcionando y probado con
datos de prueba antes de meterle 9 años de planillas con nombres inconsistentes.

---

## 8. Lo que quedó decidido

Las cinco preguntas que este diseño no podía responder solo, ya resueltas:

| # | Pregunta | Decisión |
| --- | --- | --- |
| 1 | El CUIL del paciente no está en las planillas | **Lo carga el usuario** al dar de alta al paciente, y es editable. El sistema no lo deduce de ningún lado |
| 2 | CAMI tiene dos CUIT | Se usa **`30709435538`** (el de 88 filas contra 9 del otro) y el campo queda **editable** por si hay que corregirlo |
| 3 | El tope de CAMI no se deduce de los datos | El tope pasa a ser **configurable en sí/no + valor**, y no sólo para CAMI: para todas las obras sociales y, mes a mes, para cada módulo (ver R6) |
| 4 | "OSTOPIA" no aparecía en las planillas | Era **OSPIA**, y sí está: un paciente con 4 días autorizados, valor igual al módulo general. Valor y tope se cargan **mes a mes** |
| 5 | Qué color lleva una factura cobrada a medias | **Azul**, como cuarto estado "Parcial", con el saldo pendiente al lado del monto |

## 9. Lo que todavía hay que confirmar

1. **El valor de OSPIA mes a mes.** Tiene un solo paciente y arranca en junio, así que
   la planilla no alcanza para reconstruir la serie. Hay que cargarla a mano.
2. **Qué obras sociales topean de verdad.** La validación del motor contra la planilla
   (ver abajo) acota mucho la lista, pero conviene confirmarla con cada obra social
   antes de cargar el tope: si se carga un tope que no existe, el sistema factura de
   menos.
3. **Los días autorizados de la semana están desactualizados en la planilla.** El motor
   los necesita para generar las fechas, y hoy la `Hoja5` no coincide con lo que se
   factura en 110 de 414 casos. Es el dato que hay que reconfirmar paciente por
   paciente antes de migrar.

---

## 10. Etapa 2 y 3: el motor de sesiones

Implementadas en `motor/`. Las dos etapas van juntas porque los feriados y las
internaciones son entradas de la misma función que genera las fechas.

| Archivo | Qué hace |
| --- | --- |
| `modelo.py` | Las entidades de la sección 2 como dataclasses, sin dependencias |
| `sesiones.py` | R1 (generación), R2 (monto), R6 (tope en cascada) |
| `feriados.py` | Semilla de feriados nacionales, con Pascua calculada |
| `test_sesiones.py` | 34 tests |
| `validar_contra_planilla.py` | Contrasta el motor con lo que la planilla factura |

### Lo que devuelve el motor

No un número, sino el número **y su porqué**:

```python
r = generar_sesiones(autorizacion, 2026, 1, obra_social, feriados=[...])

r.cantidad                  # 21
r.explicacion()             # "21 sesiones · no se cuentan 1 por feriado: 01/01"
r.transcurridas(hoy)        # 10 al 15/01/2026
r.descartadas_por(FERIADO)  # las fechas concretas, con su descripción
r.tope_aplicado             # None si la obra social no topea
```

`transcurridas` se calcula al mirar, con la fecha que se le pase, así que nunca queda
vieja. Es lo que alimenta la ficha del paciente (P5).

### Decisiones de implementación

- **El tope se aplica al final y descarta las últimas fechas del mes**, no las
  primeras: las primeras ya se prestaron. Las descartadas quedan con motivo
  `FUERA_DE_TOPE`, no desaparecen.
- **Un día que es feriado y además cae en internación se descuenta una sola vez.** El
  motivo que queda registrado es el feriado, porque es el que aplica a todos los
  pacientes.
- **Si falta el valor del módulo, el motor levanta `ValorModuloFaltante`** en lugar de
  usar el del mes anterior. Es la decisión de R2 llevada a código: el desfasaje de 11,8
  millones salió justamente de arrastrar valores en silencio.
- **La internación de un paciente no afecta a otro**, aunque el rango de fechas se
  solape. Hay un test para eso.

### Qué dice la validación contra la planilla

El motor reproduce enero 2026 exactamente: con los cinco días autorizados y el feriado
del 1º da 21, que es lo que factura la planilla. Los tests cubren los cuatro casos
(5, 4, 3 y 2 días por semana) contra los números reales.

Sobre los 414 meses-paciente facturados en 2026:

| Resultado | Casos | Qué significa |
| --- | --- | --- |
| Explicable | 242 · 58,5% | El número facturado es uno de los que permite el calendario |
| Por encima | 110 · 26,6% | Facturó **más** de lo que permiten sus días autorizados |
| Por debajo | 62 · 15,0% | Facturó menos: internación, ausencias o un tope |

Los dos grupos que no cierran dicen cosas distintas:

- **Los 62 "por debajo" tienen una firma clara: 20 de ellos facturan exactamente 20**,
  y son de OSSEG, OSSACRA y OSPE. Eso es un tope de 20 funcionando. Confirma por otra
  vía la lista de obras sociales que topean.
- **Los 110 "por encima" son un problema de datos, no del motor**: hay pacientes con 2
  días autorizados en la `Hoja5` que facturan 20 sesiones. La `Hoja5` está vieja —ella
  misma tiene anotaciones como "se habían pedido 5" y "esperando autorización"—. Son
  los pacientes cuya autorización hay que reconfirmar antes de migrar.

Para correr la validación:

```bash
cd facturacion/motor
python3 validar_contra_planilla.py SESIONES.xlsx --anio 2026
```

---

## 11. Etapa 4: facturas, pagos y semáforo

| Archivo | Qué hace |
| --- | --- |
| `cobranza.py` | R3: el semáforo, el cuadro de montos y el tablero anual |
| `facturacion.py` | Emitir, anular, reemplazar, registrar y revertir pagos |
| `confirmaciones.py` | Los mensajes de la sección 6, con el impacto calculado |
| `test_cobranza.py` | 45 tests |
| `demo_tablero.py` | Un año completo corrible, para ver los cuadros sin base cargada |

### El semáforo no se guarda

`estado_cobranza(factura, pagos, obra_social, hoy)` recibe la fecha en que se mira. No
hay ningún campo "estado" en la base que pueda quedar viejo: una factura pasa de
amarilla a roja sola, porque el rojo es una consecuencia del paso del tiempo y no un
dato que alguien tenga que actualizar.

Los días de vencimiento salen de `obra_social.dias_vencimiento`, así que cada obra
social puede tener el suyo. Hay tests del borde exacto: a los 59 días sigue amarillo, a
los 60 pasa a rojo.

### El azul gana

Una factura cobrada a medias queda azul **aunque esté vencida**. Es deliberado: no es
lo mismo que no hayan pagado nada, y conviene que se distinga de un vistazo. La celda
muestra el saldo al lado del monto:

```
284.814,26 · saldo 84.814,26
```

### Pagos: varios por factura, y se revierten en lugar de editarse

Un pago no se edita ni se borra. Para deshacerlo se registra **otro pago con monto
negativo** que apunta al original. Así `cobrado = suma de los montos` sigue funcionando
sin ningún caso especial, y los dos registros quedan a la vista.

```python
p   = registrar_pago(1, factura, [], date(2026, 3, 6))        # 284.814,26
rev = revertir_pago(2, p, date(2026, 3, 20), "el depósito no se acreditó")
cobranza.cobrado(factura, [p, rev])                            # 0.0
```

Al revertirse el pago la factura vuelve al color que le corresponde por antigüedad, que
a esa altura suele ser rojo. Eso es correcto: la deuda sigue ahí.

Si no se pasa monto, se toma el saldo pendiente —que es lo que la pantalla precarga—.
Un pago que supera el saldo se rechaza salvo que se confirme con `permitir_exceso`.

### Anular arrastra los pagos

`anular()` pone la factura en `ANULADA`, guarda el motivo (obligatorio) y devuelve las
reversiones de todos sus pagos. Es exactamente lo que anuncia el mensaje de nivel 3:

> *"Vas a anular la factura 661 de Paciente ejemplo 1 por 351.829,38. Ya figura cobrada
> 351.829,38 el 06/03/2026. Esto también revierte el pago registrado."*
> Escribí el número de factura para confirmar: `[____]`

Después, `reemplazar()` arma el borrador que la sustituye apuntando a la anulada. Las
anuladas no aparecen en el tablero, pero quedan en la base.

### Los dos cuadros

`resumen()` devuelve el cuadro de montos que pediste:

```
Facturado                          3.526.922,35
Cobrado          (verde)           1.998.489,00
Saldo de parciales (azul)            214.805,65
Pendiente        (amarillo)          870.812,80
Atrasado +60 días (rojo)             442.814,90
────────────────────────────────────────────────
Por cobrar                         1.528.433,35
```

`tablero_anual()` devuelve el P6: color y monto por paciente y mes, total por paciente,
total por mes y total por profesional —este último porque la planilla agrupa por "Lic.
que lo factura"—.

### Las confirmaciones, calculadas

`confirmaciones.py` no muestra nada: calcula el impacto y devuelve el nivel que
corresponde. La pantalla sólo lo renderiza. El nivel no es fijo por operación, sale de
lo que está en juego:

| Operación | Nivel | Por qué |
| --- | --- | --- |
| Cambiar un horario | 1 · avisar después | Fácil de deshacer |
| Feriado que cae sábado | 1 · avisar después | No afecta ninguna sesión: no molesta al usuario |
| Feriado en día hábil | 2 · confirmar antes | Dice a cuántos pacientes alcanza y qué pasa con lo ya emitido |
| Cambiar el valor de un módulo | 2 · confirmar antes | Dice cuántas recalcula y cuántas no toca |
| Anular una factura | 3 · escribir | Sale hacia la obra social y puede revertir un pago |
| Baja de paciente **con** facturas | 3 · escribir | Dice cuántas facturas tiene y que el sistema no borra |
| Baja de paciente **sin** movimiento | 2 · confirmar antes | No hay nada que perder |

Que un feriado de fin de semana baje a nivel 1 y que una baja sin movimiento baje a
nivel 2 es parte del punto de la sección 6: si todo confirma igual, la gente deja de
leer.

### Validación

`demo_tablero.py` recorre un año completo con los valores reales del módulo APE y
reproduce facturas de la planilla:

| Mes | Sesiones | Valor | Monto calculado | En la planilla |
| --- | --- | --- | --- | --- |
| Enero | 21 | 16.753,78 | 351.829,38 | 351.829,38 ✓ |
| Julio | 22 | 20.127,95 | 442.814,90 | 442.814,90 ✓ |

### Lo que sigue

Etapas 5, 6 y 7: el tablero y la ficha ya tienen sus funciones, falta la interfaz;
APROSS tiene el cálculo y las confirmaciones, falta la pantalla; y la migración con
`extraer_planillas.py`, que va última y necesita primero reconfirmar los días
autorizados de cada paciente.
