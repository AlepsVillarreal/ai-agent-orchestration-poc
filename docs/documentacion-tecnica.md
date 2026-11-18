# Documentación técnica

Este documento responde las diez preguntas de defensa de `TASK.md` §9 y explica cómo se usa Google
ADK, la memoria, la idempotencia y la observabilidad, con los límites conocidos y las decisiones
clave. Las figuras, el ciclo de un turno y la tabla LLM/código están en
[`arquitectura.md`](arquitectura.md). El registro completo, en orden, está en
[`decisiones.md`](decisiones.md); lo que no aparece en la tabla final es detalle revisable. Ejemplos
grabados: [`../examples/transcripts/README.md`](../examples/transcripts/README.md); instalación y
ejecución: [`../README.md`](../README.md).

## Cómo se usa Google ADK

Con google-adk 2.14.1, el `Runner` recibe cada línea de la CLI (`assistant/cli.py`), carga la
sesión de `RedisSessionService`, corre el `App` y guarda cada `Event`; `session.state` lleva
`pending` y `history`. El `App` es un `Workflow` (`root_agent`) de un solo nodo dinámico, `turn`
(`build_root_agent`, `assistant/agent.py`): llama al `LlmAgent` `interpreter` con `ctx.run_node`,
pasa su salida a `handle_turn` (`core/turn.py`, sin ADK) y devuelve un único `Event` con la
respuesta y el delta de estado (D1.1). El `interpreter` no tiene tools: ADK valida su JSON con
`output_schema` (`TurnInterpretation`) y, por `include_contents="none"`, solo ve el mensaje actual y
un resumen de `pending` sin valores. Las tools de negocio no son tools de ADK: una mala lectura da
una pregunta equivocada, nunca una ejecución equivocada (D1). Un solo agente, porque el modelo no
decide la secuencia ([por qué](arquitectura.md#qué-decide-el-llm-y-qué-el-código); línea 589).
Modelo: `gemini-3.5-flash-lite` por Google AI Studio; la evaluación no depende de él (línea 597).
Reintenta un 503 una vez (D1.2). `spikes/spike_d11.py` probó el nodo con modelo falso y Redis real.

## Memoria y estado

El ciclo lo controla estado estructurado, no la conversación (TASK.md líneas 202-229). `pending` es
un solo registro: `operation_id`, `tools`, `status` (`collecting`, `awaiting_confirmation` o
`awaiting_clarification`), `steps` (cada uno con `tool`, los `args` tal como los dijo el usuario y,
en un plan, `depends_on`), `missing`, `invalid`, `last_question` y, al esperar la confirmación,
`confirmation_hash`; una aclaración guarda `candidates` en lugar de `steps`. `history` guarda las
últimas 10 operaciones cerradas (`closed_entry`, `core/history.py`); ninguna regla ni el modelo la
leen (D6). El historial conversacional son los `session.events` de ADK, que el modelo no ve
(`include_contents="none"`, D2): recibe solo `pending_summary` (`core/instruction.py`), sin valores.

| TASK.md | Qué se mantiene | Dónde |
|---|---|---|
| 210 | Intención actual | `tools` y `status` de `pending` |
| 212 | Herramientas pendientes | `tools` y el `tool` de cada paso |
| 214 | Argumentos recopilados | `args` de cada paso |
| 216 | Argumentos faltantes | `missing` y `last_question` |
| 218 | Estado de validación | `invalid`, `status` y `confirmation_hash` |
| 220 | Estado de ejecución | `status` de cada entrada de `history`; claves `idem:` (`started`, `done`) |
| 222 | Resultados | `result` y `outcome` de cada paso de `history` |
| 224 | Dependencias | `depends_on` del paso que depende |

**Por qué Redis** (línea 227): la sesión sigue en otro proceso (`--session`), Redis corre con AOF
(`appendonly`, `docker-compose.yml`), `RedisSessionService` ya viene en ADK y el mismo Redis guarda
las claves de idempotencia fuera de `session.state`, porque el estado solo se escribe al final del
turno (D1.1, D9). **TTL:** una sesión vence 7 días después de su último evento (valor por defecto de
`RedisSessionService` en google-adk 2.14.1; la CLI no lo cambia); una clave `idem:`, a las 24 h.
**Continuidad:** un turno en que el modelo falla no cambia el estado (D1.1). Lo prueban
`test_confirmed_operation_is_gone_after_reload` y `test_history_accumulates_and_is_never_read`.

## Idempotencia, estado incierto y fallos parciales

**Clave.** Cada paso confirmado corre bajo `execution_key` (`core/executions.py`): el sha256 de
sesión + tool + los valores que recibe la tool, con el número del ticket en el paso que lo recibe
(D9.1, D62, D88). `_run_confirmed` (`core/turn.py`), el único punto que llama a una tool, la reserva
antes: en la CLI, `RedisExecutions` (`assistant/executions.py`) hace `SET NX GET EX 86400` con
`started` y luego guarda `done` con el resultado; en los tests, `InMemoryExecutions`, que no vence.
Con la clave ya en `done`, devuelve el resultado guardado sin ejecutar:
«Ya creé el ticket TCK-… con estos mismos datos; no lo repetí.» El duplicado se detecta al
ejecutar, después del "sí": repetir una solicitud ya ejecutada vuelve a mostrar la confirmación (D9).

**Estado incierto** (línea 451). Si la clave está `started` sin `done` (una caída o un timeout a
mitad), si la tool lanza una excepción o si su respuesta no se puede leer, no se sabe si el efecto
ocurrió: el paso se cierra `uncertain`, la clave queda `started` y nada se reintenta; ante la duda,
a lo más una vez (D9.2, D60). Con un proveedor que aceptara claves de idempotencia se reintentaría
con la misma. Cuánto bloquea: [límites](#límites-conocidos). Ningún archivo de `examples/` lo
muestra; lo prueba `test_case_h_uncertain_execution_never_runs` (clave `started`, la tool no corre).
Las otras dos formas del reintento (línea 443) tienen su test: un timeout del modelo antes de ejecutar
(`test_case_h_retry_after_a_model_timeout`) y un "sí" reenviado tras un turno no guardado, que
reutiliza la clave `done` (`test_case_h_resent_yes_after_an_unsaved_turn`).

**Fallo declarado.** Un `success: false` borra la clave y repetir la solicitud la ejecuta de nuevo
(D9.3): «No pude {acción}: la herramienta respondió con un error. Si me lo pides de nuevo, …»

**Fallos parciales** (línea 272). Un plan corta en el primer paso que no termina hecho; los
siguientes no se intentan, la respuesta lo dice y `history` los guarda como `skipped` (D8.1, D89):
«No intenté enviar una notificación porque un paso anterior no se completó.» No hay reintento
automático (D8.3): al repetir la solicitud, el ticket reutiliza su clave `done` y su ID, y solo la
notificación, cuya clave liberó el fallo, corre otra vez (D88; `test_retry_after_step_two_failed`).

## Observabilidad

Cada turno escribe una línea en stderr con el logger `assistant.turn` (`log_turn`,
`assistant/agent.py`), en formato `clave=valor`: `session`, `act`, `ops` (antes del filtro del
registro) y `route` (la rama del núcleo) y, según el turno, `op` (si hay registro), `depends=`,
`results=` (`tool:ID:outcome` por paso intentado), `idem=`, `tool_error=`, `error=`, `invalid=`,
`changes=`, `rejected=` y `ambiguity=`. Las tres últimas pueden llevar texto del usuario (D21);
`invalid=` y `changes=` no nombran la tool y, en un plan, van en el orden de los pasos. Un 503
reintentado que termina bien no deja rastro ([límites](#límites-conocidos); D36). `history`, en
Redis, guarda el desenlace de cada paso. No hay trazas distribuidas ni métricas.

## Preguntas de defensa técnica

### 1. ¿Cómo evita que el LLM invente argumentos faltantes?

Un campo ausente es un faltante: `assessment` (`core/assessment.py`) lo pide por su etiqueta y el
registro no llega a confirmación mientras falte algo; el código solo completa el año y el minuto,
por regla (D5, D17; `test_case_a_end_to_end_through_the_node` pide el título). El modelo copia lo
dicho en el mensaje actual y no ve los valores guardados (D2). Solo los nombres se comprueban contra
el mensaje: `merge_args` (`core/merge.py`) descarta una persona que no está escrita en él (D4 regla
3; `test_rejected_name_is_dropped_alone`); lo demás lo detiene la confirmación ([límites](#límites-conocidos)).
En la transcripción del caso F el modelo puso "revisar el servidor" en el título: eligió el campo de
un texto que el usuario sí dijo, no inventó nada (`examples/transcripts/README.md`).

### 2. ¿Cómo diferencia una intención informativa de una intención de ejecución?

Por `act` (siete valores, `core/interpretation.py`) y por lo que el código hace con él según lo
pendiente (D3; TASK.md línea 276). `question` responde las capacidades del registro sin iniciar nada
ni aplicar nada del mensaje (D20, D67; `test_question_or_other_with_pending_keeps_state`).
`request_action` inicia un flujo que pregunta lo que falta o propone los valores, y nunca ejecuta en
ese turno (D7.1). Solo `confirm` autoriza, y solo una operación en `awaiting_confirmation`: sin otra
tool ni cambios, con `confirms_tool` nulo o de una tool pendiente y el hash de lo mostrado igual
(`_confirm`, `core/turn.py`; D7.2, D7.4). Sin nada pendiente, un "sí" responde
«No hay ninguna operación pendiente.» (`test_case_g_yes_with_nothing_pending`).

### 3. ¿Cómo determina cuándo utilizar una o varias herramientas?

`filter_by_registry` (`core/operations.py`) quita las tools fuera del catálogo (D10) y
`_rules_table` (`core/turn.py`) cuenta las operaciones `explicit` y `plausible`: ninguna,
capacidades (R1); una `explicit`, un flujo (R2); dos o más, un plan (R3); alguna `plausible`,
pregunta sin iniciar nada (R4, R5). Cuenta, no lee palabras clave (D3; línea 109); dos de una misma
tool preguntan cuál primero (D11). El caso E cae en R3 (`test_case_e_through_the_node`); con las dos
lecturas del caso C, el código pregunta (`test_case_c_through_the_node`, `test_fixed_outputs_pass`).
Con los textos vigentes de `core/interpretation.py` el modelo reportó solo la notificación, y dos
mediciones con textos ajustados (revertidos) tampoco: falla la lectura (D52; [límites](#límites-conocidos)).

### 4. ¿Cómo mantiene el estado de una operación pendiente entre mensajes?

En `pending`, dentro de `session.state`: el nodo lo lee al empezar el turno, solo cambia con el
`Event` final y el `Runner` lo guarda en Redis, así que la sesión sigue en otro proceso (D1.1). Cada
mensaje se fusiona con los `args` guardados (`merge_args`, D4) y se valida otra vez (`validate`,
`core/validation.py`). El modelo no ve los valores: recibe un resumen con tools, estado, faltantes y
última pregunta (D2, D14). Un turno en que el modelo falla no cambia el estado (D1.1). Lo prueban
`test_case_b_through_the_node` (el ticket se completa en tres mensajes y se crea con el "sí") y
`test_confirmed_operation_is_gone_after_reload`. Detalle en [Memoria y estado](#memoria-y-estado).

### 5. ¿Cómo resuelve contradicciones entre argumentos previamente recopilados?

Gana el último valor, entero, y la respuesta lo muestra («Cambié la hora: 25:90 → 10:30.»;
`overwrite`, D4 reglas 5 y 9); lo ausente nunca borra lo guardado (`merge_args`, `core/merge.py`).
Un turno con un cambio nunca ejecuta: si todo sigue válido, vuelve a mostrar la confirmación con un
hash nuevo y pide otro "sí" (D7, D47; `test_correction_while_awaiting`). "No, asígnalo a Ana" es una
corrección, no un descarte (D7.3; `test_reject_with_args_is_a_correction`). Dos formas de fecha que
no dan el mismo día son un error de coherencia y ninguna gana (D5.1c), y el valor corregido se valida
de nuevo (`test_case_d_correction_is_validated_again`).

### 6. ¿Cómo evita ejecutar dos veces una misma operación?

Con la operación pendiente, repetir la solicitud no cambia nada: la fusión no da cambios y se repite
lo pendiente (D9, D40; `test_case_h_repeated_while_pending`). Al ejecutar, cada paso reserva su
clave de idempotencia (sesión + tool + valores, `execution_key`, `core/executions.py`): ya en `done`,
devuelve el ticket guardado sin crear otro (`test_case_h_through_the_node`), también ante un "sí"
reenviado desde otro proceso (`test_resent_yes_survives_a_new_process`). Tras ejecutar, `pending` se
vacía, así que otro "sí" no encuentra nada. Lo que no se sabe si terminó no se repite (D9.2), y solo
los tests lo muestran. Detalle en [Idempotencia](#idempotencia-estado-incierto-y-fallos-parciales).

### 7. ¿Cómo maneja fallos parciales cuando se ejecutan varias herramientas?

`_run_confirmed` (`core/turn.py`) corre los pasos del plan en orden y corta en el primero que no
termina hecho (D8.1, D89): si falla el ticket, la notificación no se intenta y la respuesta lo dice
(`test_case_e_first_step_fails_through_the_node`); si falla la notificación, el ticket queda creado y
se informan los dos (`test_case_e_second_step_fails_through_the_node`). No hay reintento automático
(D8.3): un fallo declarado libera su clave (D9.3), así que repetir la solicitud reutiliza el ticket
con su ID y vuelve a intentar solo la notificación (D88; `test_retry_after_step_two_failed`). El plan
cerrado queda en `history` con el desenlace de cada paso. Detalle en
[Idempotencia](#idempotencia-estado-incierto-y-fallos-parciales).

### 8. ¿Qué responsabilidades corresponden al LLM y cuáles al código determinístico?

El LLM solo llena `TurnInterpretation`: el acto, las tools con su certeza, los argumentos tal como
se dijeron, `depends_on` y `confirms_tool`. El código filtra, cuenta, fusiona, valida, pregunta,
confirma, ordena, ejecuta y responde con plantillas (`core/replies.py`): el modelo nunca redacta la
respuesta, y los valores que extrajo aparecen dentro de plantillas para que el usuario los confirme
([tabla](arquitectura.md#qué-decide-el-llm-y-qué-el-código)). El núcleo no importa ADK
(`test_core_imports_no_adk`); `ambiguity_reason` no decide nada (`test_ambiguity_reason_changes_no_decision`).
En las transcripciones de C y F el código acota la lectura del modelo: rechazó "Solo avísale" como
canal, mostró el texto de F en el título y nada corrió sin un "sí" (D52, D93).

### 9. ¿Por qué seleccionó ese mecanismo de memoria?

Separa el estado que decide (`pending`, `history`) de la conversación y usa lo que ADK ya trae:
`RedisSessionService` guarda `session.state` en Redis con AOF, que sobrevive a un reinicio y deja
seguir la sesión desde otro proceso (`assistant/cli.py`). Una memoria solo en el proceso se pierde al
reiniciar, y el mismo Redis guarda las claves de idempotencia, que no pueden esperar al final del
turno (D9). Para los cierres se descartaron borrar el registro y dejar solo el log (TASK.md pide los
resultados, líneas 220-222) y una clave aparte por sesión, otro almacén para datos que ninguna regla
lee (D6). Lo prueban `test_confirmed_operation_is_gone_after_reload` y
`test_resent_yes_survives_a_new_process`. Detalle en [Memoria y estado](#memoria-y-estado).

### 10. ¿Cómo escalaría la solución para soportar decenas o cientos de herramientas?

Una tool nueva es un `ToolSpec` en `TOOLS` (`core/registry.py`): sus campos con su tipo, su contrato
Pydantic, su mock y sus frases en español. La fusión y la validación se declaran por tipo de campo
(`test_rules_are_declared_per_kind`, `test_every_kind_has_a_rule`), la tabla R1–R5 cuenta
operaciones, el nombre de tool es una cadena libre (D10), la instrucción y `ToolArgs` se generan del
catálogo (`build_instruction`, `build_args_model`) y un enlace de ID se declara con `IdLink` (D25)
y debe pasar `test_declared_links_never_loop`. Pero la instrucción lista todo el catálogo y `ToolArgs`
tiene todos sus campos: con cientos de tools haría falta preseleccionar las candidatas por turno
antes de llamar al intérprete ([límites](#límites-conocidos)).

## Límites conocidos

Cada límite vive solo aquí; las respuestas de arriba enlazan a esta lista.

- Con `gemini-3.5-flash-lite`, el mensaje del caso C llega solo como notificación: el agente pide el canal y nunca ofrece la reunión (D52).
- Un cambio de intención descarta todo lo pendiente, también lo de una tool que se repite: "mejor crea un ticket y avísale a Carlos" con una notificación pendiente la empieza de cero (D3.1).
- "Sí, pero por SMS" devuelto como `confirm` sin argumentos ejecuta los valores mostrados; el código no ve el "pero" (D7.3, D4).
- Solo los nombres se anclan al mensaje: un título, un canal o un número mal convertido ("media hora" como 60) solo los detiene la confirmación (D4, D7.5).
- La clave de idempotencia lleva la sesión, no el usuario: dos tickets idénticos en 24 h en la misma sesión son uno, y en dos sesiones son dos (D9.1, D62).
- Un `uncertain` bloquea esa misma solicitud 24 h; para intentarla otra vez hay que cambiar algún valor (D9.2, D62).
- Tras un timeout del paso 2 de un plan, la notificación de ese ticket no sale en 24 h aunque se repita la solicitud (D88).
- Si el modelo invierte `depends_on` (el ticket dependiendo de la notificación), ese orden manda; se ve en la confirmación antes del "sí" (D84).
- Con un plan pendiente, "No, la notificación no" llega como `reject` sin argumentos y descarta el plan entero; nada se ejecuta (D87).
- Con un ticket pendiente, dos `create_ticket` con campos disjuntos en un mismo mensaje se unen en el pendiente (D54).
- Una notificación no relacionada en un plan con un ticket recibe el número del ticket; se ve en la confirmación, antes del "sí" (D8.4).
- Un 429 real no se observó (D28); el simulado responde «El modelo no está disponible ahora; no cambié nada.» sin tocar el estado, y el intérprete reintenta un 503 (y un fallo de conexión) una vez, sin dejar rastro en el log (D1.2, D36).
- Con cientos de tools, la instrucción lista todo el catálogo y `ToolArgs` es un solo modelo con todos los campos (D2, D10).
- Una fecha sin año que ya pasó se lee como la del año siguiente, y "hoy" es la hora local de la máquina, sin zona horaria (D56).
- "Mañana" visto al día siguiente en un turno que repite lo pendiente muestra la fecha nueva sin la línea de D55, y "el viernes 27" vuelve a pedir la fecha sin decir que falta el mes (D55, D56).

## Decisiones clave

| Decisión | Qué decide | Por qué |
|---|---|---|
| D1 | El LLM solo interpreta; el código fusiona, valida, confirma, ordena, ejecuta y responde. | Validación determinística sin depender del prompt (TASK.md líneas 166-168 y 587). |
| D1.1 | Un nodo dinámico de ADK aloja el turno; el estado solo se escribe con el `Event` final. | Un turno fallido no deja estado a medias: la continuidad que pide la línea 227. |
| D1.2 | El intérprete reintenta un 503 una vez; el 429 y las tools, nunca. | El intérprete no tiene efectos, así que repetirlo no duplica nada (líneas 286 y 580). |
| D2 | El intérprete devuelve acto, tools con certeza y argumentos tal como se dijeron, sin ver los valores guardados. | El LLM no garantiza la validez (línea 168) ni completa datos (línea 184). |
| D3 | Tabla R1–R5 por conteo de operaciones `explicit` y `plausible`; aclaración en R4 y R5. | Aclarar ante la ambigüedad (líneas 137 y 355) sin palabras clave (línea 109). |
| D3.1 | Un cambio de intención archiva lo pendiente y empieza de cero. | No mezclar argumentos de dos operaciones (línea 413). |
| D4 | Gana el último valor, lo ausente no borra y los nombres se anclan al mensaje. | Conservar y actualizar lo recopilado (línea 145) sin inventar (línea 184). |
| D5 | Validación por tipo de campo, con fecha y hora contra el momento del turno. | Presencia, tipos, valores, formatos, negocio y coherencia (líneas 151-168). |
| D6 | Los cierres van a `history` (últimos 10), que ninguna regla lee. | Mantener el estado de ejecución y los resultados (líneas 220-222). |
| D7 | Toda operación y todo plan se confirman; el hash ata el "sí" a lo mostrado. | Autorización ligada a la operación pendiente (líneas 276-284). |
| D7.4 | Un "sí" que nombra otra tool no ejecuta nada. | Un "sí" no autoriza otras herramientas (línea 284). |
| D8 | Plan lineal que corta en el primer fallo, sin reintento automático. | Orden de ejecución y no ejecutar la segunda si falla la primera (líneas 252 y 270). |
| D8.4 | Sin `depends_on` del modelo, el registro infiere la dependencia. | Transferir el `ticket_id` entre herramientas (línea 390). |
| D9 | Clave de sesión + tool + valores, `started`/`done` por 24 h; lo incierto no se repite. | Evitar duplicados y explicar el estado incierto (líneas 286 y 451). |
| D9.3 | Un `success: false` libera la clave. | Reintentar un fallo parcial repitiendo la solicitud (líneas 270-272). |
| D10 | El nombre de tool es una cadena libre que filtra el registro, con aviso. | El nombre de tool no es un enum que haya que regenerar con el catálogo, y una petición fuera de él no se fuerza a una tool cercana (líneas 562 y 128-132). |

El resto de [`decisiones.md`](decisiones.md) es detalle revisable; una entrada "autodecidida" la
eligió el orquestador dentro de TASK.md y las decisiones del autor, que puede revertirla.
