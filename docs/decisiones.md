# Decisiones de diseño

Registro de las decisiones de arquitectura del PoC, en el orden en que se tomaron. Las
referencias a `TASK.md` y sus números de línea apuntan al enunciado de la prueba técnica, que
no forma parte del repositorio.

**D1 · Control del ciclo de ejecución: opción B** (2026-11-16)

- **Decisión:** el LLM solo interpreta (`LlmAgent` con `output_schema`, sin acceso a las tools
  de negocio): acto de diálogo, tools candidatas y argumentos mencionados. El código
  determinístico fusiona argumentos, valida, detecta faltantes, asocia confirmaciones, ordena
  dependencias, aplica idempotencia y ejecuta.
- **Motivo:** el enunciado exige validación determinística y control explícito del ciclo sin
  depender del prompt (TASK.md líneas 166-168, 568-570 y 587). Una interpretación errónea puede
  producir una pregunta equivocada, nunca una ejecución equivocada.
- **Alcance mínimo (≈20–22 h):** contratos Pydantic y validador por campo; una operación
  pendiente en `session.state` con reductor; respuestas con plantillas; plan lineal de dos pasos
  para el caso E; confirmación ligada a `operation_id` + hash de argumentos; clave de
  idempotencia por ejecución; tests A–H con interpretaciones fijas y una corrida grabada con el
  modelo real.
- **Repliegue:** si una prueba temprana muestra que el modelo no llena el esquema de forma
  fiable (casos A, C y G), pasar al híbrido (`LlmAgent` con una sola tool `report_request`), que
  reutiliza el código determinístico. Si quedan menos de ~15 h sin un turno completo
  funcionando, opción A con el mismo validador.
- **Descartadas:** A (el modelo conduce la secuencia; los callbacks solo vetan) y C (sin
  justificación técnica con tres tools, TASK.md línea 589).
- **Resuelto en D1.1:** B1 `BaseAgent` propio vs B2 `Workflow`. Se adopta un nodo dinámico
  sobre el runtime de nodos, con el spike superado y B1 como repliegue. B1 (como anfitrión
  principal) y B2 estático quedan descartadas; los motivos están en D1.1.

**D2 · Frontera LLM/código: esquema del intérprete S2′** (2026-11-16)

- **Decisión:** el intérprete (`LlmAgent` con `output_schema=TurnInterpretation`) devuelve lo
  que el usuario dijo, nunca lo que es válido: `act` (enum), `operations` (lista de
  `{tool, certainty: explicit|plausible, depends_on, args}`), `confirms_tool` (solo veto; nulo
  no bloquea) y `ambiguity_reason`. `args` es un modelo compartido con todos los campos
  opcionales y sin restricciones de rango; `priority` y `channel` llegan como la palabra del
  usuario ("alta", "correo"); fechas y horas como componentes (`day`, `month`, `year`,
  `relative_days`, `weekday` con la palabra del usuario; `hour`, `minute`). Enums solo donde
  el modelo clasifica. Ausente = no dicho en este mensaje. Cada campo de tipo lista llega en
  tres cubetas opcionales: `attendees` (la lista que el usuario dio; reemplaza),
  `attendees_add` (nombres que pidió agregar) y `attendees_remove` (nombres que pidió
  quitar). Sin las dos últimas, el campo se comporta como antes de la enmienda. En
  `provide_info`, los argumentos nuevos van en una operación de la tool pendiente y el código
  ignora su `certainty`. Esto cubre solo los argumentos de una tool ya pendiente; en la
  respuesta a una aclaración, `certainty` sí cuenta (D3). Los valores de `act` están fijados
  en D3.
- **Motivo:** el modelo transcribe los valores y solo clasifica la estructura (`act`, `tool`,
  `certainty`, `depends_on`) o convierte lenguaje a números (`month`, `relative_days`). Así un
  valor inválido o no dicho llega al validador en lugar de corregirse en silencio, y
  "urgente" se rechaza en vez de convertirse en un `high` válido (TASK.md líneas 166-168, 184
  y 504).
- **Enmienda (2026-11-16) · operaciones sobre listas:** cada campo de tipo lista gana dos
  cubetas, `<campo>_add` y `<campo>_remove`; el campo original conserva el significado de
  reemplazo. Las cubetas se declaran por tipo de campo en el registro, no por tool: todo
  campo lista las recibe (TASK.md línea 562).
  - *Motivo:* con una reunión con Juan pendiente, "agrega a Ana" y "con Ana" llegaban ambos
    como `attendees=["Ana"]`, y "quita a Juan" no podía expresarse (podía llegar como
    `attendees=["Juan"]`, lo contrario de lo pedido). El verbo se perdía en el intérprete,
    así que cualquier regla de fusión de D4 era una apuesta (TASK.md líneas 145 y 557). El
    modelo no puede enviar la lista completa porque no ve los valores guardados
    (`include_contents='none'`; `{pending?}` sin valores), ni vaciar un campo con nulo,
    porque ADK descarta los nulos (`model_dump(exclude_none=True)`,
    `utils/_schema_utils.py:260`, google-adk 2.14.1). Decidir si el usuario agrega, quita o
    reemplaza es una clasificación, que D2 ya asigna al modelo; aquí la expresa al elegir
    la cubeta, sin un enum nuevo.
  - *Compatibilidad:* una interpretación sin cubetas significa lo mismo que antes; los
    tests escritos solo con `attendees` siguen siendo válidos. Las cubetas son listas de
    cadenas opcionales, como `attendees`: no añaden construcciones nuevas al esquema.
  - *Descartadas:* reemplazar siempre y avisar del cambio, como limitación documentada (no
    cubre "quita a Juan"); una verificación de palabras clave en código ("agrega",
    "también"), que repite la regla por verbo descartada en D3 (P3) y contradice TASK.md
    línea 109; un enum por campo (`attendees_op: replace|add|remove`), porque un mensaje
    solo lleva una operación y "quita a Juan y agrega a Ana" no cabe; y una lista de
    `{op, name}`, que cambia el tipo de `attendees`, invalida los tests escritos con una
    lista de cadenas y añade una clasificación por elemento.
  - *Entradas para D4 (D4 las decide):* quitar un nombre que no está guardado; un
    `_remove` que vacía la lista (el campo pasa a faltante); agregar un nombre que ya está
    (sin duplicar, sobre nombres normalizados); el anclaje aplica a cada nombre de cada
    cubeta; y un turno que trae `attendees` junto con alguna cubeta, o el mismo nombre en
    `_add` y `_remove`.
- **Contexto y respuestas:** `include_contents='none'` + resumen `{pending?}` (tools
  pendientes, estado, campos faltantes y última pregunta; sin valores recopilados).
  Respuestas solo con plantillas: una llamada al LLM por turno. Temperatura y nivel de
  thinking por defecto; sin `max_output_tokens` ajustado.
- **Código que protege la frontera:** `ValidationError` o salida vacía → plantilla "no te
  entendí" sin tocar el estado; filtro por contrato de la tool; nombres anclados al mensaje
  actual, sin distinguir mayúsculas ni acentos (el anclaje está decidido; si es duro o blando
  se define en D4); tabla de sinónimos en español e inglés para `priority` y `channel`.
- **Solo para logs:** `ambiguity_reason` se registra y ninguna decisión lo lee; un test lo
  demuestra. Que el código llegue a usarlo requiere una decisión nueva.
- **Umbral de fiabilidad (fase 0):** modelo base `gemini-3.5-flash-lite`. 23 escenarios (A,
  B, C, D, E, F, G, X —un valor de enum no permitido, como "urgente"—, pregunta informativa,
  día de la semana, el contraste del caso C —"Avísale a Carlos que el servidor ya está
  disponible", TASK.md línea 117—, el primer mensaje del caso G —TASK.md línea 424—,
  "Necesito reportar que el servidor está caído" —TASK.md línea 113—, tres respuestas a una
  aclaración: "las dos", "solo avísale" y "no sé, lo que sea", y tres sondas sobre una
  aclaración de dos candidatas: "sí", "No, la reunión no" y el mensaje del caso C repetido,
  y cuatro de lista con una reunión con Juan pendiente: "agrega a Ana", "quita a Juan",
  "con Ana" y "quita a Juan y agrega a Ana")
  × 3 redacciones × 3 corridas = 207 llamadas solo al intérprete (prueba de humo: 23). En B,
  F, G, las tres respuestas, las tres sondas y los cuatro de lista se evalúa el segundo
  mensaje con un `{pending?}` escrito a mano. Se juzga por lo que haría el código, no por el
  JSON exacto (C pasa si el código preguntaría, la pregunta lista `send_notification` y
  `schedule_meeting`, y nada se ejecuta).
  - *Con P1 (D3):* una operación `plausible` de más en un escenario claro es un desacierto,
    porque el código preguntaría; nunca es un fallo crítico. Cuenta además para el disparador
    de D3. Cada escenario claro (lista en D3) debe llegar a R2 o R3 en ≥ 7/9.
  - *Respuestas a una aclaración:* se espera que "las dos" resuelva a ambas candidatas, "solo
    avísale" solo a `send_notification` y "no sé, lo que sea" a ninguna. Cada una pasa con
    ≥ 7/9. Fallo crítico aquí: se habría iniciado una tool que el usuario no eligió.
  - *Sondas:* se espera que "sí" y el mensaje repetido no inicien nada (respuesta no clara) y
    que "No, la reunión no" nunca inicie `schedule_meeting`. Cada una pasa con ≥ 7/9, con el
    mismo fallo crítico. Se anota además cuántas veces el modelo devuelve operaciones con
    `reject` o `cancel`, y cuántas "sí" llega con las dos tools `explicit`.
  - *Listas:* se espera "agrega a Ana" → Ana solo en `attendees_add`; "quita a Juan" →
    Juan solo en `attendees_remove`; "con Ana" → Ana solo en `attendees`; y "quita a Juan
    y agrega a Ana" → Juan en `attendees_remove` y Ana en `attendees_add`. Cada uno pasa
    con ≥ 7/9. Fallo crítico: un nombre que el usuario pidió quitar llega en `attendees` o
    en `attendees_add`. Cualquier otra cubeta equivocada es un desacierto.
  - *Fallo de forma:* `ValidationError`, salida vacía, o un objeto anidado (`args`, `date`,
    `time`) vacío cuando el mensaje sí traía ese dato. *Fallo crítico:* el código habría
    ejecutado algo o aceptado un valor no dicho. *Desacierto:* cualquier otro resultado
    distinto del esperado.
  - Se sigue adelante con menos de 3/207 fallos de forma, ≥ 85 % de aciertos, A, C y G en
    ≥ 7/9, cada escenario claro en ≥ 7/9 y ningún escenario con 2 o más fallos críticos.
  - Un escenario con ≥ 2/9 fallos críticos → mejorar descripciones (≤ 2 h) y, si persiste,
    probar `gemini-3.8-flash` (confirmar antes que su cuota gratuita alcanza); si aun así
    falla, se documenta como limitación.
  - Más de 10 % de `priority`/`channel` no mapeables, contando solo los turnos en que el
    usuario dio un valor válido (X queda fuera) → enum con `other` + texto crudo.
  - Son umbrales para detectar pronto un esquema roto, no una medida estadística. Revisar los
    límites reales en `https://aistudio.google.com/rate-limit` antes de la corrida completa.
- **Precisa el repliegue de D1:** el híbrido (`report_request`) se activa solo por fallos de
  forma (≥ 3/207 o un patrón repetido, tras ≤ 1 h simplificando el esquema). Los fallos de
  comprensión se corrigen con descripciones o con otro modelo, no con el híbrido.
- **Descartadas:** S1, texto crudo plano (el código acaba haciendo trabajo de lenguaje, no
  hay dónde expresar la dependencia del caso E y los campos de distintas tools comparten
  nombre); S2 con enums (un "urgente" se vuelve un `high` válido que el validador no
  detecta); S3, citas de evidencia (la cita puede existir y el valor ser derivado, como un
  título sacado de la descripción en el caso A, y una cita mal copiada obliga a volver a
  preguntar). De S3 se conservan `confirms_tool` y `ambiguity_reason`.
- **Pendiente:** D4, anclaje de nombres duro o blando y reglas de fusión (para las listas
  ya hay un marcador que leer: las cubetas `_add` y `_remove` de la enmienda; D4 fija las
  reglas de borde) [resuelto en D4]; D7, confirmación, reglas del veto de `confirms_tool` y números no
  escritos como cifras; D8, regla de `depends_on`. Sin asignar: un posible marcador de
  "instancia nueva" para "crea otro ticket" con un ticket pendiente (misma causa: la
  palabra "otro" se pierde en el intérprete); no forma parte de esta enmienda.

**D1.1 · Anfitrión del ciclo por turno: un nodo dinámico sobre el runtime de nodos** (2026-11-16)

- **Decisión:** el turno lo aloja una sola función asíncrona decorada con
  `@node(rerun_on_resume=True)`, envuelta como
  `Workflow(name="root_agent", edges=[("START", turn)])`. En cada turno hace tres cosas: llama
  al intérprete con `await ctx.run_node(interpreter, node_input=...)` dentro de
  `try/except Exception`; pasa la interpretación y el estado actual al núcleo en Python puro; y
  devuelve exactamente un `Event(message=<respuesta de plantilla>, state=<delta>)`. Adoptada:
  el spike pasó; B1 queda como repliegue.
- **Motivo:** el "B1 vs B2" original mezclaba dos preguntas independientes: qué runtime
  ejecuta el turno, y si lo aloja una función o un grafo de nodos. B1 (`BaseAgent` propio) es
  una función sobre el runtime heredado; B2 (`Workflow` estático) es un grafo de nodos sobre el
  runtime de nodos; esta decisión es una función sobre el runtime de nodos. Un grafo sobre el
  runtime heredado no aplica.
  - *Runtime:* un `BaseAgent` propio como raíz corre por la ruta heredada en 2.14.1, y adk.dev
    describe la orquestación con `BaseAgent` como superada por los workflows basados en grafos
    y los dinámicos. Esta forma es el patrón de workflow dinámico de la propia documentación
    (TASK.md línea 572).
  - *Una función:* el ciclo es una sola llamada al núcleo (TASK.md línea 570). Un grafo
    estático duplicaría la máquina de estados del núcleo en la lista de aristas (TASK.md línea
    508) y repartiría un turno en varios eventos. Tampoco puede capturar el fallo de un nodo
    sin callbacks y un valor centinela, o sin un envoltorio dinámico, y en ese punto ya es
    este diseño.
  - *Se conserva de B1:* una función legible, `try/except` normal, y la respuesta y el cambio
    de estado en un mismo evento.
- **Reglas que se derivan:**
  - *Estado:* se escribe solo a través del evento final. Ninguna escritura en `ctx.state`
    dentro del cuerpo, porque una escritura hecha antes de una excepción se persiste igual.
  - *Fallos:* se clasifican en este orden, sin tocar el estado en ningún caso. La redacción de
    las plantillas corresponde a D2.
    1. La excepción interna, `getattr(ex, "error", ex)`, es un `ValidationError`, o la salida
       está vacía → "no te entendí".
    2. Cualquier otra cosa → "modelo no disponible".

    `getattr(ex, "status_code", None) == 429` puede elegir una redacción más específica dentro
    del caso 2. Nunca es la única prueba de un fallo del modelo, porque un timeout no trae
    código de estado.
  - *Sin imports privados:* nada de `google.adk.workflow._errors`.
  - *Sin `retry_config`* en el nodo del turno ni en el intérprete. Un `RetryConfig()` con los
    campos por defecto reintenta cualquier excepción hasta 5 veces, lo que vuelve a ejecutar
    el cuerpo y gasta cuota de la capa gratuita.
  - *Reanudación (resumability) desactivada,* que es el valor por defecto
    (`App.resumability_config = None`). La CLI siempre envía un mensaje nuevo y nunca un
    `invocation_id`. Si D9 la activa, el cuerpo puede ejecutarse dos veces, así que la
    ejecución ya debe ser segura bajo la clave de idempotencia.
  - *Frontera:* el núcleo no importa ADK. El nodo es el único adaptador.
- **Costos aceptados:** el evento JSON del intérprete queda guardado en `session.events`, y un
  turno fallido guarda además un evento con `error_code` y sin texto. Ninguno llega al modelo,
  por `include_contents='none'`. Por eso la CLI imprime solo los eventos que traen texto y
  cuyo autor no es `interpreter`.
- **Spike (≤ 1.5 h, modelo falso, sin API key; script en [`spikes/spike_d11.py`](../spikes/spike_d11.py)):**
  cinco turnos en una misma sesión: válido,
  JSON inválido, salida vacía, 429 simulado, válido. Pasa si:
  - hay exactamente un evento de respuesta por turno; en los turnos válidos lleva el delta de
    estado y en los fallidos no lleva ninguno;
  - el estado no cambia tras los tres fallos, y el 429 recibe el mensaje "modelo no
    disponible";
  - el servicio de sesiones de Redis guarda y recarga el evento con `error_code`, y el quinto
    turno funciona;
  - con la configuración por defecto, el cuerpo se ejecuta una vez por turno.
- **Resultado del spike (2026-11-16): pasó.** Cinco turnos contra google-adk 2.14.1 (Python
  3.12.3, versiones de `uv.lock`), con modelo falso y un Redis 7.0.15 real: un evento de
  respuesta por turno, con delta solo en los turnos válidos; estado intacto tras los tres
  fallos y "modelo no disponible" para el 429; los eventos con `error_code` se guardan y se
  recargan con una instancia nueva de `RedisSessionService`; el cuerpo corrió una vez por
  turno (5 ejecuciones, 5 llamadas al modelo). Observado: el evento con `error_code` lo firma
  `root_agent`, no `interpreter`, y no trae texto; la salida vacía no genera ese evento; ADK
  registra el traceback del 429 en stderr aunque el nodo lo capture. B1 queda como repliegue
  sin activar.
- **Repliegue:** B1, con el mismo núcleo y las mismas plantillas, si el almacén de Redis
  maneja mal el evento de error o el cuerpo se ejecuta más de una vez por turno. El cambio es
  alrededor de una hora de código de adaptador.
- **Descartadas:** B1 como anfitrión principal (ruta heredada del `Runner` y orquestación que
  la documentación da por superada; queda solo como repliegue) y B2 estático (duplica la
  máquina de estados del núcleo en las aristas, reparte el turno en varios eventos y no
  captura el fallo de un nodo sin callbacks y centinela o sin un envoltorio dinámico).
- **Sin verificar:** cómo muestra `adk web` el evento con `error_code`; si todas las rutas de
  reanudación del runtime de nodos comprueban `is_resumable`; y el comportamiento con el
  modelo real y un 429 en vivo, que corresponde a la fase 0.

**D3 · Política de ambigüedad: P1, condicionada a la fase 0** (2026-11-16)

- **Decisión:** el código decide cuándo preguntar contando las operaciones `explicit` y
  `plausible` que quedan tras el filtro del registro. Con al menos una `explicit` y al menos una
  `plausible` (fila R4) pide una aclaración que lista las candidatas y no inicia ninguna. Dos o
  más `explicit` sin ninguna `plausible` (R3) siguen construyendo un plan, así que el caso E no
  pregunta. P1 se mantiene mientras la fase 0 muestre que el modelo rara vez añade una operación
  `plausible` a solicitudes que TASK.md trata como claras; si el disparador salta, se pasa a
  P2′.
- **Motivo:** TASK.md condiciona la pregunta ("si no existe suficiente información", líneas 137
  y 355) y luego declara que el mensaje de prueba cumple la condición: el caso se titula
  "Intención ambigua" (línea 338) y enumera tres lecturas (líneas 344-350). Todo lo que RF-02
  pide evitar es actuar de más (líneas 128-134), y ninguna línea penaliza una pregunta de más.
  P1 es la única opción en la que el caso obligatorio muestra el comportamiento que nombra su
  título, y su fallo típico es una pregunta, no una ejecución. La regla vive en una tabla en
  código que cuenta operaciones, no en el prompt (línea 587).
- **Compuerta de entrada:** en este orden. S es el conjunto de tools con operaciones `explicit`
  tras el filtro.
  1. El filtro del registro descarta las operaciones de tools desconocidas.
  2. Si hay una aclaración pendiente, el turno se lee como respuesta a ella (ver *Aclaración*)
     antes de mirar `act`. Sin este orden, "solo avísale" devuelto como `request_action`
     entraría a la tabla como una solicitud nueva y perdería los argumentos guardados.
  3. Si hay una operación o un plan pendiente, antes de enrutar por `act`:
     - *Otra tool:* S contiene una tool que no está pendiente y `act` es `request_action`,
       `provide_info`, `reject` o `cancel` → cambio de intención. El registro pendiente se
       archiva como abandonado y la tabla corre sobre este turno. Las tools de S que ya estaban
       pendientes conservan sus argumentos guardados; las demás se descartan sin mezclar nada
       (caso F, TASK.md líneas 411-417). Con `reject` o `cancel`, las operaciones de tools
       pendientes no pasan a la tabla.
     - *Misma tool:* todas las operaciones del turno son de tools pendientes y `act` es
       `request_action` o `provide_info` → se trata como `provide_info`: fusión según D4 y
       `certainty` ignorada (D2; caso B, TASK.md líneas 327-331).
     - *Solo `plausible` de otra tool:* el registro pendiente no se toca y se repite su última
       pregunta. Una `plausible` nunca descarta un flujo.
     - *Resto:* `confirm`, `reject` y `cancel` van a la operación pendiente (D7); `question` y
       `other` responden con una plantilla y no tocan el estado.
  4. Si no hay nada pendiente: `request_action` llega a la tabla de reglas; `provide_info` con
     operaciones se trata como `request_action`; `confirm`, `reject`, `cancel` y `provide_info`
     sin operaciones responden "No hay ninguna operación pendiente." sin tocar el estado
     (TASK.md línea 284); `question` y `other` responden con una plantilla y sin operación.
- **Tabla de reglas:** las filas se cuentan después del filtro y leen conteos, no nombres de
  tools, así que la tabla no crece con el catálogo (TASK.md línea 562).
  - *R1, ninguna operación:* plantilla de capacidades.
  - *R2, exactamente una `explicit` y ninguna `plausible`:* inicia ese flujo en estado de
    recopilación (TASK.md línea 117).
  - *R3, dos o más `explicit` y ninguna `plausible`:* construye un plan (caso E, TASK.md línea
    378).
  - *R4, al menos una `explicit` y al menos una `plausible`:* pregunta y lista las candidatas;
    no inicia nada (TASK.md líneas 134, 137 y 355).
  - *R5, solo `plausible`:* pregunta.
- **Valores de `act` (fijados):** `request_action`, `provide_info`, `confirm`, `reject`,
  `cancel`, `question` y `other`. No se añade ningún valor nuevo (por ejemplo `select`): cada
  clase extra es una frontera más que el modelo puede errar. `question` y `other` siguen
  separados porque `question` es la respuesta a la pregunta de defensa 2 (TASK.md línea 554);
  pueden fusionarse si la fase 0 muestra que el modelo los confunde. Para D3, `reject` y
  `cancel` hacen lo mismo; su diferencia es de D7.
- **Aclaración (provisional):** estas reglas son provisionales hasta que las tres respuestas a
  una aclaración y las tres sondas pasen la fase 0 (D2). La revisión adversarial de este diseño
  se hizo el 2026-11-16 y sus correcciones ya están aquí. Que solo `explicit` seleccione no se
  ha probado con el modelo.
  - *Pregunta:* con una candidata (R5 con una sola `plausible`), "¿Quieres {acción}? Responde sí
    o no."; con dos, "¿Quieres {acción A}, {acción B}, o ambas?"; con tres, "¿Qué necesitas:
    {acción A}, {acción B} o {acción C}? Puedes elegir varias."; con cuatro o más no hay
    aclaración y se responde con la plantilla de capacidades (inalcanzable con tres tools). Se
    listan todas las operaciones que pasan el filtro, `explicit` primero y una entrada por
    tool. La `explicit` también se lista: la lista de operaciones no dice si la `plausible` es
    una alternativa o un añadido, y un sí/no sobre la `plausible` no puede expresar "solo B",
    que es una de las tres lecturas (TASK.md líneas 346-350). El registro aporta dos etiquetas
    por tool: la frase de acción ("enviar una notificación") y el nombre corto
    ("notificación"). La plantilla nunca lee `ambiguity_reason` (D2).
  - *Estado:* la aclaración es un estado (`awaiting_clarification`) del único registro
    pendiente de D1, no un registro aparte. Guarda `id`, `candidates` (lista de
    `{tool, args, depends_on}` tal como se interpretaron), `asked_turn` y `attempts` (0 o 1).
    Los argumentos se guardan porque el turno de respuesta no ve el mensaje original
    (`include_contents='none'`) y TASK.md línea 145 exige conservar lo recopilado. La
    `certainty` original no se guarda. El resumen `{pending?}` muestra al modelo solo los
    nombres de las tools candidatas, el estado y la última pregunta, sin valores (D2).
  - *Lectura de la respuesta:* se aplica antes de enrutar por `act`, en este orden. S es el
    conjunto de tools con operaciones `explicit` tras el filtro.
    1. `act` es `cancel` o `reject` y ninguna tool de S está fuera de las candidatas → se
       descarta la aclaración, no se inicia nada y se dice: "No inicié ninguna acción. Dime qué
       necesitas cuando quieras." Un rechazo nunca selecciona: en "No, la reunión no" la tool
       nombrada es la que el usuario no quiere.
    2. S no está vacío, todas sus tools son candidatas, `act` es `request_action`,
       `provide_info` o `confirm`, y ninguna candidata fuera de S llega como `plausible` → se
       resuelve a S. Con `confirm`, S debe tener exactamente una tool. Una tool inicia su
       flujo; dos o más forman un plan lineal en el orden de las candidatas, que conserva la
       dependencia que las candidatas elegidas ya traían entre sí (D8 la valida) y no añade
       ninguna. Los argumentos guardados se fusionan con los nuevos según D4.
    3. `act` es `confirm`, S está vacío, hay exactamente una candidata y `confirms_tool` es
       nulo o la nombra → se resuelve a esa candidata.
    4. S contiene una tool que no es candidata y `act` es `request_action`, `provide_info`,
       `reject` o `cancel` → es una solicitud nueva: la aclaración se archiva como abandonada y
       la tabla de reglas corre sobre este turno. Las tools de S que eran candidatas conservan
       sus argumentos guardados; las demás candidatas se descartan (caso F, TASK.md líneas
       411-417). Con `reject` o `cancel`, las operaciones de tools candidatas no pasan a la
       tabla.
    5. Cualquier otro caso → respuesta no clara.

    Solo `explicit` selecciona: si "lo que sea" llega como dos operaciones `plausible`, no
    inicia ninguna (TASK.md línea 134). Una `plausible` sobre otra candidata vuelve la
    respuesta no clara, así que repetir el mensaje original no lo resuelve. El turno que
    resuelve una aclaración nunca ejecuta ni autoriza (TASK.md líneas 276-284): aunque los
    argumentos guardados estén completos y válidos, su respuesta es la pregunta por los
    faltantes o la solicitud de confirmación. D7 no puede eximir de confirmación a un flujo en
    el turno en que nace de una aclaración.
  - *Respuesta no clara:* la primera vez `attempts` pasa a 1 y se vuelve a preguntar en forma
    cerrada: "Necesito que elijas: '{nombre A}', '{nombre B}' o 'ambas'. Si no quieres hacer
    nada, di 'cancelar'." (con una candidata: "Necesito un sí o un no: ¿quieres {acción}?"; con
    tres: "Necesito que elijas una o varias: '{nombre A}', '{nombre B}' o '{nombre C}'. Si no
    quieres hacer nada, di 'cancelar'."). La segunda vez se descarta la aclaración, no se
    inicia nada y se dice: "No inicié ninguna acción. Dime qué necesitas cuando quieras." Un
    "sí" posterior ya no tiene a qué asociarse (TASK.md línea 284).
  - *Fallo del intérprete en el turno de respuesta:* D1.1 no toca el estado, así que `attempts`
    no avanza y la aclaración sigue pendiente. Una racha de errores del modelo no la agota; un
    test lo comprueba.
- **Frontera con D2:** la regla de D2 "el código ignora su `certainty`" cubre solo los
  argumentos nuevos de una tool que ya está pendiente. En la respuesta a una aclaración el
  usuario está declarando su intención, y ahí `certainty` sí cuenta. La instrucción al modelo
  no debe decir que `certainty` nunca importa en las respuestas.
- **Si el modelo clasifica mal:**
  - *Solo cuestan una pregunta o una reformulación:* una `plausible` falsa junto a una
    `explicit` (R4; solo con P1); una solicitud clara devuelta solo como `plausible` (R5); una
    solicitud indirecta leída como `question`; una pregunta de capacidades leída como solicitud
    (R2 inicia el flujo y los campos faltantes lo detienen).
  - *Caso C devuelto como dos `explicit`:* cae en R3 y se construye un plan. Ninguna regla de
    D3 lo detiene (TASK.md línea 134); lo frenan los campos faltantes de la reunión y la
    confirmación del plan, si D7 la adopta.
  - *Afirmación leída como solicitud con todos los argumentos:* R2 inicia el flujo y nada en
    D3, D4 o D5 lo detiene. Que falten argumentos no es una capa de seguridad diseñada: depende
    de lo que traiga el mensaje.
  - *Dependencia de D7:* D3 se apoya en la confirmación en esos dos casos: una solicitud que
    llega con todos sus argumentos completos, y el plan que se construye cuando el caso C
    vuelve como dos operaciones `explicit`.
  - *Limitación para el README (omisión):* si el caso C vuelve como una sola notificación
    `explicit`, el agente actúa bien para lo que recibió (R2, pregunta el canal) y la reunión
    nunca se ofrece. Ninguna capa puede detectar una lectura que el modelo no reportó. Se mide
    con el resultado del caso C en la fase 0.
- **Disparador para pasar a P2′:**
  - *Escenarios claros:* aquellos cuya interpretación esperada llega a la tabla sin ninguna
    `plausible`: A, D, E, F (segundo mensaje), X, día de la semana, el contraste del caso C
    (TASK.md línea 117), el primer mensaje del caso G (línea 424) y la línea 113. Son
    9 × 9 = 81 corridas. Quedan fuera C (se espera que pregunte), B y G (su segundo mensaje no
    llega a la tabla), la pregunta informativa (se queda en la compuerta) y las respuestas y
    sondas (se leen como respuesta a una aclaración).
  - *Qué se cuenta:* las corridas de esos escenarios que caen en R4. Las que caen en R5
    preguntan igual con P1 y con P2′, así que no cuentan aquí; las cubre el mínimo de 7/9 por
    escenario claro de D2.
  - *Cláusula agregada:* 9 o más de las 81 corridas (más de 10 %) → P2′.
  - *Cláusula por escenario:* un escenario claro con 2 o más de sus 9 corridas en R4 → una
    ronda de mejora de descripciones (≤ 1 h) y se repiten ese escenario y el caso C (18
    llamadas), porque las descripciones son compartidas. Se pasa a P2′ si la repetición deja
    3 o más de 9 en R4, o 2 en redacciones distintas. Si C deja de pasar en la repetición, se
    revierte el cambio de descripciones. Dos corridas en R4 en una misma redacción se
    documentan como efecto de esa redacción y P1 se mantiene.
  - *Método:* la fase 0 guarda las interpretaciones crudas y un test las repite por las tablas
    de P1 y de P2′, sin llamadas nuevas al modelo.
  - Son disparadores para decidir pronto, no una medida estadística.
- **Repliegue P2′:** en R4 se inicia la operación `explicit` (o el plan, si son varias) y la
  respuesta avisa de la lectura descartada: "No agendé ninguna reunión; si también la quieres,
  pídemela." R5 sigue preguntando. Se defiende como confirmación implícita y se acepta la
  discusión sobre TASK.md línea 355: el README debe decir que el caso C informa y no pregunta.
  Si se activa, cambia D2: el caso C pasa con una operación `explicit` y al menos una
  `plausible`, el aviso en la respuesta y nada ejecutado (solo `plausible` sigue preguntando y
  también pasa); y una `plausible` de más en un escenario claro deja de ser un desacierto,
  porque produce un aviso y no una pregunta.
- **Descartadas:** P2, iniciar la `explicit` y descartar la `plausible` en silencio (toma las
  mismas decisiones que P2′ con menos información para el usuario; en el caso titulado
  "Intención ambigua" pregunta por el canal y no por la intención, TASK.md líneas 338 y 355; y
  deja una `plausible` que nadie lee); P3, regla por verbo (decide por palabra clave, TASK.md
  línea 109; "avísale" es el verbo de la línea 117 y también el del caso C, línea 342, así que
  nunca preguntaría en C; y D2 no tiene campo para el verbo: obtenerlo reabre D2 o lleva la
  regla al prompt, contra D1 y TASK.md línea 587).
- **Pendiente:** D4, "Avísale a Pedro que ya quedó" con una aclaración pendiente (el esquema
  no lo distingue de una respuesta: se resuelve a `send_notification` y la regla de fusión
  decide si "Pedro" reemplaza a "Carlos", TASK.md línea 413), la regla de fusión de los
  argumentos guardados, y "crea otro ticket" con un ticket pendiente (la compuerta lo fusiona
  con el pendiente) [resuelto en D4]; D7, qué tools quedan exentas de confirmación (nunca en el turno que
  resuelve una aclaración), la confirmación de planes y la diferencia entre `reject` y
  `cancel`; D8, hereda que el plan de una aclaración es lineal, en el orden de las candidatas
  y con la dependencia que ya traían. Sin asignar: nombres de tools como enum cerrado o cadena
  libre; avisar al usuario de las operaciones `explicit` que el filtro del registro descarta;
  una `question` durante una aclaración (hoy cuenta como respuesta no clara); dos operaciones
  de la misma tool con distinta `certainty`; R3 con tres o más `explicit` (D1 prevé dos
  pasos); `asked_turn` e `id` de la aclaración, que ninguna regla lee; y dónde vive un
  registro "archivado". Revisión adversarial de la aclaración: hecha el 2026-11-16. La marca
  "provisional" se quita cuando las tres respuestas y las tres sondas pasen la fase 0.

**D4 · Fusión de argumentos entre turnos y anclaje duro de nombres** (2026-11-16)

- **Decisión:**
  1. *Forma.* D4 son dos pasos en Python puro dentro del reductor: anclar los argumentos que
     llegan contra el mensaje actual y fusionarlos con los guardados. Devuelve los argumentos
     nuevos, los cambios, los nombres rechazados y las notas. No valida, no lee registros
     archivados y no importa ADK. Cada regla se declara por tipo de campo en el registro
     (`text`, `word`, `int`, `person`, `date`, `time`, `person_list`), nunca por tool.
  2. *Ausente.* Un campo está ausente si falta o si es una cadena en blanco, una lista vacía o
     un objeto vacío. Lo mismo vale para cada componente de una fecha o de una hora y para
     cada elemento de una lista. El cero es un valor. Un nombre repetido dentro de una lista
     se guarda una vez. Lo ausente nunca cambia lo guardado. Un escalar no se puede vaciar,
     solo reemplazar.
  3. *Anclaje duro.* En los campos `person` y en cada nombre de cada cubeta de lista se toman
     las palabras del nombre que no están en la lista de exclusión. Debe quedar al menos una,
     y cada una debe ser una palabra completa del mensaje actual (NFKD, sin marcas
     combinantes, `casefold`). La lista de exclusión es exactamente: yo, mi, me, tu, ti, te,
     el, ella, usted, ustedes, nosotros, nosotras, ellos, ellas, le, les, lo, la, los, las,
     se, nos. El nombre que falla se descarta él solo y se anota en el log. Ningún otro tipo
     de campo se ancla.
  4. *Una vez, al entrar.* El anclaje corre en el turno en que llega el valor, contra el
     mensaje de ese turno, incluidas las candidatas de una aclaración. Un valor guardado
     nunca se vuelve a anclar.
  5. *Escalares, fechas y horas.* Gana el último valor, entero. Dos valores son iguales si
     coinciden tras la misma normalización que los nombres, sin espacios en los extremos;
     entonces nada cambia y se conserva la grafía guardada. Los componentes de fecha y hora
     se guardan como se dijeron; la conversión a ISO, los valores por defecto y la
     aritmética de fechas son de D5.
  6. *Listas.* Se parte de `<campo>` si llegó y, si no, de la lista guardada; se agrega
     `<campo>_add`; después se quita `<campo>_remove`, comparando el nombre normalizado
     entero. Un resultado vacío deja el campo ausente. Las listas se comparan como conjuntos.
     Tres casos producen una nota: quitar un nombre que no está y agregar uno que ya está,
     que no cambian nada; y el mismo nombre en `_add` y `_remove`, que queda fuera.
  7. *Validez.* La fusión ignora la validez y guarda las palabras del usuario. Un valor nuevo
     inválido reemplaza a uno guardado válido; D5 lo reporta y la operación no puede
     ejecutarse hasta que se corrija.
  8. *Cambios.* Por campo: `fill` (ausente → valor), `overwrite` (valor → otro valor; incluye
     todo cambio a una lista guardada) o `clear` (una lista que queda vacía), con el valor
     anterior y el nuevo. No se guarda la procedencia; la lista de cambios del turno va al
     log.
  9. *Respuestas.* Se muestra cada cambio (anterior → nuevo en un `overwrite`; la lista
     anterior en las listas) y cada nota. Si se rechazó algún nombre, una línea fija lo dice
     sin mostrar el nombre. Un flujo que nace de las candidatas de una aclaración lista
     todos los valores que heredó.
  10. *Valores del código* (`ticket_id`, resultados): viven fuera de `args`. La fusión solo
      escribe campos del contrato.

  *Lo que D4 exige a D7* (vinculante; D7 puede añadir, no quitar):
  - La fusión corre antes de evaluar un `confirm`. La compuerta de D3 no lo hace hoy:
    `confirm` cae en "Resto" (D3, *Compuerta de entrada*, paso 3).
  - Un "sí" cuenta solo en un turno cuya fusión no devolvió nada: ningún cambio, ningún
    nombre rechazado y ninguna nota.
  - Un turno con un `overwrite`, un `clear`, un nombre rechazado o una nota nunca ejecuta.
- **Motivo:** TASK.md línea 145 pide conservar lo recopilado y actualizarlo con información
  nueva: lo ausente no borra (regla 2) y lo nuevo reemplaza (reglas 5 y 6). La línea 184
  prohíbe inventar: un nombre se copia, no se parafrasea, así que el código puede exigir sus
  palabras en el mensaje. Las líneas 186-200 piden identificar el problema: "40" y "25:90"
  se guardan como se dijeron para que D5 nombre ambos (regla 7). Las líneas 276-286 atan el
  "sí" a una operación identificada: si llega con un cambio, un rechazo o una nota, ya no
  responde a lo que se mostró. La línea 413 pide no mezclar: D3 archiva el flujo anterior,
  la fusión no lee archivados y un nombre solo entra si está escrito en el mensaje nuevo.
  - *Hechos de D2:* el modelo no ve los valores guardados (`include_contents='none'`;
    `{pending?}` sin valores), así que la fusión la hace el código. Los nulos no llegan al
    núcleo (`workflow/_llm_agent_wrapper.py:410` → `utils/_schema_utils.py:260`, google-adk
    2.14.1), pero `""`, `[]`, `{}` y `0` sí: de ahí la regla 2. El verbo de una lista llega
    en la cubeta: de ahí la regla 6.
  - *Lista de exclusión:* nunca elige una acción ni un significado; solo puede rechazar un
    valor, y un rechazo cuesta una pregunta.
  - *Quitar al final:* cuando el modelo se contradice, el código se queda con quitar.
- **Precisa D3:** D3 guarda las candidatas de una aclaración "tal como se interpretaron". Por
  la regla 4 se guardan después del anclaje: el mensaje que resuelve ("solo avísale") ya no
  trae los nombres. El texto de D3 no se edita.
- **Descartadas:** anclaje blando (guarda un nombre inventado, que se ejecuta si el usuario
  confirma sin leer); coincidencia por subcadena ("Ana" está dentro de "mañana"); un
  directorio de personas (regla de negocio, D5); preguntar siempre ante un cambio (cada
  corrección cuesta un turno, incluso "mejor media"); `source_turn` (ninguna regla lo lee);
  fusionar componentes de fecha u hora (10:30 y luego "a las 11" da 11:30, válido y
  equivocado); la excepción para día, mes y año (tres casos laterales: completar el mes
  cuenta como cambio, "mejor el viernes 27" pierde el mes y un año viejo sobrevive);
  guardar ISO al entrar ("40 de noviembre" no cabe y D4 haría aritmética de fechas);
  conservar el valor anterior si el nuevo es inválido (seguiría ejecutable un valor que el
  usuario ya cambió, y la fusión tendría que conocer la validez); agregar al final en las
  listas (un nombre en ambas cubetas dejaría invitado a quien se pidió quitar); y reglas
  especiales para los dos traspasos de D3 (leen la intención en un valor: "Avísale a Pedro,
  no a Carlos" es una corrección y perdería el mensaje).
- **Limitaciones para el README:** el código solo comprueba nombres: la fusión no detecta un
  título, prioridad, canal, duración, fecha u hora inventados; en el caso A (TASK.md línea
  304), un título derivado de la descripción depende del escenario A de la fase 0 y del
  resumen de confirmación. Un nombre debe escribirse en el mismo mensaje ("asígnalo a él" y
  "avísame" no anclan). El anclaje prueba que una palabra se dijo, no que sea una persona ni
  que esté en el campo correcto. Un apodo que el modelo normaliza se rechaza. Un nombre
  igual a un pronombre de la lista no puede introducirse. "Quita a Juan" no coincide con
  "Juan Pérez". "Crea otro ticket" con un ticket pendiente actualiza el pendiente. Un
  destinatario nuevo durante una aclaración conserva el mensaje guardado. Dos valores para
  un mismo campo en un solo mensaje no pueden detectarse. Un escalar no se puede vaciar. La
  lista de exclusión solo cubre el español.
- **Pendiente:** D7, sin decidir aquí: si alguna tool queda exenta de confirmación y, por
  tanto, si un turno solo con `fill` puede ejecutar; que un plan necesita una confirmación
  que liste los valores de cada paso, porque cambiar el responsable del ticket no toca el
  destinatario de la notificación; si el hash de confirmación cubre los argumentos guardados
  o los valores normalizados que se muestran; un `reject` que trae argumentos; y un "sí,
  pero…" que el modelo devuelve como `confirm` sin argumentos. D5: valores por defecto de
  una fecha parcial, tipos de fecha mezclados, quién reporta dos argumentos que no
  concuerdan y `duration_minutes=0`. D8: un argumento que el modelo coloca en el paso
  equivocado de un plan lo quita el filtro por contrato de D2 y nadie lo avisa. Sin
  asignar: anclar las palabras de `priority` y `channel`; se revisa si la fase 0 muestra
  que el modelo cambia esas palabras. Para leer la fase 0: un número que el mensaje no
  contenía cuenta como fallo crítico.
