# Transcripciones con el modelo real

Cada archivo `caso_x.txt` es la salida estándar (stdout) de la CLI, sin editar, al mandarle las
líneas de `examples/inputs/caso_x.txt` con el modelo real. Las líneas que empiezan con `> ` son los
mensajes del usuario (el eco de la CLI con stdin por tubería, D24); el resto son las respuestas del
agente. Los casos son los de `docs/TASK.md` §4 (líneas 288-451) y los mismos mensajes que prueban
los tests de `tests/test_cases.py`.

## Corrida

- **Fecha:** 2026-11-17, de 18:25 a 18:31, hora local (CST).
- **Commit:** `f6afe1d` (rama `impl/poc`), sin cambios de código.
- **Modelo:** `gemini-3.5-flash-lite` (`assistant/interpreter.py`), por Google AI Studio.
- **Estado:** Redis local (`docker compose up -d redis`), una sesión nueva por archivo (D24), sin
  `MOCK_FAIL`.
- **Cómo se mandaron las líneas:** una a la vez, esperando la respuesta de cada turno y 13 s entre
  turnos (el ritmo de la capa gratuita que se usó en la fase 0). El envío se habría detenido tras
  un turno con "El modelo no está disponible ahora…" o "No te entendí…"; no pasó.
- **Para reproducirlo:** el stdout es el mismo que con

  ```bash
  uv run python -m assistant.cli < examples/inputs/caso_x.txt
  ```

  un archivo a la vez. Los IDs (`TCK-`, `NTF-` + 8 hex) cambian en cada corrida, y la lectura del
  modelo puede variar.

## Llamadas

Una línea enviada es un turno y una llamada al intérprete (D1.1). Se contaron en el log del turno
(stderr) y coinciden con las líneas `> ` de cada transcripción.

| Archivo | Líneas | Llamadas | Acumulado |
|---|---|---|---|
| `caso_a.txt` | 1 | 1 | 1 |
| `caso_b.txt` | 4 | 4 | 5 |
| `caso_c.txt` | 4 | 4 | 9 |
| `caso_d.txt` | 2 | 2 | 11 |
| `caso_e.txt` | 3 | 3 | 14 |
| `caso_f.txt` | 2 | 2 | 16 |
| `caso_g.txt` | 2 | 2 | 18 |
| `caso_h.txt` | 5 | 5 | 23 |

Total: **23 de 30** llamadas aprobadas. Peticiones HTTP: **≤ 2 × llamadas** (≤ 46), porque el
intérprete reintenta un 503 una vez (D1.2) y un reintento que termina bien no deja rastro en la CLI
(D36). Ningún turno respondió "modelo no disponible" ni "no te entendí", y el stderr no tuvo
ninguna traza de error de ADK. Sin paradas.

## Por archivo

El extracto del log lleva solo `act`, `ops`, `route` y, si están, `depends`, `results` (con los IDs
abreviados), `invalid` (campo y causa, sin valores) y si hubo `rejected` y `ambiguity`; nunca
valores del usuario ni ids de sesión u operación. "Igual al test" quiere decir: la misma respuesta,
línea por línea, que da el test del caso con las interpretaciones fijas de
`tests/interpretations.py` (`FIXED`) por el mismo bucle de la CLI, salvo los IDs y las fechas que
dependen del día. En ningún turno hubo `rejected` ni `ambiguity`.

### Caso A (`caso_a.txt`; TASK.md líneas 292-308)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Necesito crear un ticket porque el servidor está caído | `act=request_action ops=create_ticket:explicit route=R2` | Igual al test (`test_case_a_end_to_end_through_the_node`). |

Ejecuciones: ninguna (`EXAMPLE_RUNS`: ninguna).

### Caso B (`caso_b.txt`; líneas 311-335)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Crea un ticket para revisar el problema de autenticación | `act=request_action ops=create_ticket:explicit route=R2` | Igual al test (`test_case_b_through_the_node`). |
| 2 | Alta, asígnalo a Carlos | `act=provide_info ops=create_ticket:explicit route=same_tool` | Igual al test. |
| 3 | El título es Falla de autenticación | `act=provide_info ops=create_ticket:explicit route=same_tool` | Igual al test. |
| 4 | Sí, créalo | `act=confirm ops= route=confirmed results=create_ticket:TCK-…:ran` | Igual al test. |

Ejecuciones: "Listo: creé el ticket TCK-606E965B." tras el "sí" (`EXAMPLE_RUNS`: un
`create_ticket`).

### Caso C (`caso_c.txt`; líneas 338-355)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Avísale a Carlos que mañana revisamos el servidor | `act=request_action ops=send_notification:explicit route=R2` | Difiere: el modelo devolvió solo `send_notification`, sin `schedule_meeting` `plausible`, así que el turno cae en R2 y pide el canal en vez de preguntar "¿Quieres enviar una notificación, programar una reunión, o ambas?" (`test_case_c_through_the_node`, ruta `R4`). Lectura del modelo, la limitación de D52. |
| 2 | Solo avísale | `act=provide_info ops=send_notification:explicit route=same_tool invalid=channel:not_allowed` | Difiere: el test resuelve aquí la aclaración (regla 2) y anota destinatario y mensaje; con la notificación ya pendiente, el modelo copió "Solo avísale" como canal. El validador lo rechaza (`not_allowed`, D44), la respuesta da los valores permitidos (D43) y vuelve a pedir el canal. Lectura del modelo; ver *Diferencias no previstas*. |
| 3 | Por correo | `act=provide_info ops=send_notification:explicit route=same_tool` | Difiere en una línea: "Cambié el canal: Solo avísale → correo." en lugar de "Anoté el canal: correo." (el último valor reemplaza al anterior, D4). La confirmación es igual al test. |
| 4 | Sí, hazlo | `act=confirm ops= route=confirmed results=send_notification:NTF-…:ran` | Igual al test. |

Ejecuciones: "Listo: envié la notificación NTF-2A3DF8AC." tras la confirmación y el "sí"
(`EXAMPLE_RUNS`: un `send_notification`). Ninguna reunión.

### Caso D (`caso_d.txt`; líneas 357-371)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Agenda una reunión el 40 de noviembre a las 25:90 | `act=request_action ops=schedule_meeting:explicit route=R2 invalid=date:nonexistent,time:bad_format` | Igual al test (`test_case_d_through_the_node`). |
| 2 | El 20 de noviembre a las 10:30 | `act=provide_info ops=schedule_meeting:explicit route=same_tool` | Igual al test (`test_case_d_correction_is_validated_again`). La corrida fue el 2026-11-17, el mismo día que el `NOW` de los tests, así que "20 de noviembre" es el 2026-11-20 en los dos (D56); la respuesta muestra día y mes, no la fecha ISO. |

Ejecuciones: ninguna (`EXAMPLE_RUNS`: ninguna).

### Caso E (`caso_e.txt`; líneas 374-392)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Crea un ticket de prioridad alta para Carlos y mándale una notificación por email cuando esté creado | `act=request_action ops=create_ticket:explicit,send_notification:explicit route=R3 depends=send_notification<create_ticket` | Igual al test (`test_case_e_through_the_node`). |
| 2 | El título es Servidor caído, la descripción es que el servidor de producción no responde, y el mensaje: ya se registró la falla | `act=provide_info ops=create_ticket:explicit,send_notification:explicit route=same_tool` | Igual al test. |
| 3 | Sí, adelante | `act=confirm ops= route=confirmed results=create_ticket:TCK-…:ran,send_notification:NTF-…:ran` | Igual al test. |

Ejecuciones: "Listo: creé el ticket TCK-41C53663." y "Listo: envié la notificación
NTF-137B22A3." tras el "sí" (`EXAMPLE_RUNS`: un `create_ticket` y un `send_notification`).
**El número del ticket** (línea 390; D90), leído de `history` en la sesión de Redis después de la
corrida: una entrada `completed` con `create_ticket` `ran` y `send_notification` `ran`, y el
`message` que recibió la notificación es "ya se registró la falla (ticket TCK-41C53663)", el mismo
ID que la línea "Listo: creé el ticket …".

### Caso F (`caso_f.txt`; líneas 395-417)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Agenda una reunión con Juan | `act=request_action ops=schedule_meeting:explicit route=R2` | Igual al test (`test_case_f_through_the_node`). |
| 2 | Mejor crea un ticket para revisar el servidor | `act=reject ops=create_ticket:explicit route=other_tool:R2` | Difiere: el modelo puso "revisar el servidor" en el título y no en la descripción, así que la respuesta dice "Anoté el título: revisar el servidor." y pide "la descripción, la prioridad y a quién se asigna" (el test: "Anoté la descripción…" y pide el título). Además, `act=reject` en lugar de `request_action`; con otra tool, `reject` también es cambio de intención (D3.1, D3.3) y la ruta es la misma (`test_reject_or_cancel_with_another_tool_is_a_change_of_intention`, `tests/test_turn.py`). Lectura del modelo; ver *Diferencias no previstas*. |

Ejecuciones: ninguna (`EXAMPLE_RUNS`: ninguna).

### Caso G (`caso_g.txt`; líneas 420-438)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Envía un correo a Carlos para informarle que el ticket quedó resuelto | `act=request_action ops=send_notification:explicit route=R2` | Igual al test (`test_case_g_through_the_node`). |
| 2 | Sí, hazlo | `act=confirm ops= route=confirmed results=send_notification:NTF-…:ran` | Igual al test. |

Ejecuciones: "Listo: envié la notificación NTF-E145B885." tras el "sí" (`EXAMPLE_RUNS`: un
`send_notification`).

### Caso H (`caso_h.txt`; líneas 441-451)

| Turno | Mensaje | Extracto del log | Frente al test |
|---|---|---|---|
| 1 | Crea un ticket titulado Servidor caído: el servidor de producción no responde, prioridad alta, asígnalo a Carlos | `act=request_action ops=create_ticket:explicit route=R2` | Igual al test (`test_case_h_repeated_while_pending`). |
| 2 | (el mismo mensaje) | `act=request_action ops=create_ticket:explicit route=same_tool` | Igual al test: la confirmación otra vez, sin ninguna línea "Anoté…". |
| 3 | Sí, créalo | `act=confirm ops= route=confirmed results=create_ticket:TCK-…:ran` | Igual al test (`test_case_h_through_the_node`). |
| 4 | (el mismo mensaje) | `act=request_action ops=create_ticket:explicit route=R2` | Igual al test. |
| 5 | Sí, créalo | `act=confirm ops= route=confirmed results=create_ticket:TCK-…:reused` | Igual al test: "Ya creé el ticket TCK-E129F310 con estos mismos datos; no lo repetí." |

Ejecuciones: "Listo: creé el ticket TCK-E129F310." tras el primer "sí"; el segundo "sí" reutiliza
ese ticket y no ejecuta nada (`EXAMPLE_RUNS`: un `create_ticket`).

En todos los archivos, cada "Listo: …" y "Ya creé …" viene después de una confirmación y de un
"sí" del usuario, y las ejecuciones coinciden con `EXAMPLE_RUNS` de `tests/test_cases.py`.

## Diferencias conocidas

- **B** (líneas 317-319 y 327-335; D17, D7.1): **confirmada.** La primera pregunta pide
  también el título ("el título, la prioridad y a quién se asigna"), no solo "¿Qué prioridad
  tendrá y a quién se lo asignamos?", y el caso termina con "Sí, créalo". El modelo no sacó un
  título de `B1`, así que el tercer turno anota el título (no lo sobrescribe).
- **C** (líneas 344-355; D52): **confirmada en parte.** `C1` cayó en R2 y pidió el
  canal, "Por correo" llevó a la confirmación y el "sí" envió una sola notificación: no ejecuta
  varias tools por asociación (línea 353) ni nada sin confirmación (D7.1), pero no pide la
  aclaración (línea 355); la reunión nunca se ofrece. **No confirmada:** "Solo avísale" no repitió
  la pregunta sin cambios (D40); el modelo lo copió como canal y el validador lo rechazó (ver
  abajo). El modelo no aclaró esta vez, así que no hay nada que comparar con la pregunta de
  `test_case_c_through_the_node`.
- **D** (líneas 198 y 365-371; D56, D92): **confirmada.** Las frases "La fecha indicada no
  existe." y "La hora está fuera del formato válido." salen sin la cabecera ni la pregunta de la
  línea 198; la corrección se leyó contra la fecha de la corrida (2026-11-17, la misma del `NOW`
  de los tests) y la reunión no se ejecuta: faltan el título, la duración y los asistentes.
- **E** (líneas 382-392; D84, D91 decisión F, D90): **confirmada.** `depends=send_notification<create_ticket`
  (el `depends_on` lo dio el modelo; no hizo falta inferirlo), la confirmación lleva "El mensaje
  llevará al final el número de ticket." y el "sí" creó el ticket y después la notificación, con el
  número al final de su mensaje (leído de `history`, arriba).
- **F** (líneas 401-417; D17): **confirmada.** La pregunta tras "Agenda una reunión con
  Juan" nombra el título, la fecha, la hora y la duración, no solo "¿Qué día y a qué hora?"
  (línea 403), y el segundo turno empieza con "No voy a programar una reunión; descarté lo que
  tenía pendiente." (D64). El nuevo ticket no hereda nada de la reunión (línea 413).
- **G** (líneas 432-438): **confirmada.** Confirmación sin volver a pedir datos y una sola
  notificación tras el "sí".
- **H** (líneas 447-451; D40, D62, D88): **confirmada.** El segundo mensaje igual, con el ticket
  pendiente, no cambió nada; el modelo devolvió los mismos valores normalizados en el primer y el
  cuarto mensaje, así que el quinto turno responde "Ya creé el ticket … con estos mismos datos; no
  lo repetí." (`results=create_ticket:TCK-…:reused`). Ningún archivo muestra el timeout ni el
  estado incierto (línea 451): están en `test_case_h_retry_after_a_model_timeout`,
  `test_case_h_resent_yes_after_an_unsaved_turn` y `test_case_h_uncertain_execution_never_runs`.

## Diferencias no previstas

Dos, las dos de lectura del modelo; ninguna contradice TASK.md ni llevó a una ejecución sin
confirmar:

1. **C, turno 2 ("Solo avísale").** Con la notificación ya pendiente (por D52), el modelo devolvió
   `provide_info` con `channel` = "Solo avísale" en lugar de una operación sin argumentos. El
   modelo copió el texto del usuario, no inventó un canal; el validador determinístico lo rechazó
   como valor no permitido (`invalid=channel:not_allowed`; RF-04, líneas 147-168) y la respuesta
   lo dice ("No reconozco el canal que indicaste; puede ser correo, SMS o push.") y vuelve a pedir
   el canal (RF-06, líneas 186-188). El turno siguiente lo reemplazó por "correo" (D4: gana el
   último valor) y solo el "sí" ejecutó.
2. **F, turno 2 ("Mejor crea un ticket para revisar el servidor").** El modelo puso "revisar el
   servidor" en `title` (las interpretaciones fijas, en `description`) y marcó el acto como
   `reject`. El valor es texto del usuario, no inventado; el cambio de intención se detectó igual
   (`route=other_tool:R2`), la reunión se descartó sin ejecutarse y el ticket nuevo no lleva nada
   de ella (líneas 411-417). El agente pide los campos que faltan para esa lectura.

## Lo que no se grabó

- `MOCK_FAIL` con el modelo real (D91, *Pendiente*): el fallo parcial del caso E se prueba con el
  modelo falso en `test_case_e_second_step_fails_through_the_node` y
  `test_case_e_first_step_fails_through_the_node`.
- Otros modelos.

## Secretos

Sobre las ocho transcripciones, esta nota y los ocho stderr de la corrida (que no se versionan):

- `grep -cE 'AIza[0-9A-Za-z_-]{20,}|[?&]key=' <archivo>` → `0` en cada uno.
- `uv run python -c "import sys; from dotenv import dotenv_values as d; k=d('.env')['GOOGLE_API_KEY']; print(any(k in open(p,encoding='utf-8').read() for p in sys.argv[1:]))" <archivos>`
  → `False`.
- `grep -c` del prefijo de las carpetas de usuario de macOS sobre `examples/transcripts/*` (sin
  rutas locales) → `0` en cada archivo.
