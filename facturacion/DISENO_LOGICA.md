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
  cuit                       ← hoy está en la columna "CUIL" de la planilla
  condicion_iva              EXENTO | RESPONSABLE_INSCRIPTO | MONOTRIBUTO
  tope_sesiones_mes          límite propio de cada obra social (ej. OSSACRA 20)
  dias_vencimiento           default 60, para el semáforo
  activa

Modulo
  id, codigo                 APE | CAMI | OSPEDYC | OSTOPIA
  nombre, activo

Profesional
  id, nombre                 PASTE | BORELLO | BUTIGNOL | AMPUDIA | MARTIN
  matricula, activo

ValorModulo                  ← el corazón de "configurable mes a mes"
  modulo_id, anio, mes
  valor_sesion
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
  dni, cuil                  ← el CUIL real del paciente, que hoy no está en ninguna planilla
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

Si falta el valor del módulo para ese mes, el sistema **no inventa ni arrastra el del
mes anterior**: bloquea la facturación y avisa
*"Falta el valor del módulo CAMI para marzo 2026. Cargalo en Configuración → Valores por módulo."*
Esto es deliberado: arrastrar valores en silencio es justamente lo que produjo los
`12370,61` / `12370,16` de la planilla.

### R3 · Semáforo de cobranza

El color no se guarda: se calcula al mirar. Así nunca queda desactualizado.

| Estado | Condición | Color |
| --- | --- | --- |
| **Cobrado** | la factura tiene pago registrado | 🟩 verde |
| **Pendiente** | emitida, sin pago, y hace menos de 60 días de la emisión | 🟨 amarillo |
| **Atrasado** | emitida, sin pago, y pasaron 60 días o más desde la emisión | 🟥 rojo |
| Sin facturar | no hay factura emitida para ese mes y paciente | gris |

```
estado_cobranza(factura, hoy):
    si factura.estado == ANULADA           → anulada
    si existe pago                         → COBRADO
    dias = hoy − factura.fecha_emision
    si dias >= obra_social.dias_vencimiento → ATRASADO
    si no                                   → PENDIENTE
```

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

### R6 · Topes por obra social

El tope vive en la obra social y se puede pisar por autorización (un paciente puede
tener autorizado menos que el máximo de su obra social). Lo que indican los datos de
2026:

| Obra social | Tope observado | Confianza |
| --- | --- | --- |
| OSSACRA, OSPE, UOM | 20 | alta — pacientes con 5 días autorizados aparecen con 20, no con 21 |
| SANCOR | sin tope aparente | media — aparecen 21 sesiones |
| CAMI | 16 ó 20 según el caso | **a confirmar** — hay pacientes con 3 días autorizados facturando 16 |

Los tres valores se cargan en Configuración; los de CAMI hay que revisarlos con la
obra social antes de fijarlos.

---

## 4. Las pantallas

### P1 · Configuración (se carga una vez, se ajusta cuando cambia)

Cinco pestañas, todas con la misma forma: tabla + alta + edición con confirmación.

1. **Obras sociales** — nombre, CUIT, condición IVA, tope de sesiones, días de
   vencimiento.
2. **Módulos** — APE, CAMI, OSPEDYC, OSTOPIA.
3. **Valores por módulo** — la grilla clave: módulos en las filas, los 12 meses en
   las columnas. Se ve de un vistazo qué falta cargar.

   | Módulo | Ene | Feb | Mar | … |
   | --- | --- | --- | --- | --- |
   | APE | 16.753,78 | 17.722,15 | 17.722,15 | … |
   | CAMI | 10.052,27 | 10.052,27 | ⚠️ falta | … |
   | OSPEDYC | 16.753,78 | 17.722,15 | 17.722,15 | … |
   | OSTOPIA | — | — | — | … |

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

Desde cualquier fila amarilla o roja: fecha de pago (obligatoria), monto (viene
precargado con el de la factura, editable para pagos parciales), medio de pago y
observaciones. Al guardar, la fila pasa a verde.

### P5 · Ficha del paciente

Lo que se pidió ver completo al seleccionar un paciente:

```
PACIENTE EJEMPLO 1
DNI 12.345.678 · CUIL 27-12345678-4
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

## 8. Lo que falta definir

Cinco cosas que el diseño no puede resolver solo, porque son decisiones del negocio:

1. **El CUIL del paciente no está en ninguna planilla.** La columna "CUIL" es el CUIT
   de la obra social. La ficha de paciente lo pide: hay que conseguir esos datos.
2. **CAMI tiene dos CUIT** (`30709435538` y `30677896090`) y dos valores de sesión
   conviviendo en el mismo mes. ¿Son dos módulos distintos o un error arrastrado?
3. **El tope de CAMI** no se deduce de los datos (hay pacientes con 3 días autorizados
   facturando 16 sesiones). Hay que confirmarlo con la obra social.
4. **OSTOPIA no aparece en ninguna de las dos planillas.** Está en el pedido pero no
   en los datos: hace falta su valor de sesión y su tope.
5. **Pagos parciales.** El modelo los soporta (varios `Pago` por `Factura`), pero hay
   que definir qué color le toca a una factura cobrada a medias: ¿verde, amarillo, o un
   cuarto estado "parcial"?
