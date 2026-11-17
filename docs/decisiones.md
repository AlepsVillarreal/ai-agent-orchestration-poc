# Decisiones de diseño

Registro de las decisiones de arquitectura del PoC, en el orden en que se tomaron. Las
referencias a `TASK.md` y sus números de línea apuntan al enunciado de la prueba técnica,
copiado literal en [`TASK.md`](TASK.md); su línea 4 queda en blanco a propósito para conservar
esa numeración (D72).

Una entrada marcada **autodecidida por el orquestador; revisable** es una elección de
implementación que el orquestador del equipo de agentes tomó sin detener el trabajo, por regla
del autor (2026-11-16): la opción más chica que cumple TASK.md y las decisiones ya tomadas. El
autor puede revertirla. Una decisión de diseño nueva que cambie lo que ve el usuario, cuándo se
ejecuta una tool o qué se guarda se sigue consultando al autor.

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
  para el caso E [enmendado en D8]; confirmación ligada a `operation_id` + hash de argumentos; clave de
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
- **Umbral de fiabilidad (fase 0):** [enmendado en F0] modelo base `gemini-3.5-flash-lite`. 23 escenarios (A,
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
  reglas de borde) [resuelto en D4]; D7, confirmación [resuelto en D7], reglas del veto de `confirms_tool` y números no
  escritos como cifras [resuelto en D7.4 y D7.5]; D8, regla de `depends_on` [resuelto en D8]. Sin asignar: un posible marcador de
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
  - *Sin `retry_config`* [enmendado en D1.2] en el nodo del turno ni en el intérprete. Un `RetryConfig()` con los
    campos por defecto reintenta cualquier excepción hasta 5 veces, lo que vuelve a ejecutar
    el cuerpo y gasta cuota de la capa gratuita.
  - *Reanudación (resumability) desactivada,* que es el valor por defecto
    (`App.resumability_config = None`). La CLI siempre envía un mensaje nuevo y nunca un
    `invocation_id`. Si D9 la activa [resuelto en D9], el cuerpo puede ejecutarse dos veces, así que la
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
     - *Otra tool:* [enmendado en D3.1] S contiene una tool que no está pendiente y `act` es `request_action`,
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
- **Aclaración (provisional):** [enmendado en F0] estas reglas son provisionales hasta que las tres respuestas a
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
    `{tool, args, depends_on}` tal como se interpretaron), `asked_turn` y `attempts` (0 o 1) [enmendado en D3.2].
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
    4. [enmendado en D3.1] S contiene una tool que no es candidata y `act` es `request_action`, `provide_info`,
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
  - *Respuesta no clara:* [enmendado en D3.2] la primera vez `attempts` pasa a 1 y se vuelve a preguntar en forma
    cerrada: "Necesito que elijas: '{nombre A}', '{nombre B}' o 'ambas'. Si no quieres hacer
    nada, di 'cancelar'." (con una candidata: "Necesito un sí o un no: ¿quieres {acción}?"; con
    tres: "Necesito que elijas una o varias: '{nombre A}', '{nombre B}' o '{nombre C}'. Si no
    quieres hacer nada, di 'cancelar'."). La segunda vez se descarta la aclaración, no se
    inicia nada y se dice: "No inicié ninguna acción. Dime qué necesitas cuando quieras." Un
    "sí" posterior ya no tiene a qué asociarse (TASK.md línea 284).
  - *Fallo del intérprete en el turno de respuesta:* [enmendado en D3.2] D1.1 no toca el estado, así que `attempts`
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
    confirmación del plan, si D7 la adopta [resuelto en D7].
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
- **Disparador para pasar a P2′:** [enmendado en F0]
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
  `cancel` [resuelto en D7]; D8, hereda que el plan de una aclaración es lineal, en el orden de las candidatas
  y con la dependencia que ya traían [resuelto en D8]. Sin asignar: nombres de tools como enum cerrado o cadena
  libre [resuelto en D10]; avisar al usuario de las operaciones `explicit` que el filtro del registro descarta [resuelto en D10];
  una `question` durante una aclaración (hoy cuenta como respuesta no clara); dos operaciones
  de la misma tool con distinta `certainty` [resuelto en D11]; R3 con tres o más `explicit` (D1 prevé dos
  pasos) [resuelto en D8]; `asked_turn` e `id` de la aclaración, que ninguna regla lee; y dónde vive un
  registro "archivado" [resuelto en D6]. Revisión adversarial de la aclaración: hecha el 2026-11-16. La marca
  "provisional" se quita cuando las tres respuestas y las tres sondas pasen la fase 0 [enmendado en F0].

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
  pero…" que el modelo devuelve como `confirm` sin argumentos [resuelto en D7]. D5: valores por defecto de
  una fecha parcial, tipos de fecha mezclados, quién reporta dos argumentos que no
  concuerdan y `duration_minutes=0` [resuelto en D5]. D8: un argumento que el modelo coloca en el paso
  equivocado de un plan lo quita el filtro por contrato de D2 y nadie lo avisa. Sin
  asignar: anclar las palabras de `priority` y `channel`; se revisa si la fase 0 muestra
  que el modelo cambia esas palabras. Para leer la fase 0: un número que el mensaje no
  contenía cuenta como fallo crítico.

**D5 · Validación por tipo de campo** (2026-11-16)

- **Decisión:** el validador es Python puro, corre después de la fusión de D4 y declara cada
  regla por tipo de campo en el registro. Devuelve faltantes, inválidos con su causa y los
  valores normalizados que muestra la confirmación (D7). [`date` y `time` sin regla hasta la
  09b; ver D44] [reglas de `date` y `time` en D56]
  - *D5.1 · Fechas:* `relative_days` y `weekday` se resuelven contra "hoy" (un día de la
    semana es su próxima ocurrencia, sin contar hoy); sin año, la próxima ocurrencia de ese
    día y mes; sin mes, el campo falta y se pregunta; un día que no existe es inválido.
    "Hoy" es la fecha local de la máquina y los tests la inyectan.
  - *D5.1b · Fecha pasada:* es inválida, como restricción de negocio.
  - *D5.1c · Formas mezcladas:* dos formas de fecha que no dan el mismo día ("el viernes 27"
    si el 27 no es viernes) son un error de coherencia; ninguna gana.
  - *D5.2 · Horas:* `hour` 0–23 y `minute` 0–59; sin minuto, `:00`; sin hora, el campo
    falta. Una hora sin "de la tarde" se toma como llegó y se ve en la confirmación. Fecha y
    hora juntas en el pasado son un error de coherencia.
  - *D5.3 · Duración:* entero de 1 a 480 (8 h); `0` y los negativos son inválidos.
  - *D5.4 · Personas* (`assignee`, `recipient`, `attendees`): texto no vacío y anclado
    (D4), nada más. Sin directorio y sin exigir correo o teléfono según `channel`.
- **Motivo:** RF-04 pide validar tipos, formatos, restricciones de negocio y coherencia
  (TASK.md líneas 155-163), y RF-06 y el caso D piden nombrar el problema de la fecha y el
  de la hora (líneas 186-200 y 361-371). La fecha aporta una regla de negocio (fecha
  pasada) y dos de coherencia (formas mezcladas; fecha y hora en el pasado). Solo se
  completan el año y el minuto; el mes y la hora se preguntan, porque completarlos sería
  inventar (línea 184). La lectura AM/PM la protege la confirmación de D7, no el validador
  (líneas 72 y 194-198). Un rango fijo resuelve el `duration_minutes=0` que D4 dejó a D5
  (líneas 73 y 161). El caso G solo da "Carlos" y debe llegar a la confirmación sin pedir
  más (líneas 424-438); un directorio sería una regla de negocio que nadie pidió (líneas
  54, 74 y 90).
- **Descartadas:**
  - *D5.1:* sin año, el año en curso (y fecha pasada si ya pasó); aceptar solo
    `relative_days` o día y mes, y volver a pedir `weekday` y cualquier mezcla.
  - *D5.1b:* aceptar una fecha pasada.
  - *D5.1c:* que gane la primera forma y se avise (el código elegiría por el usuario).
  - *D5.2:* un horario laboral (p. ej. 08:00–20:00) como restricción de negocio; preguntar
    mañana o tarde ante una hora de 1 a 12.
  - *D5.3:* mayor que 0 sin tope; de 15 a 480 en múltiplos de 15.
  - *D5.4:* un directorio fijo con correo y teléfono, que invalida a quien no esté; exigir
    que `recipient` sea un correo con `email` y un teléfono con `sms`.
- **Resuelve** lo que D4 dejó a D5: valores por defecto de una fecha parcial, tipos de
  fecha mezclados, quién reporta dos argumentos que no concuerdan (el validador, como error
  de coherencia) y `duration_minutes=0`.

**D6 · Archivado y resultados** (2026-11-16)

- **Decisión:** al cerrarse (completado, fallido, incierto, cancelado o abandonado por un
  cambio de intención), el registro pendiente sale de `pending` y se agrega a
  `session.state["history"]`, que guarda los últimos 10 con su estado final, sus valores y
  su resultado. Ninguna regla lo lee: la fusión no lee archivados (D4).
- **Motivo:** RF-07 pide mantener el estado de ejecución y los resultados de las tools
  (TASK.md líneas 208-229, en especial 220-222); así tienen un lugar en el mismo estado de
  Redis, sin un almacén nuevo. El caso F archiva el flujo abandonado sin mezclarlo (líneas
  411-417). El resultado también queda bajo la clave de idempotencia (D9), que es la que se
  consulta antes de ejecutar.
- **Descartadas:** borrar el registro y dejar solo la línea de log (RF-07 pide mantener los
  resultados); una clave aparte en Redis por sesión, fuera de `session.state` (un almacén
  más para datos que ninguna regla lee).
- **Repliegue por plazo:** borrar el registro y dejar solo el log; es el tercer recorte de
  código, después de los de D8.1 y D7.3.

**D7 · Confirmación** (2026-11-16)

- **Decisión:**
  - *D7.1 · Qué se confirma:* todo, siempre. Cada operación y cada plan necesitan un "sí"
    explícito; un plan se confirma una sola vez, con los valores normalizados de cada paso.
    Un "sí" mientras faltan datos o hay inválidos no ejecuta y repite la pregunta pendiente.
    Ninguna tool queda exenta, así que un turno solo con `fill` nunca ejecuta.
  - *D7.2 · Hash:* el hash de D1 cubre los valores normalizados que se mostraron (p. ej. la
    fecha ISO ya resuelta de "mañana"). Al llegar el "sí" se recalcula y debe coincidir; si
    "mañana" cambió de día a medianoche, el hash cambia y se vuelve a preguntar.
  - *D7.3 · `reject` y `cancel`:* `cancel` descarta la operación pendiente; `reject` sin
    argumentos también. `reject` con argumentos de la tool pendiente es una corrección: pasa
    por la fusión de D4, nunca ejecuta y vuelve a pedir confirmación.
- **Cumple lo que D4 exige a D7:**
  - *La fusión corre antes de evaluar un `confirm`:* en el paso "Resto" de la compuerta de
    D3, un `confirm` o un `reject` que trae argumentos de la tool pendiente pasa primero por
    la fusión, y solo después se evalúa.
  - *Un "sí" cuenta solo en un turno cuya fusión no devolvió nada:* ningún cambio, ningún
    nombre rechazado, ninguna nota, y el hash de D7.2 coincide.
  - *Un turno con un `overwrite`, un `clear`, un nombre rechazado o una nota nunca ejecuta:*
    muestra lo que cambió y vuelve a pedir confirmación.
  - Cumple también D3: sin tools exentas, el turno que resuelve una aclaración nunca
    ejecuta.
- **Motivo:** RF-08 y RF-10 piden ejecutar solo con autorización y atar el "sí" a una
  operación identificada (TASK.md líneas 233-243 y 276-284), y el caso G confirma antes de
  ejecutar (líneas 420-438). Una regla sin excepciones cubre G y los dos casos en que D3 se
  apoya en la confirmación: una solicitud completa en un solo mensaje y el caso C leído
  como dos `explicit`. El hash sobre lo mostrado confirma exactamente lo que el usuario vio.
  Un `reject` con argumentos es la opción más chica que no pierde "No, mejor a las 11", que
  la línea 145 obliga a incorporar.
- **Descartadas:**
  - *D7.1:* eximir `create_ticket`, que se ejecutaría en el turno en que queda completo si
    solo trajo `fill`; confirmar solo `send_notification` y los planes.
  - *D7.2:* hash de los argumentos guardados, con las palabras del usuario (no cambia si
    "mañana" pasa a ser otro día); sin hash, solo `operation_id` y la regla de D4.
  - *D7.3:* descartar siempre e ignorar con un aviso los argumentos de un `reject` (pierde
    la corrección); que `reject` nunca descarte y pregunte qué cambiar.
- **Repliegue por plazo:** en D7.3, `reject` y `cancel` descartan siempre; es el segundo
  recorte de código.
- **Para el README:** el caso B termina con un "sí", que TASK.md líneas 311-335 no muestran.
- **Limitación para el README:** "Sí, pero por SMS" devuelto como `confirm` sin argumentos
  ejecuta los valores mostrados; el código no puede ver el "pero" (D4).
- **Pendiente:** las reglas del veto de `confirms_tool` con una operación pendiente (D2 solo
  fija "nulo no bloquea") y los números no escritos como cifras (D2). Ninguna opción
  aprobada los cubre. [resuelto en D7.4 y D7.5]

**D8 · Plan de varias tools** (2026-11-16)

- **Decisión:**
  - *D8.1 · Forma:* [enmendado en D8.4] lineal, una tool por paso, hasta las tres del catálogo. Cada paso va
    después del que nombra en `depends_on`; un `depends_on` que apunta fuera del plan o forma
    un ciclo se ignora y se anota en el log. El plan se detiene en el primer fallo, aunque
    los pasos no dependan entre sí. Una sola confirmación para todo el plan (D7.1). Amplía el
    plan de dos pasos del alcance mínimo de D1.
  - *D8.2 · `ticket_id`:* [enmendado en D8.4] el registro declara que un paso de `send_notification` que depende
    de `create_ticket` recibe el `ticket_id` al final de `message` ("… (ticket TCK-0001)").
    `message` sigue siendo obligatorio y se pide si falta. La confirmación del plan dice "se
    añadirá el número de ticket".
  - *D8.3 · Fallos parciales:* si falla el paso 1, el 2 no corre, el plan se cierra como
    `failed` y se informa. Si falla el 2, se informan el ticket creado y el fallo, y el plan
    se cierra. Sin reintento automático: para reintentar, el usuario repite la solicitud y la
    idempotencia (D9) devuelve el ticket existente en vez de crear otro. Los mocks fallan por
    parámetro en los tests y con `MOCK_FAIL=create_ticket` en la CLI.
- **Motivo:** RF-09 y el caso E piden orden de ejecución, pasar el resultado y no ejecutar
  la segunda tool si falla la primera (TASK.md líneas 120, 250-252, 260-270 y 378-392). Una
  sola forma de plan, sin grafo: tres pasos cuestan lo mismo que dos y resuelven "R3 con
  tres o más `explicit`", que D3 dejó sin asignar (línea 555). El código no redacta
  contenido: solo añade un valor que él mismo produjo y que vive fuera de `args` (D4 regla
  10; línea 184). Los fallos
  parciales y los reintentos (líneas 270-272, 559 y 580) se explican con un mecanismo que ya
  existe, la clave de D9.1; con otra clave, repetir la solicitud crearía un segundo ticket.
- **Descartadas:**
  - *D8.1:* exactamente dos pasos, respondiendo "una o dos operaciones a la vez" ante tres
    `explicit`; ejecutar los pasos independientes aunque falle otro.
  - *D8.2:* que el código genere `message` con una plantilla si falta (redactaría
    contenido); un campo opcional `reference` en `send_notification` (cambia el contrato,
    TASK.md líneas 89-92).
  - *D8.3:* dejar pendiente el paso 2 fallido hasta un "reintenta"; un reintento automático
    con la misma clave antes de reportar el fallo.
- **Repliegue por plazo:** en D8.1, exactamente dos pasos (el alcance de D1), que es el
  primer recorte de código; quitar `MOCK_FAIL` de la CLI y dejar el fallo solo como
  parámetro de los tests es el cuarto.
- **Sin cambio:** un argumento en el paso equivocado de un plan lo quita el filtro por
  contrato y nadie lo avisa; sigue como limitación de D4.

**D9 · Idempotencia y estado incierto** (2026-11-16)

- **Decisión:**
  - *D9.1 · Clave:* hash de sesión + tool + valores normalizados. Registro en Redis con
    `SET NX`: `started` antes de llamar al mock y `done` con el resultado después, con TTL de
    24 h. Una ejecución igual ya hecha devuelve el resultado guardado sin ejecutar ("Ese
    ticket ya existía: TCK-0001").
  - *D9.2 · Estado incierto:* `started` sin `done` (caída o timeout) no se reejecuta. La
    operación se cierra como `uncertain` y se le dice al usuario que no se pudo confirmar si
    se ejecutó y que no se reintentará; la clave expira con el TTL. La documentación explica
    que con un proveedor que acepte claves de idempotencia se reintentaría con la misma.
  - *Caso H, dos momentos:* con la operación pendiente, la misma solicitud entra por "Misma
    tool" (D3), la fusión no cambia nada y se repite la pregunta; ya ejecutada, el duplicado
    se detecta tras el "sí". Un "sí" reenviado tras un turno no persistido encuentra `done` y
    no reejecuta.
- **Motivo:** un solo mecanismo cubre las dos formas del caso H, el mensaje repetido y el
  reintento tras un timeout (TASK.md líneas 286, 443-449 y 558). El registro vive fuera de
  `session.state` porque el estado solo se escribe al final del turno (D1.1) y una caída a
  mitad de la ejecución lo perdería. Ante la duda se ejecuta a lo más una vez: con un efecto
  externo, un duplicado es peor que pedir verificación (líneas 272, 451 y 580). La
  reanudación de ADK sigue desactivada (D1.1).
- **Descartadas:**
  - *D9.1:* `operation_id` + hash de la confirmación (protege el reintento del mismo "sí",
    pero una solicitud idéntica repetida después de ejecutarse crea otro ticket); preguntar
    "¿Creo otro igual?" ante una ejecución igual ya hecha.
  - *D9.2:* reintentar con la misma clave y que el mock deduplique, como un proveedor;
    preguntar al usuario si reintentar, con el riesgo de duplicado a la vista.
- **Limitaciones para el README:** dos tickets idénticos a propósito en menos de 24 h no son
  posibles; hay que cambiar algún valor. El duplicado se detecta al ejecutar, después del
  "sí".
- **Pendiente:** qué deja la clave cuando el mock responde con `success=false`. Si queda
  como `done`, repetir la solicitud devuelve el fallo guardado y el reintento de D8.3 no
  vuelve a ejecutar el paso que falló. [resuelto en D9.3]

**D10 · Nombres de tools en el esquema del intérprete: cadena libre** (2026-11-16)

- **Decisión:** `operations[].tool` es una cadena libre que filtra el registro (D3, paso 1).
  Si el filtro descarta algo y queda otra operación, la respuesta suma una línea fija:
  "Parte de tu solicitud no corresponde a ninguna de mis herramientas." Si no queda nada, R1
  responde con las capacidades.
- **Motivo:** el esquema no crece con el catálogo (TASK.md línea 562), y una petición fuera
  del catálogo no se fuerza a una tool cercana ni termina en "no te entendí" (líneas 107 y
  128-132). Es la única opción compatible con el paso 1 de D3, que supone una cadena libre.
- **Descartadas:** un enum cerrado generado del registro (un nombre desconocido es un
  `ValidationError` y responde "no te entendí"); una cadena libre descartada en silencio (el
  usuario no sabe que parte de su solicitud se ignoró).

**D11 · Dos operaciones de la misma tool en un turno** (2026-11-16)

- **Decisión:** [enmendado en D11.1] si tras el filtro del registro un turno trae dos operaciones de la misma tool
  ("avísale a Carlos por correo y a Ana por SMS"), el código no inicia ni fusiona nada, no
  toca el estado y responde con una plantilla: "Puedo hacer una operación de cada tipo por
  mensaje; ¿por cuál empiezo?". Vale igual si difieren en `certainty`.
- **Motivo:** nunca ejecuta ni mezcla argumentos (TASK.md líneas 128-134 y 413) y evita
  decidir qué paso recibe qué argumentos. Con D8.1 (una tool por paso), un plan nunca tiene
  dos pasos de la misma tool.
- **Descartadas:** quedarse con la primera, descartar las demás y avisarlo; juntarlas en una
  sola operación (`explicit` si alguna lo es; argumentos de la primera), que mezcla
  argumentos de dos solicitudes.

**Enmienda a D2 y D3 (F0) · Corrida completa de la fase 0 condicionada a la cuota** (2026-11-16)

- **Decisión:** la prueba de humo de 23 llamadas es obligatoria y se corre justo después del
  primer turno completo. La corrida de 207 llamadas de D2 se hace solo si la cuota del día
  alcanza al terminar los tests de los casos A–H. Si no se hace [enmendado en F0.2: no se hace]:
  - el README dice que los umbrales de D2 se midieron solo con el humo;
  - D3 conserva la marca "provisional" de la *Aclaración* y P1 se mantiene;
  - el *Disparador para pasar a P2′* de D3 no se mide, porque necesita 81 corridas de los
    escenarios claros.

  Si se hace, D2 y D3 se aplican tal como están escritos.
- **Motivo:** protege el plazo y la cuota de la capa gratuita sin perder la prueba temprana
  del esquema que pide el repliegue de D1. La validación no depende del modelo (TASK.md
  líneas 166-168) y el foco de la evaluación es la orquestación, no el modelo ni el prompt
  (líneas 587 y 597). Es una desviación consciente del umbral de D2, no una decisión nueva:
  el texto de D2 y D3 no se edita.
- **Descartadas:** humo y corrida completa, las dos obligatorias (D2 tal como está escrito);
  solo el humo, sin corrida completa en ningún caso.
- **Pendiente:** el umbral de fallos de forma de D2 que activa el híbrido (≥ 3/207) no tiene
  un equivalente fijado para las 23 llamadas del humo. [resuelto en F0.1]

**D9.3 · Enmienda a D9: un resultado fallido libera la clave** (2026-11-16)

- **Decisión:** si el mock responde `success=false`, el fallo se informa al usuario, se anota
  en el log y la clave de idempotencia se borra. Repetir la solicitud la ejecuta de nuevo. Un
  timeout no cambia: `started` sin `done` se cierra como `uncertain` (D9.2).
- **Motivo:** así funciona el reintento de D8.3 sin un mecanismo nuevo. Si falló el paso 1,
  repetir la solicitud crea el ticket; si falló el paso 2, el paso 1 encuentra `done` y
  devuelve el ticket guardado, y la notificación corre de nuevo (TASK.md líneas 270-272 y
  559).
- **Descartada:** guardar el fallo como `done`; repetir la solicitud devolvería el fallo
  guardado durante 24 h y el paso que falló no volvería a ejecutarse.

**D7.4 · Enmienda a D7: veto de `confirms_tool`** (2026-11-16)

- **Decisión:** un `confirm` cuyo `confirms_tool` nombra una tool distinta de la pendiente no
  ejecuta ni cambia nada, y vuelve a mostrar la confirmación pendiente [precisado en D57: nada
  de lo que trae el mensaje; el paso del tiempo sí puede cambiar la validez]. En un plan,
  `confirms_tool` puede nombrar cualquier tool del plan. Nulo no bloquea (D2).
- **Motivo:** el "sí" vale solo para la operación pendiente identificada y nunca autoriza otra
  tool (TASK.md líneas 282-284). Repetir la confirmación muestra qué está pendiente sin perder
  lo recopilado (línea 145).

**D7.5 · Enmienda a D2 y D7: números no escritos como cifras** (2026-11-16)

- **Decisión:** sin regla extra. El modelo convierte las palabras a número ("media hora" →
  30), como ya le asigna D2, y la confirmación (D7.1, D7.2) muestra el valor normalizado antes
  del "sí".
- **Motivo:** una conversión equivocada cuesta una corrección, nunca una ejecución
  equivocada: el usuario ve el número antes de autorizar (TASK.md líneas 168 y 276-284).
- **Limitación para el README:** el código no comprueba que un número dicho con palabras se
  convirtió bien; "media hora" que llega como 60 es válida y solo la confirmación lo deja ver.

**F0.1 · Enmienda a F0: umbral de la prueba de humo** (2026-11-16)

- **Decisión:** con 0/23 fallos de forma se mantiene la opción B. Con uno o más, el
  orquestador se detiene y muestra los casos al autor, que elige entre el híbrido de D1, un
  ajuste del esquema o seguir. No se fija un número de antemano.
- **Motivo:** el umbral de D2 (≥ 3/207) no tiene traducción directa a 23 llamadas; en vez de
  fijar una proporción, el autor revisa cada fallo de forma antes de activar o descartar el
  repliegue de D1.

**D12 · Contratos: un argumento fuera del contrato es un error** (2026-11-16) · autodecidida
por el orquestador; revisable

- **Decisión:** los tres contratos Pydantic (`core/contracts.py`) heredan de una base con
  `extra="forbid"`. Cada mock valida sus argumentos contra el contrato antes de producir un
  resultado, así que una clave que el contrato no declara lanza `ValidationError` y nada se
  ejecuta. Lo prueba `test_contract_rejects_unknown_fields`.
- **Motivo:** el contrato es la última barrera antes del mock (TASK.md líneas 149 y
  233-243). El filtro por contrato de D2 ya quita las claves desconocidas y los valores del
  código viven fuera de `args` (D4 regla 10), así que una clave de más indica un error del
  código: debe fallar, no ignorarse en silencio.
- **Descartada:** el valor por defecto de Pydantic, que ignora las claves desconocidas.

**D13 · Contratos: `duration_minutes` como entero estricto** (2026-11-16) · autodecidida por
el orquestador; revisable

- **Decisión:** en `ScheduleMeetingArgs`, `duration_minutes` es `StrictInt`: el contrato
  rechaza `"30"`, `True` y `30.0` en vez de convertirlos. El rango 1–480 sigue siendo del
  validador (D5.3), así que el contrato acepta `0` y negativos.
- **Motivo:** el validador de D5.3 entrega un entero; una conversión implícita ("30" → 30) en
  la última barrera ocultaría un error del validador (TASK.md líneas 73 y 155).
- **Descartada:** `int` de Pydantic, que convierte `"30"` en 30.
- **Pendiente:** ningún test lo cubre: con `int` los 46 tests siguen pasando (revisión de la
  tarea 01). Falta un caso `"30"` en el test de formas inválidas de los contratos.
  [resuelto en la tarea 02]

**D14 · Intérprete: el resumen de lo pendiente llega por un `InstructionProvider`**
(2026-11-16) · autodecidida por el orquestador; revisable

- **Decisión:** la instrucción del `interpreter` (`assistant/interpreter.py`) es una función
  que recibe el `ReadonlyContext`, lee `state["pending"]` y une la parte fija, generada del
  registro, con el resumen de `pending_summary` (`core/instruction.py`). El resumen lee solo
  `tools`, `status`, `missing` y `last_question`, nunca un valor recopilado, y no se agrega
  ninguna clave al estado. Lo prueban `test_pending_summary_has_no_collected_values` y
  `test_interpreter_request_with_fake_model`.
- **Motivo:** con una instrucción de texto, ADK sustituye `{pending?}` por
  `str(state["pending"])`, el registro entero con sus valores, en contra de D2; y una
  `{palabra}` en una descripción que no sea clave del estado lanza `KeyError`
  (`flows/llm_flows/prompt/_instructions_utils.py:150-197`, google-adk 2.14.1). Una función
  se salta esa sustitución (`agents/llm_agent.py:820-842`). El modelo ve un resumen del
  estado estructurado, separado del historial, con el mecanismo que ADK ofrece para eso
  (TASK.md líneas 229 y 572).
- **Precisa D2:** `{pending?}` queda como el nombre del resumen, no como sintaxis de ADK. El
  texto de D2 no se edita.
- **Descartadas:** una instrucción de texto con `{pending_summary?}` y una clave derivada que
  el nodo escribe en cada delta (un texto redundante en Redis que debe seguir a `pending`);
  `{pending?}` literal con el registro en otra clave (cambia el nombre que fija D6).
- **Pendiente:** la tarea 03 debe escribir `tools` también en una aclaración, con los nombres
  de las candidatas: D3 (*Aclaración*, *Estado*) solo lista `candidates`, y el resumen no la
  lee.

**D15 · Intérprete: una clave fuera del esquema se descarta** (2026-11-16) · autodecidida por
el orquestador; revisable

- **Decisión:** `TurnInterpretation` y sus modelos internos (`core/interpretation.py`)
  conservan el valor por defecto de Pydantic, sin `extra="forbid"`: una clave que el esquema
  no declara se descarta y el turno sigue. Lo prueba `test_unknown_keys_are_dropped`.
- **Motivo:** es lo mismo que hace el filtro por contrato de D2 con un argumento ajeno. El
  `response_schema` no lleva `additionalProperties`, que el SDK rechaza en modo Developer API
  cuando es verdadero (`google/genai/_transformers.py:650-654`, google-genai 2.34.0) y cuyo
  `false` no se puede comprobar sin llamar al modelo. Con `response_schema` la salida ya
  viene restringida, así que el caso es casi inalcanzable; y descartar una clave nunca
  ejecuta nada, porque los argumentos siguen pasando por fusión, validación y confirmación
  (TASK.md líneas 166-168 y 233-243).
- **Precisa D12:** D12 vale para los contratos, la última barrera antes del mock, donde una
  clave de más es un error del código; aquí la clave viene del modelo. El texto de D12 no se
  edita.
- **Descartada:** `extra="forbid"`, como en D12 (una clave ajena sería un `ValidationError`
  que responde "no te entendí", y se enviaría `additionalProperties: false` a AI Studio sin
  verificarlo).

**D16 · Intérprete: valores de reserva en el resumen de lo pendiente** (2026-11-16) ·
autodecidida por el orquestador; revisable

- **Decisión:** si al registro pendiente le falta una de las cuatro claves que lee el
  resumen, `pending_summary` escribe `none` (`tools`, `missing`, `last_question`) o `unknown`
  (`status`) en vez de fallar. Sin registro escribe "Nothing is pending.".
- **Motivo:** la tarea 03 crea siempre las cuatro claves, así que solo afecta a un registro
  mal formado; un fallo al armar la instrucción tumbaría el turno por un problema que no es
  del usuario (TASK.md línea 580).
- **Descartada:** leer las claves con `pending["status"]`, que lanza `KeyError` con un
  registro incompleto.
- **Pendiente:** ningún test lo cubre: los tests del resumen usan un registro completo o
  ninguno (`test_pending_summary_has_no_collected_values`,
  `test_pending_summary_when_nothing_is_pending`). [resuelto en la tarea 03]

**D17 · Pregunta de faltantes: sin valores permitidos** (2026-11-16) · autodecidida por el
orquestador; revisable

- **Decisión:** la pregunta de faltantes (`missing_question`, `core/replies.py`) nombra cada
  campo con su `label` del registro, en el orden del registro, y no añade "(alta, media o
  baja)" a `priority` ni "(correo, SMS o push)" a `channel`. Esa misma pregunta se guarda en
  `last_question`. Lo prueba `test_missing_question_has_no_collected_value_and_no_options`.
  [precisado en D43]
- **Motivo:** la pregunta del caso B no lista opciones (TASK.md línea 319). `last_question`
  llega al intérprete en el resumen de D14; con las opciones a la vista, el modelo puede
  convertir "urgente" en "alta", lo que D2 descarta (líneas 166-168). Un valor fuera de la
  lista lo rechaza el validador (D5), que pide la corrección (líneas 186-200).
- **Descartadas:** listar las opciones en la respuesta y en `last_question` (le enseña al
  modelo los valores válidos en cada turno de recopilación); listarlas solo en la respuesta
  (`last_question` deja de ser lo que el usuario leyó).

**D18 · Registro pendiente: una lista de pasos desde el primer flujo** (2026-11-16) ·
autodecidida por el orquestador; revisable

- **Decisión:** el registro que R2 crea en `pending` (`_start_flow`, `core/turn.py`) guarda
  `steps: [{tool, args}]` con un solo paso, junto a `status`, `tools`, `missing` y
  `last_question`; no guarda `tool` ni `args` en la raíz. `tools` se conserva aunque repita
  `steps[].tool`, porque D14 la lee y la aclaración la escribe sin `steps`. Lo prueban
  `test_case_a_creates_pending_and_asks_for_missing` y
  `test_pending_record_is_json_and_feeds_the_summary`.
- **Motivo:** una operación es un plan de un paso. Fusión, validación, confirmación y
  ejecución se escriben una vez sobre un paso, y el plan de D8.1 solo agrega pasos y
  `depends_on` (TASK.md líneas 212-224 y 378). Es la misma información con otra disposición
  dentro de `pending`: el usuario no ve cambio y no cambia cuándo se ejecuta nada.
- **Descartada:** `tool` y `args` en la raíz y `steps` solo con el plan (la tarea del plan
  reescribiría lo anterior o mantendría dos formas de registro).

**D19 · Registro pendiente: `last_question` nulo con la solicitud completa** (2026-11-16) ·
autodecidida por el orquestador; revisable

- **Decisión:** si la solicitud llega con todos los campos (`G1`, `H1`), R2 crea el registro
  con `missing: []` y `last_question: None`, y responde con la plantilla provisional "Tengo
  los datos; la validación y la confirmación aún no están disponibles." Nada se ejecuta. La
  clave existe (D14) y el resumen escribe `none`. La tarea 09b lo reemplaza al crear
  `awaiting_confirmation`. [`awaiting_confirmation` nace en la 10a; ver D44]
- **Motivo:** `last_question` es estado estructurado que el intérprete lee como la última
  pregunta al usuario (TASK.md líneas 216 y 229); guardar la plantilla provisional le
  mostraría algo que no es una pregunta.
- **Precisa D16:** el valor de reserva `none` de `last_question` ya no aparece solo con un
  registro mal formado: también con este registro completo. El texto de D16 no se edita.
- **Descartada:** guardar el texto provisional en `last_question`.
- **Pendiente:** ningún test lo cubre:
  `test_complete_request_creates_pending_but_does_not_execute` no comprueba `last_question`
  (revisión de la tarea 03). [resuelto en la tarea 03, ronda 2]

**D20 · `question` y `other` sin nada pendiente: la plantilla de capacidades** (2026-11-16) ·
autodecidida por el orquestador; revisable

- **Decisión:** sin registro pendiente, `question` y `other` responden con la misma
  plantilla que R1, generada de las `action_phrase` del registro ("Puedo ayudarte a crear un
  ticket, programar una reunión o enviar una notificación. ¿Qué necesitas?"). No tocan el
  estado ni inician nada, aunque traigan operaciones. Lo prueba `test_nothing_pending_acts`.
- **Motivo:** D3 (*Compuerta de entrada*, paso 4) solo pide "una plantilla"; una sola es la
  opción más chica. Una intención informativa no inicia un flujo (TASK.md línea 554), y la
  plantilla no nombra tools en el código (línea 562).
- **Descartada:** una plantilla propia para `question`, que empiece distinto.
- **Pendiente:** el test solo usa `question` y `other` sin operaciones; con una operación
  `explicit` nada fija que no se inicie un flujo (revisión de la tarea 03). [resuelto en la
  tarea 03, ronda 2]

**D21 · Log del turno: `ambiguity_reason` tal como lo escribe el modelo** (2026-11-16) ·
autodecidida por el orquestador; revisable

- **Decisión:** la línea de log de cada turno (`log_turn`, `assistant/agent.py`, logger
  `assistant.turn`) suma `ambiguity=<texto>` cuando el intérprete lo trae, sin recortarlo ni
  filtrarlo. Es texto libre del modelo y puede repetir un nombre o un dato del mensaje; el
  resto de la línea (`session`, `act`, `ops`, `route`, `error`) no lleva valores ni texto del
  usuario. Lo prueba `test_one_log_line_per_turn`, que deja fuera esa clave al comprobar que
  no aparecen "servidor" ni "Carlos".
- **Motivo:** D2 (*Solo para logs*) manda registrarlo, y el log local es la vía de
  observabilidad (TASK.md línea 471). Sin él, la prueba de humo de la tarea 06 no tiene cómo
  leer por qué el modelo vio ambigüedad en el caso C (líneas 338-355).
- **Descartadas:** quitarlo del log (contradice D2); recortarlo o enmascarar los nombres
  (exige reconocer nombres en texto libre).
- **Pendiente:** la documentación de observabilidad (tareas 19 y 20) debe decir que esa clave
  puede llevar texto del usuario.

**D22 · CLI: las trazas de ADK quedan en stderr** (2026-11-16) · autodecidida por el
orquestador; revisable

- **Decisión:** la CLI deja el logger raíz en WARNING y `assistant` en INFO, ambos a stderr,
  y no silencia el logger `google_adk`. El traceback que ADK registra cuando falla el
  intérprete ("Node execution failed with exception", p. ej. con un 429) sale en stderr
  aunque el nodo capture el error y responda con la plantilla. stdout lleva solo la
  conversación.
- **Motivo:** es el comportamiento que ya observó el spike (D1.1, *Resultado del spike*).
  Capturar solo stdout basta para que los ejemplos reproducibles salgan limpios (TASK.md
  línea 526), y la traza conserva el detalle de un fallo del modelo para la tarea 06 (líneas
  471 y 580).
- **Descartada:** subir el logger `google_adk` a CRITICAL (esconde fallos útiles).
- **Pendiente:** revisar la decisión si las tareas 15 o 17 capturan stderr.

**D23 · Redacción de las plantillas de fallo** (2026-11-16) · autodecidida por el
orquestador; revisable

- **Decisión:** `core/replies.py` define una plantilla por caso de D1.1 (*Fallos*):
  `NOT_UNDERSTOOD`, "No te entendí; no cambié nada. ¿Puedes decirlo de otra forma?", y
  `MODEL_UNAVAILABLE`, "El modelo no está disponible ahora; no cambié nada. Intenta de nuevo
  en un momento." Lo prueba `test_failed_turn_keeps_state` (JSON roto, fuera del esquema,
  salida vacía, 429 y timeout).
- **Motivo:** las dos dicen que el estado no cambió, que es lo que garantiza D1.1: el
  usuario puede repetir sin perder lo recopilado (TASK.md líneas 145 y 580).
- **Descartada:** una redacción propia para el 429, que D1.1 permite ("puede") y no exige.

**D24 · CLI: sesión, arranque y salida** (2026-11-16) · autodecidida por el orquestador;
revisable

- **Decisión:** `uv run python -m assistant.cli [--session ID]` (`assistant/cli.py`).
  - *Sesión:* sin `--session`, una sesión nueva (uuid) por ejecución; su id y el comando
    para continuarla salen en stderr. `user_id` fijo (`cli`).
  - *Arranque:* carga `.env`, comprueba Redis (`ping` a `REDIS_URL`, por defecto
    `redis://localhost:6379/0`) y después `GOOGLE_API_KEY`. Cada fallo sale con un mensaje
    en stderr, sin traceback, y código 1; el de Redis nunca muestra la contraseña de la URL.
  - *Salida:* una línea de stdin es un mensaje nuevo y las líneas en blanco se saltan. Con
    stdin por tubería, cada línea se repite como `> …` antes de la respuesta. Solo se
    imprimen los eventos con texto cuyo autor no es `interpreter` (D1.1, *Costos aceptados*).
  - *Log:* la línea de cada turno no lleva valores de argumentos ni el texto del mensaje; la
    tarea 08 suma los cambios de fusión con sus valores (D4 regla 8). [resuelto en D42]

  Lo prueban `test_chat_prints_only_replies`, `test_cli_exits_clearly_when_redis_is_down`,
  `test_cli_exits_clearly_without_api_key`,
  `test_run_creates_a_session_per_run_and_continues_it` y
  `test_redis_url_in_messages_never_shows_a_password`.
- **Motivo:** una sesión fija haría que cada ejecución heredara lo pendiente de la anterior
  y mezclaría los ejemplos reproducibles (TASK.md línea 526); `--session` muestra la
  continuidad del estado entre procesos (línea 227). Sin comprobar la clave, cada turno
  respondería "modelo no disponible", que confunde a quien instala (línea 528). Con el eco,
  la salida por tubería se lee como una transcripción.
- **Descartadas:** una sesión fija ("default"); dejar que Redis o la clave fallen en el
  primer turno (la conexión de `RedisSessionService` es perezosa).

**D3.1 · Enmienda a D3: un cambio de intención descarta todo lo pendiente** (2026-11-17) ·
decidida por el autor

- **Decisión:** en un cambio de intención (D3, *Compuerta de entrada*, paso 3, *Otra tool*, y
  *Lectura de la respuesta*, regla 4), la operación, el plan o la aclaración pendiente se
  archiva como abandonado (D6) y la tabla de reglas corre sobre todas las operaciones del
  turno. Ninguna tool conserva argumentos guardados, aunque ya estuviera pendiente o fuera
  candidata. Con `reject` o `cancel`, las operaciones de las tools pendientes también pasan a
  la tabla.
- **Motivo:** el caso F pide detectar el cambio, no mezclar argumentos e iniciar el flujo
  nuevo (TASK.md líneas 395-417); no pide conservar los de una tool que se repite. Ahorra una
  rama del núcleo y su test. Recorte del hallazgo 6 de la revisión del backlog (2026-11-17):
  esas dos reglas de D3 no tenían tarea.
- **Limitación para el README:** "mejor crea un ticket y avísale a Carlos" con una
  notificación pendiente empieza la notificación de cero y vuelve a pedir lo que el mensaje no
  repite. Con `reject` o `cancel`, una operación de la tool rechazada que el modelo devuelva en
  ese turno inicia un flujo; nada se ejecuta sin la confirmación de D7.1.

**D3.2 · Enmienda a D3: aclaración mínima** (2026-11-17) · decidida por el autor

- **Decisión:** una respuesta no clara a una aclaración (D3, *Lectura de la respuesta*, regla
  5) repite la misma pregunta y no cambia nada más. La regla 1 no cambia: `cancel` y `reject`
  descartan la aclaración. Se quitan el contador `attempts`, la repregunta cerrada, el
  descarte al segundo intento, sus plantillas y el test de que un fallo del intérprete no
  consume `attempts`.
- **Motivo:** el caso C pide preguntar y no ejecutar (TASK.md líneas 338-355); no pide un
  número de intentos ni una segunda pregunta distinta. Recorte de la nota 10 de la revisión
  del backlog (2026-11-17, tarea 13 adelgazada).
- **Consecuencia:** una aclaración sin respuesta clara sigue pendiente hasta que se resuelve,
  se cancela o llega un cambio de intención (D3.1). Un fallo del intérprete en ese turno sigue
  sin tocar el estado (D1.1).

**D8.4 · Enmienda a D8.1 y D8.2: dependencia inferida** (2026-11-17) · decidida por el autor

- **Decisión:** si el modelo pone `depends_on`, manda (D8.1). Si falta y el plan trae
  `create_ticket` y `send_notification`, el código infiere la dependencia: el ticket va
  primero y su `ticket_id` se añade al final de `message`, como en D8.2. La confirmación del
  plan lo muestra ("se añadirá el número de ticket") y el log anota que la dependencia se
  infirió. Dónde vive la condición lo fija D25.
- **Motivo:** el caso E pide ejecutar en el orden correcto y transferir el `ticket_id`
  (TASK.md líneas 388-390); no puede depender de que el modelo devuelva `depends_on`.
  Hallazgo 7 de la revisión del backlog (2026-11-17), opción A. El arnés de la fase 0
  (`phase0/run.py`) informa aparte si `E1` trae `depends_on`.
- **Limitación para el README:** una notificación no relacionada en el mismo plan recibe el
  número de ticket; se ve en la confirmación, antes del "sí" (D7.1).
- **Descartada:** no inferir y documentarlo como limitación (el ejemplo grabado del caso E
  incumpliría la línea 390).

**F0.2 · Enmienda a F0: la corrida de 207 no se hace** (2026-11-17) · decidida por el autor

- **Decisión:** se quita la tarea 16 (`phase0-full-run`) y se aplica la rama "si no se hace"
  de F0 tal como está escrita: el README dice que los umbrales de D2 se midieron solo con la
  prueba de humo de 23 llamadas; D3 conserva la marca "provisional" de la *Aclaración*; el
  *Disparador para pasar a P2′* no se mide; y P1 se mantiene. El umbral que decide es el de
  F0.1.
- **Motivo:** recorte de la nota 10 de la revisión del backlog (2026-11-17), que protege el
  plazo y la cuota de la capa gratuita. El foco de la evaluación es la orquestación, no el
  modelo (TASK.md líneas 587 y 597).

**D25 · Dependencia inferida: la condición sale de la declaración del registro** (2026-11-17)
· autodecidida por el orquestador; revisable

- **Decisión:** la inferencia de D8.4 se escribe contra la declaración que D8.2 pone en el
  registro (la tool que produce el ID y la que lo recibe), no con los nombres
  `create_ticket` y `send_notification` en el código: si falta `depends_on` y el plan contiene
  las dos tools de esa declaración, la receptora depende de la productora. La implementa la
  tarea 14a.
- **Motivo:** con las tres tools del catálogo se comporta igual que el texto del autor en
  D8.4; no cambia lo que ve el usuario, cuándo se ejecuta una tool ni qué se guarda. Evita un
  caso especial por nombre en el núcleo (TASK.md líneas 562 y 582).
- **Descartada:** nombrar las dos tools en el código, como dice D8.4 (cada par nuevo de tools
  que se pasen un ID sería otra rama).

**D26 · Arnés de la fase 0: se quita una cerca de código antes de validar** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** antes de `TurnInterpretation.model_validate_json`, el arnés
  (`strip_code_fence`, `phase0/run.py`) quita una cerca ```` ```json … ``` ```` que envuelva
  toda la salida, con las mismas comprobaciones que ADK y sin importar su módulo privado. El
  campo `raw` del JSONL guarda la salida intacta. Lo prueban
  `test_shape_failures_are_counted_and_the_run_goes_on` (una salida válida dentro de una
  cerca no es fallo de forma) y `test_empty_nested_object_needs_content_in_the_expected_output`.
- **Motivo:** ADK quita esa cerca antes de validar (`_strip_json_code_fence`,
  `utils/_schema_utils.py:214-252`, google-adk 2.14.1), así que el nodo del turno acepta esa
  salida. Contarla como fallo de forma detendría el trabajo por F0.1 y abriría la discusión
  del híbrido de D1 sin que el agente falle.
- **Descartada:** no quitarla y que la tarea 06 revise el `raw` de cada fallo `validation`
  antes de aplicar F0.1 (un paso a mano que puede olvidarse).

**D27 · Arnés de la fase 0: de dónde salen los registros pendientes** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** de los 13 escenarios de segundo mensaje (`phase0/scenarios.py`), los que ven
  un registro `collecting` lo toman del núcleo: `B2` el que crea `handle_turn(None, B1)`, y
  `F2` y `L1`–`L4` el de `handle_turn(None, F1)` (la reunión con Juan). Los demás ven un
  literal escrito a mano con `tools`, `status`, `missing=[]` y `last_question`, sin ningún
  valor recopilado, porque el núcleo aún no crea esos estados: `G2`, `awaiting_confirmation`
  con "¿Confirmas que envíe la notificación?"; y `K1`–`K3` y `S1`–`S3`,
  `awaiting_clarification` con `send_notification` y `schedule_meeting` y la pregunta de D3
  (*Pregunta*) armada con las `action_phrase` del registro. Lo prueba
  `test_the_23_scenarios_of_d2`, que además comprueba que ningún resumen lleva un valor dicho
  antes en su conversación.
- **Motivo:** donde el núcleo ya crea el registro, el modelo ve el mismo resumen de D14 que
  verá en producción, y un literal no puede separarse del núcleo sin que nada lo note.
- **Precisa D2:** D2 decía "`{pending?}` escrito a mano" para todos los segundos mensajes. El
  texto de D2 no se edita.
- **Costo aceptado:** los literales de confirmación y aclaración no se actualizan cuando el
  núcleo cree esos estados, así que pueden diferir del registro que verá el modelo en
  producción.
- **Descartada:** los 13 registros escritos a mano, como decía D2.

**D28 · Arnés de la fase 0: reintentos ante 429 y 503, y reanudación** (2026-11-17) · decidida
por el autor

- **Decisión:** solo el arnés (`phase0/run.py`) da al modelo `HttpRetryOptions` de
  google-genai, con `Gemini(model=MODEL, retry_options=RETRY_OPTIONS)` (`harness_model`): hasta
  3 intentos por llamada, con espera exponencial y jitter de 2 a 30 s, ante un 429 o un 503
  (D30). Gana además `--resume RUTA`, solo junto con `--run`: copia lo que un JSONL anterior ya
  guardó y llama solo a los escenarios que faltan (D29). La espera entre llamadas reales es de
  13 s. El nodo del turno y el intérprete de `assistant/` siguen sin reintentos (D1.1)
  [enmendado en D1.2]. Lo prueban `test_harness_keeps_its_own_retries` y
  `test_resume_calls_only_what_was_not_saved`.
- **Motivo:** la primera corrida de humo se detuvo en `N1`, 12 de 23, por un
  `503 UNAVAILABLE` ("high demand"), no por cuota. Repetirla entera gastaría cuota de la capa
  gratuita en 11 salidas ya guardadas. El arnés es una herramienta de medición, no el turno: no
  enmienda D1.1, cuyo motivo (un `RetryConfig` vuelve a ejecutar el cuerpo del nodo) sigue en
  pie (TASK.md líneas 471 y 580).
- **Espera:** la página de límites (`ai.google.dev/gemini-api/docs/rate-limits`, que redirige
  a `aistudio.google.com/docs/rate-limits`, leída el 2026-11-17) no publica RPM, RPD ni TPM de
  la capa gratuita para `gemini-3.5-flash-lite`. Por eso 13 s, que cabe en 5 RPM, y no un
  límite más alto adivinado (D2, *Umbral*).
- **Precisa D1.1:** `retry_config` (el reintento de un nodo de ADK, que D1.1 prohíbe) y
  `HttpRetryOptions` (el del cliente HTTP de google-genai) son mecanismos distintos; ninguno
  llega a `assistant/` [enmendado en D1.2]. El texto de D1.1 no se edita.
- **Hechos de google-adk 2.14.1 y google-genai 2.34.0** (para las tareas 19 y 20):
  - `Gemini.retry_options` vale `None` por defecto (`google/adk/models/google_llm.py:188`), y
    con `None` google-genai hace un solo intento (`tenacity.stop_after_attempt(1)`,
    `google/genai/_api_client.py:678`). El nodo del turno hace una sola llamada por turno.
  - Sin reintentos, el 503 de la primera corrida salió de `runner.run_async` como la
    `google.genai.errors.ServerError` original: ADK la desenvuelve de `DynamicNodeFailError` al
    terminar el nodo raíz (`google/adk/workflow/_node_runner_utils.py:276`). En el nodo del
    turno es el caso 2 de D1.1: "modelo no disponible", sin tocar el estado.
  - `APIError` trae el código HTTP en `.code` y no define `status_code`
    (`google/genai/errors.py:91`). El `getattr(ex, "status_code", None) == 429` que D1.1
    permite nunca coincidiría; no tiene efecto, porque D23 descartó una redacción propia para
    el 429.
  - Un 429 real no se observó en ninguna de las dos corridas: esa parte del *Sin verificar* de
    D1.1 sigue abierta. En la segunda corrida hubo otro 503, que absorbió el reintento del
    arnés.

**D29 · Arnés de la fase 0: `--resume` escribe un archivo nuevo** (2026-11-17) · autodecidida
por el orquestador; revisable

- **Decisión:** `--resume RUTA` lee el JSONL anterior y da por guardado cada escenario cuya
  línea trae `raw` y no trae `error` (`load_saved`). Escribe un JSONL nuevo, con el nombre de
  siempre (`<fecha>-<modelo>.jsonl`) y una línea por escenario en el orden de `SCENARIOS`: la
  de un guardado se copia byte a byte y la de uno que falta sale de su llamada. La espera corre
  solo entre llamadas reales, y el archivo anterior no se toca.
  - *Archivo anterior inválido:* una línea que no es JSON o no es un registro del arnés, un id
    repetido o desconocido, o un `fixture` o `message` que no coincide con el escenario actual
    → código 2 antes de leer `.env`, sin llamadas y sin archivo nuevo.
  - *Informe:* suma "Reanudados de `<archivo>`: m; llamadas en esta corrida: k". El veredicto
    de F0.1 sale sobre los 23 del archivo nuevo, con las mismas condiciones. Un error del
    modelo, tras los reintentos, sigue deteniendo la corrida sin veredicto.

  Lo prueban `test_resume_calls_only_what_was_not_saved`,
  `test_model_error_while_resuming_stops_without_verdict`,
  `test_invalid_previous_file_calls_nothing`, `test_wait_runs_only_between_real_calls` y
  `test_resume_needs_run`.
- **Motivo:** anexar al archivo de la primera corrida dejaría `N1` dos veces y cambiaría la
  evidencia ya escrita. Con un archivo nuevo, cada corrida queda como pasó y F0.1 se lee de un
  solo archivo de 23 líneas. Copiar la línea, y no volver a serializarla, garantiza que la
  salida guardada es la que dio el modelo.
- **Descartada:** anexar las líneas nuevas al JSONL anterior.

**D30 · Arnés de la fase 0: los reintentos cubren solo 429 y 503** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** `RETRY_OPTIONS` (`phase0/run.py`) fija `http_status_codes=[429, 503]`, con
  `attempts=3`, `initial_delay=2.0` y `max_delay=30.0`; `exp_base` y `jitter` quedan en los
  valores por defecto del SDK (2 y 1).
- **Motivo:** 429 y 503 son los dos códigos con que la API pide volver a intentar. Un 500
  puede ser un error del pedido, y repetirlo gasta cuota de la capa gratuita sin cambiar el
  resultado.
- **Desviación aceptada:** con `retry_options`, el SDK reintenta siempre sus errores de red
  transitorios (timeouts y fallos de conexión de httpx y aiohttp,
  `google/genai/_api_client.py:685`), además de los códigos de `http_status_codes`. El
  implementador lo informó, el evaluador lo aceptó y el comentario de `RETRY_OPTIONS` lo dice.
  El tope de 3 intentos por llamada no cambia.
- **Descartada:** la lista por defecto del SDK (408, 429, 500, 502, 503 y 504,
  `google/genai/_api_client.py:652-659`).

**D31 · Arnés de la fase 0: una línea en stderr por cada reintento** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** solo bajo `--run`, `_log_http_retries` sube el logger `google_genai._api_client`
  a INFO, con un handler a stderr que se agrega una sola vez. google-genai escribe ahí una línea
  antes de cada reintento (`tenacity.before_sleep_log`, `google/genai/_api_client.py:699`). Sin
  `--run` nada cambia. Lo prueba `test_retry_log_goes_to_stderr_once`.
- **Motivo:** el SDK no registra cada petición, así que las peticiones HTTP se cuentan como
  llamadas más líneas de reintento y se comparan con el techo que aprobó el autor (12
  llamadas, 36 peticiones). Es la observabilidad del arnés (TASK.md línea 471).
- **Descartada:** no registrar los reintentos (el techo aprobado no se podría comprobar).

**D32 · Resultado de la prueba de humo: 0/23 fallos de forma, se mantiene la opción B**
(2026-11-17)

- **Decisión:** por F0.1, con 0/23 fallos de forma se mantiene la opción B (D1): ni el
  híbrido ni un ajuste del esquema. Se midió con `gemini-3.5-flash-lite` (Google AI Studio,
  capa gratuita) en dos corridas, cada una con su nota del mismo nombre en `phase0/runs/`:
  - `20261117T072304Z-gemini-3.5-flash-lite.jsonl` se detuvo en `N1`, 12 de 23, por un
    `503 UNAVAILABLE`, con 0 fallos de forma en las 11 salidas;
  - `20261117T073844Z-gemini-3.5-flash-lite.jsonl`, con `--resume` sobre la primera (D29),
    copió esas 11 y llamó a las 12 que faltaban: 23 de 23, sin errores y 0 fallos de forma.
    Hubo 13 peticiones HTTP (12 llamadas y un reintento por un 503), con un techo aprobado de
    36. Su nota es la de F0.1.

  Un recuento sin modelo (`shape_failure` sobre cada `raw`) coincide con lo guardado, línea
  por línea.
- **Motivo:** F0.1 deja decidir solo a los fallos de forma, y no hubo ninguno. Los desaciertos
  de comprensión son informativos y no se vuelven a medir (F0.2): D3 conserva la marca
  "provisional" y el disparador de P2′ no se mide.
- **Lecturas que llevan a otras decisiones** (una corrida por escenario; no juzgan los ≥ 7/9
  de D2):
  - *Caso C:* `C1` volvió como una sola `send_notification` `explicit`, sin `schedule_meeting`
    y sin `ambiguity_reason`, igual que su contraste `N1`. Cae en R2 y el agente pediría el
    canal: la *Limitación (omisión)* de D3, ahora medida (TASK.md líneas 338-355). La sonda
    `S3` volvió igual y resolvería la aclaración a la notificación, que D2 (*Sondas*) llama
    fallo crítico; nada se ejecuta (D7.1). Lo toma D33.
  - *`L4`:* "Quita a Juan y agrega a Ana" trajo las cubetas correctas, pero en dos operaciones
    de `schedule_meeting`. D11 respondería con su pregunta y no aplicaría nada (TASK.md líneas
    143-145). Lo toma D11.1.
  - *Segundos mensajes:* `K1`, `K2`, `L2` y `L4` llegaron con `act=request_action` donde se
    esperaba `provide_info`. D3 (*Misma tool* y regla 2 de la *Lectura de la respuesta*)
    acepta los dos, y `K1`–`K3`, `S1`, `S2` y `L1`–`L3` dan lo que el código debe hacer.
  - *Favorables* (preguntas 1 y 8 de §9, TASK.md líneas 552 y 560): `A1` no trae `title`;
    `X1` copia "urgente"; `D1` trae 40, 25 y 90 como se dijeron, y el mes convertido a 11;
    `E1` trae `depends_on`, así que la inferencia de D8.4 no hizo falta; `G2` trae
    `confirms_tool`; ningún número que el mensaje no contuviera.
- **Pendiente:** si D33 cambia descripciones del esquema, el 0/23 y estas lecturas quedan
  medidos con las anteriores; la tarea 19 dice con qué versión se midió cada cosa (D34). [resuelto en D52]

**D33 · Caso C con el modelo real: el camino de D2, en la tarea 10c** (2026-11-17) · decidida
por el autor

- **Decisión:** una tarea nueva antes de la 13, la 10c (≤ 1 h), ajusta solo descripciones de
  campo del esquema del intérprete (`certainty` y `plausible` y, si hace falta,
  `ambiguity_reason`, en `core/interpretation.py`), sin prompt extenso ni reglas por palabra
  clave. Después vuelve a medir con el modelo real: 5 llamadas propuestas (`C1`, `N1`, `S3`,
  `A1` y `E1`; D34) y mínimo `C1`, `N1` y `S3`, con el número aprobado por el autor al llegar
  a la 10c [resuelto en D51]. Pasa si, por las filas de D3, `C1` cae en R4 o R5 y la pregunta lista la
  notificación y la reunión; `N1` cae en R2 solo con `send_notification`; `S3` no resuelve a
  ninguna candidata; y `A1` y `E1` dan lo mismo que su interpretación fija.
  - Si falla `C1`, `N1`, `A1` o `E1`, se revierten las descripciones y el caso C queda como
    limitación documentada (tareas 17 y 20).
  - Si solo falla `S3`, las descripciones se quedan, la tarea 13 lo anota y la 20 lista la
    limitación.
- **Motivo:** con el modelo real el caso C se lee como una notificación (D32), y el agente
  pediría el canal en vez de aclarar (TASK.md líneas 338-355). D2 prevé mejorar descripciones
  ante un fallo de comprensión; el híbrido no aplica, porque D2 lo reserva a fallos de forma.
  Una descripción de campo no es un prompt que decida (líneas 109 y 587).
- **Precisa D2:** la respuesta del autor no incluye el paso de D2 de probar
  `gemini-3.8-flash`: si las descripciones no lo cierran, el caso C pasa a limitación. El
  texto de D2 no se edita.

**D11.1 · Enmienda a D11: se fusionan las operaciones de una tool pendiente que no se solapan**
(2026-11-17) · decidida por el autor

- **Decisión:** solo en la rama *Misma tool* de la compuerta de D3 (paso 3: todas las
  operaciones del turno son de tools pendientes y `act` es `request_action` o `provide_info`),
  las operaciones de una misma tool cuyos `args` no comparten ninguna clave se fusionan en una
  antes de D11. Cada cubeta `_add` y `_remove` cuenta como clave propia. Si alguna clave se
  repite, o si traen `depends_on` distintos (D35), sigue la pregunta de D11. Sin nada
  pendiente, o con otra tool en el turno, D11 no cambia: dos tickets pedidos no se vuelven
  uno. La implementa la tarea 08 [corregido en D42: la 08b]; hasta entonces `core/turn.py` (`_decide`) aplica D11 tal
  como está escrita. [resuelto en D54]
- **Motivo:** en la fase 0, `L4` ("Quita a Juan y agrega a Ana", con la reunión con Juan
  pendiente) trajo las cubetas correctas partidas en dos operaciones de `schedule_meeting`
  (D32), y D11 no aplicaba nada. TASK.md líneas 143-145 piden actualizar lo recopilado con la
  información nueva. La rama *Misma tool* ya trata el turno como `provide_info` de la tool
  pendiente (D2, D3), así que la fusión completa una solicitud y no mezcla dos (líneas
  128-134 y 413).
- **Alcance:** la respuesta del autor (2026-11-17) limita la fusión a una tool ya pendiente.
  La revisión de la tarea 06 (ronda 3, hallazgo 1) la ciñe a la rama *Misma tool*: con dos
  operaciones de la tool pendiente y una `explicit` de otra tool, D3.1 archivaría lo pendiente
  y la fusión uniría dos tickets pedidos en un flujo nuevo.
- **Precisa D11:** la opción "juntarlas en una sola operación" de sus *Descartadas* sigue
  descartada fuera de esta rama. El texto de D11 no se edita.
- **Pendiente** (para la brief 08): si las claves se cuentan después del filtro del registro
  (el `L4` medido trae `"message": null` en las dos operaciones) [resuelto en D54]; cómo llega `depends_on` a la
  fusión, porque `_filter_by_registry` (`core/turn.py`) hoy lo descarta [resuelto en D42; nulo frente a un valor, en D54]; y si una candidata de
  una aclaración cuenta como tool pendiente [resuelto en D54].

**D34 · Tarea 10c: cinco llamadas propuestas y el umbral de F0.1** (2026-11-17) · autodecidida
por el orquestador; revisable

- **Decisión:** la 10c propone al autor 5 llamadas (`C1`, `N1`, `S3`, `A1` y `E1`), con
  mínimo `C1`, `N1` y `S3`; el autor aprueba el número al llegar a la 10c [resuelto en D51]. F0.1 vale también
  para ellas: un fallo de forma detiene el trabajo y se pregunta al autor. Si el autor aprueba
  solo 3, la tarea 19 dice que el 0/23 y las lecturas favorables de D32 se midieron con las
  descripciones anteriores.
- **Motivo:** las descripciones son compartidas por los 23 escenarios (D3, *Disparador*).
  `A1` vigila los ejemplos claros de RF-01 (TASK.md líneas 113-117) y `E1` el caso E (línea
  378); sin ellos, una regresión se vería recién en la grabación con el modelo real de la
  tarea 17.
- **Descartada:** medir solo las 3 llamadas de la respuesta del autor sin decir con qué
  descripciones se midió lo demás.

**D35 · Fusión de D11.1: sin exigir la misma `certainty`; no con `depends_on` distintos**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** la fusión de D11.1 no exige que las operaciones traigan la misma `certainty`.
  Si traen `depends_on` distintos, no se fusionan y sigue la pregunta de D11.
- **Motivo:** con la tool pendiente, D2 y la rama *Misma tool* de D3 ya ignoran la
  `certainty`; exigirla mandaría a D11 un `L4` con una `explicit` y una `plausible`. Dos
  `depends_on` distintos son dos órdenes posibles de un plan, y fusionarlos obligaría al
  código a elegir uno (TASK.md línea 252).
- **Descartadas:** exigir la misma `certainty`; quedarse con el `depends_on` de la primera
  operación.

**D1.2 · Enmienda a D1.1: el intérprete del agente reintenta un 503 una vez** (2026-11-17) ·
decidida por el autor

- **Decisión:** el intérprete del agente en vivo (CLI y `adk web`) usa un `Gemini` con
  `retry_options` de google-genai (`interpreter_model()` e `INTERPRETER_RETRY_OPTIONS`,
  `assistant/interpreter.py`): 2 intentos en total, solo ante un 503, espera inicial de 1 s y
  máxima de 5 s. Con 2 intentos hay una sola espera, de 1–2 s (D36). El 429 no se reintenta.
  Agotados los intentos sale la `ServerError` original, que es el caso 2 de D1.1: "modelo no
  disponible", sin tocar el estado. El `retry_config` del nodo del turno sigue en `None` y la
  reanudación sigue desactivada. El arnés conserva su propia configuración (D28, D30), porque
  siempre pasa su propio modelo.
- **Motivo:** el intérprete no tiene efectos (sin tools ni `output_key`, D1 y D1.1), y la
  ejecución de una tool va aparte, con su clave de idempotencia (D9): repetir su petición no
  puede ejecutar una tool dos veces (TASK.md líneas 286 y 558). El reintento ocurre antes de
  cualquier ejecución, y el de una tool nunca es automático (D8.3; líneas 272 y 559). Un pico
  breve de demanda, como el 503 de la tarea 06 (D28), se vuelve una respuesta más lenta en vez
  de "modelo no disponible", y la espera queda acotada a unos segundos (líneas 25, 497 y 580).
- **Sin 429:** es la cuota de la capa gratuita agotada; esperar unos segundos no la devuelve,
  y repetir gasta cuota y vuelve a fallar.
- **Sigue en pie D1.1:** un `RetryConfig` en el nodo volvería a ejecutar el cuerpo del turno
  y gastaría cuota. Este reintento vive debajo, en el cliente HTTP del modelo del intérprete:
  el nodo sigue haciendo una sola llamada al modelo por turno, que ahora puede ser hasta 2
  peticiones HTTP. El texto de D1.1 y de D28 no se edita; llevan la marca.
- **Desviación aceptada por el autor (red transitoria):** con cualquier `retry_options`,
  google-genai reintenta además `httpx.ConnectError` y `httpx.TimeoutException`
  (`google/genai/_api_client.py:96-101` y `:683-687`, google-genai 2.34.0), y no hay un
  interruptor público para apagarlo. Por eso el intérprete del agente también reintenta una
  vez un fallo de conexión, no solo un 503; el tope sigue en 2 intentos. El autor lo aceptó
  para el agente, no solo para el arnés (D30), en el pedido de la unidad 06b ("Document this
  rather than fight it"). google-genai no fija un timeout por petición
  (`_api_client.py:1414-1415`), así que en la práctica cubre fallos de conexión, no esperas
  largas.
- **Lo prueban:** `test_interpreter_configuration` (el intérprete lleva un `Gemini` con
  `INTERPRETER_RETRY_OPTIONS`), `test_interpreter_retry_options` (2 intentos, `[503]`, 1.0 y
  5.0 s, `exp_base` y `jitter` sin fijar), `test_http_retry_through_the_turn_node` (por el
  `Runner` y el nodo, sin red: `503_then_ok`, `503_twice`, `429`, `500` y
  `connect_error_twice`, con 2, 2, 1, 1 y 2 peticiones; los fallos dan "modelo no disponible"
  con el `pending` intacto), `test_harness_keeps_its_own_retries` (el arnés no cambia y su
  configuración es otra) y `test_no_retry_config_and_rerun_on_resume` (el nodo sigue sin
  `retry_config`).
- **Sin verificar:** un 503 real de Gemini (la forma exacta del cuerpo de error y la espera
  real). No se hizo ninguna llamada real; el cuerpo simulado usa la forma que lee
  `google/genai/errors.py`.

**D36 · Reintento del intérprete: espera del SDK, sin marca en el log y prueba sin red**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:**
  - *Espera:* `exp_base` y `jitter` quedan en los valores del SDK (2 y 1), como D30. Con 2
    intentos la única espera es `initial_delay` más un jitter de 0 a 1 s, es decir 1–2 s
    (`tenacity/wait.py:266-273`); `max_delay=5.0` solo acota si alguien sube `attempts`.
  - *Log:* un turno reintentado que termina bien no deja marca en la línea `assistant.turn`
    (D21) y lleva los mismos campos que uno sin reintento. La línea INFO que google-genai
    escribe antes de cada reintento (`_api_client.py:699`) tampoco sale en la CLI, que deja el
    logger raíz en WARNING (D22); solo se nota la latencia. Agotados los intentos, la línea
    dice `route=model_unavailable error=<tipo>`, como cualquier fallo del modelo.
  - *Prueba:* además de las aserciones de configuración, una de punta a punta con un `Gemini`
    real sobre `httpx.MockTransport` (APIs públicas `Gemini.client_kwargs` y
    `HttpOptions.httpx_async_client`), con los intentos y códigos de producción y las esperas
    anuladas: sin red, sin clave real y sin módulos privados.
  - *Nombres:* `build_interpreter()` sin modelo usa `interpreter_model()`, así que el
    intérprete de producción y el que se construye por defecto son el mismo;
    `INTERPRETER_RETRY_OPTIONS` no se confunde con `phase0.run.RETRY_OPTIONS`.
- **Motivo:** ninguna cambia lo que ve el usuario, cuándo se ejecuta una tool ni qué se
  guarda. La prueba de punta a punta es la única que comprueba que tras dos 503 sale la
  plantilla sin cambio de estado, y no gasta cuota (TASK.md líneas 522 y 580).
- **Descartada:** probar el reintento con el modelo real (gasta cuota y un 503 no se puede
  provocar).
- **Pendiente:** la tarea 19 documenta que un reintento que termina bien no deja rastro en la
  CLI, como límite de observabilidad (TASK.md línea 471).

**D37 · Tarea 07: detalles de la fusión de D4** (2026-11-17) · autodecidida por el
orquestador; revisable

- **Decisión:** `core/merge.py` (`merge_args`, `anchored`) fija estos detalles de D4. Ninguno
  cambia lo que ve el usuario fuera de entradas mal formadas, así que van en una línea cada
  uno, por la regla del autor del 2026-11-17 (hallazgo 10 de la revisión del backlog).
  - *Palabra completa (regla 3):* las palabras `\w+` del texto normalizado ("asígnalo" es una;
    "Ana-María", dos).
  - *Agregar uno que ya está (regla 6):* se mide contra la base del turno (`<campo>` si llegó; si
    no, lo guardado).
  - *Mismo nombre en `_add` y `_remove` (regla 6):* solo la nota `add_and_remove`, nunca también
    `remove_absent`; si estaba guardado, se quita.
  - *`Rejected.key`:* la clave tal como llegó (`attendees_add`), para el log de la tarea 08; la
    respuesta no muestra el nombre (regla 9).
  - *Lo guardado (regla 10):* pasa por `presence.clean` y solo quedan campos del contrato.
  - *Reemplazo con todos sus nombres rechazados (reglas 2 y 3):* cuenta como no enviado; la
    lista guardada se conserva.
  - *`_remove` (regla 3):* sus nombres también se anclan ("cada nombre de cada cubeta").
  - *Nombre repetido en una cubeta (regla 2):* se guarda una vez, con la primera grafía, sin
    nota.
  - *Igualdad fuera de cadenas (regla 5):* exige el mismo tipo; `30` → `30.0` es un
    `overwrite`.
- **Motivo:** es la lectura más literal de las reglas 2, 3, 5, 6 y 10 de D4 (TASK.md líneas
  145 y 184).
- **Pendiente:** "agregar uno que ya está" contra la base del turno no tiene test (revisión de
  la tarea 07, ronda 3, hallazgo 1); lo cierra la tarea 08. Por la regla 9 de D4, si el modelo
  llena `attendees` y `attendees_add` con el mismo nombre en un flujo nuevo, la respuesta dirá
  que ya estaba aunque no había nada guardado. [resuelto en D42]

**D38 · Fusión: un nombre igual al guardado pero no escrito en el mensaje se rechaza**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** `merge_args` ancla cada valor `person` y cada nombre de lista que llega en el
  turno, aunque coincida con el guardado. Con `assignee="Carlos"` guardado, un
  `assignee="Carlos"` que llega con "Sí, hazlo" da `Rejected("assignee", "Carlos")`, ningún
  cambio y "Carlos" sigue guardado. Son D4 reglas 3 y 4 al pie de la letra: la regla 4 exime
  al valor guardado, no al que llega igual.
- **Lo que ve el usuario:** por lo que D4 exige a D7 (D7, *Cumple*), ese "sí" no cuenta: no
  ejecuta, la respuesta lleva la línea fija de nombre rechazado (D4 regla 9) y vuelve a pedir
  la confirmación. En el caso G (TASK.md líneas 430-438), un modelo que repitiera el
  destinatario en cada "sí" impediría ejecutar.
- **Motivo:** solo pasa si el modelo inventa el valor, porque no ve lo guardado
  (`include_contents='none'` y resumen sin valores, D2 y D14); un nombre que el mensaje no dice
  no viene del usuario (línea 184). Aceptarlo por coincidir con lo guardado haría depender el
  anclaje de lo guardado, y el "sí" se ata a la operación mostrada (líneas 282-284). En la
  prueba de humo, `G2` ("Sí, hazlo") volvió sin operaciones (D32).
- **Descartada:** no anclar un valor igual al guardado (sin cambio ni rechazo; el "sí" contaría).
- **Pendiente:** ningún test lo fija; las revisiones de la tarea 07 lo corrieron a mano. Las
  tareas 09b y 10a (caso G) lo tienen en cuenta. [test en D47] Vale mientras `last_question` no lleve valores
  (D2, D14): si la 09b guarda ahí la confirmación de D7.1 con los valores normalizados, el
  modelo los vería y podría repetirlos. [la confirmación nace en la 10a y su pregunta guardada
  no lleva valores; ver D43]

**D39 · Fusión en el turno: cada `fill` se muestra antes de la pregunta** (2026-11-17) ·
decidida por el autor

- **Decisión:** R2 y la rama *Misma tool* de D3 muestran cada `fill` antes de la pregunta,
  igual que un `overwrite` o un `clear` ("Anoté la descripción: el servidor está caído."). R2
  fusiona contra un registro vacío (D4 regla 4), así que todo lo que trae es `fill`.
  `last_question` sigue siendo solo la pregunta (D17), sin esas líneas ni valores (D2, D14).
  Respuesta del autor a la brief 08, opción (a).
- **Motivo:** es la lectura literal de D4 regla 9 ("se muestra cada cambio"), y la regla 8
  cuenta `fill` como cambio. En el caso A muestra que solo se extrajo la descripción (TASK.md
  líneas 302-304); en el caso B, que se incorporó lo nuevo (líneas 327-331).
- **Lo que ve el usuario:** una línea por campo recibido antes de la pregunta, desde el primer
  mensaje. No cambia cuándo se ejecuta nada ni qué se guarda.
- **Precisa D4 y D19:** la regla 9 de D4 vale también para R2, no solo para lo que llega con
  algo pendiente; y con una solicitud completa, la plantilla provisional de D19 va después de
  las líneas `fill`. El texto de D4 y de D19 no se edita.
- **Descartada:** que R2 muestre solo notas y rechazos, y que los `fill` se vean en *Misma
  tool* y en la confirmación (D7.1).
- **Lo prueban:** `test_same_tool_merges_and_asks_for_the_rest`, `test_case_b_until_complete`,
  `test_new_flow_note_is_true` y `test_case_a_creates_pending_and_asks_for_missing`.

**D40 · Con algo pendiente, un turno sin operaciones es *Misma tool*** (2026-11-17) · decidida
por el autor

- **Decisión:** con un registro pendiente, `provide_info` o `request_action` sin ninguna
  operación entra por la rama *Misma tool* de D3 (paso 3): la fusión no recibe nada, el estado
  no cambia y se repite la pregunta pendiente [precisado en D57: salvo que el paso del tiempo
  cambie la validez]. Respuesta del autor a la brief 08, opción (a).
- **Motivo:** es D3 al pie de la letra ("todas las operaciones del turno son de tools
  pendientes" se cumple con ninguna), y es lo que D9 (*Caso H*) pide para un mensaje que no
  aporta nada (TASK.md líneas 145 y 447).
- **Lo que ve el usuario:** la pregunta pendiente en vez de la plantilla provisional. No cambia
  cuándo se ejecuta nada ni qué se guarda.
- **Precisa D3:** la rama *Misma tool* (paso 3) incluye el turno sin operaciones. El texto de
  D3 no se edita.
- **Descartada:** dejar la plantilla provisional y que lo decida la tarea 12.
- **Lo prueba:** `test_pending_without_operations_repeats_the_question`.

**D41 · Precisa D10: con algo pendiente, una solicitud que el filtro descarta lleva la línea de
D10** (2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** si el filtro del registro descartó alguna operación y hay un registro
  pendiente, la respuesta antepone la línea fija de D10 ("Parte de tu solicitud no corresponde a
  ninguna de mis herramientas.") aunque no quede ninguna operación. Con `A1` pendiente y solo
  una tool fuera del catálogo, el turno entra por *Misma tool* sin operaciones (D40): línea de
  D10 y la pregunta pendiente, con el estado intacto. Sin nada pendiente no cambia: R1 responde
  con las capacidades, sin la línea.
- **Cambia lo que ve el usuario:** suma una línea a la respuesta en un caso que D10 no cubría
  ("queda otra operación" o "no queda nada" sin pendiente). Por eso queda a la vista del autor
  como revisable, aunque la regla del encabezado reserva al autor ese tipo de decisión.
- **Motivo:** D10 descartó que una tool desconocida se descarte en silencio (el usuario no sabe
  que parte de su solicitud se ignoró). La opción (a) de D40 cubre un turno que el intérprete
  devuelve sin operaciones, no uno cuyas operaciones filtró el registro (TASK.md líneas 107 y
  128-137). Should-fix 1 de la revisión de la tarea 08, ronda 1.
- **Alcance real:** hoy la línea precede a cualquier respuesta con algo pendiente, no solo a la
  pregunta repetida: también a la plantilla provisional (`confirm`, `cancel`, `question` o un
  turno mixto con una tool fuera del catálogo, y un registro que no está en `collecting`) y a
  `READY_NOT_AVAILABLE_YET` con un registro completo. Ningún test fija esa parte (revisión de la
  tarea 08, ronda 2, nota 1). Las tareas 10a y 12, que reemplazan esas plantillas, deben
  decidirla y probarla en vez de heredarla. [la parte de la 10a, resuelta en D45 y D47] [la parte
  de la 12, resuelta en D71]
- **Redacción:** "Parte de tu solicitud" es inexacta cuando la única operación del turno era la
  descartada; se acepta por ahora.
- **Descartada:** dejar la pregunta pendiente sola, sin aviso (el descarte silencioso que D10
  descartó).
- **Lo prueban:** `test_pending_and_unknown_tool_adds_the_d10_line` (también que un turno sin
  operaciones no lleva la línea) y `test_pending_and_unknown_tool_through_the_node`.

**D42 · Tarea 08: la fusión de D4 conectada al turno** (2026-11-17) · autodecidida por el
orquestador; revisable

- **Decisión:** `core/turn.py` pasa R2 (contra `{}`) y la rama *Misma tool* (contra el paso de
  cada tool, D18) por `merge_args`, y `core/replies.py` escribe las líneas de D4 regla 9. Ninguno
  de estos detalles cambia lo que ve el usuario más allá de D39–D41, así que van en una línea
  cada uno (regla del autor desde la tarea 07):
  - *Entradas del turno:* lo que no viene del intérprete llega en `TurnInput` (dataclass
    congelada); hoy solo `message`, la 09b suma `today` y la 11 `session_id`. [precisado en
    D58: la 09b suma `now`, con fecha y hora]
  - *Texto del mensaje:* el nodo lo toma del `types.Content` que recibe
    (`workflow/_node_runner_utils.py:136-141`, google-adk 2.14.1) y une sus partes de texto con
    un espacio; solo se usa para anclar, nunca va al estado ni al log.
  - *Log (D4 reglas 3 y 8, D24):* todo turno entendido suma, al final y antes de `ambiguity=`,
    `changes=` (`campo:tipo:anterior->nuevo`, valores en JSON) y `rejected=` (`clave:nombre`),
    aun vacíos; esas dos claves llevan valores del usuario y la línea ya no se parte por espacios.
  - *Solo `collecting`:* *Misma tool* exige un registro en `collecting`; los demás estados siguen
    con la plantilla provisional (10a y 13). [`awaiting_confirmation` resuelto en D47]
  - *Sin cambios:* un turno *Misma tool* que no cambia nada devuelve un delta vacío; uno con solo
    una nota o un rechazo tampoco cambia el estado y muestra esas líneas antes de la pregunta.
    [precisado en D57: salvo que el paso del tiempo cambie la validez]
  - *Varios pasos:* `missing` y las preguntas de cada paso se concatenan; la 14a decide esa
    redacción.
  - *Redacción:* "Anoté {etiqueta}: {valor}.", "Cambié {etiqueta}: {antes} → {después}.",
    "Vacié {etiqueta} (antes: {lista}).", "{nombre} ya está en {etiqueta}." y una línea fija de
    nombre rechazado que no muestra el nombre.
  - *Nota `add_present`:* en presente, así que también es cierta en un flujo nuevo cuya lista
    trajo el nombre en este turno.
  - *Fechas y horas:* se muestran con los componentes tal como llegaron ("día 40, mes 11",
    "25:90", "las 10"), sin convertir; resolverlas es de D5 (09b).
  - *Filtro del registro:* `filter_by_registry` pasa a público y conserva `depends_on` (nulo si
    no vino); R2 ya no filtra por segunda vez.
  - *Cubetas:* `list_buckets` (`core/registry.py`) da los nombres `_add`/`_remove` a
    `core/merge.py` y `core/turn.py`; no se aplica al texto del esquema ni de la instrucción,
    medidos así en D32.
- **Motivo:** TASK.md líneas 143-145 y 179 piden conservar lo recopilado e incorporar lo nuevo,
  y la línea 184 no inventar: un flujo nuevo pasa por la misma fusión, así que un nombre que no
  está escrito en el mensaje nunca entra. El log suma lo que cambió (línea 471).
- **Precisa D21:** el resto de la línea sigue sin valores; el *Pendiente* de D21 (tareas 19 y
  20) suma `changes=` y `rejected=`. El texto de D21 no se edita.
- **Resuelve:** el *Pendiente* de D37 ("agregar uno que ya está" contra la base del turno,
  con test, y la nota en un flujo nuevo) y la parte de `depends_on` del *Pendiente* de D11.1.
- **Lo prueban:** `test_r2_anchors_and_merges_a_new_flow`, `test_filter_keeps_depends_on`,
  `test_changes_are_shown`, `test_repeated_request_changes_nothing`,
  `test_add_present_is_measured_against_the_turn_base`, `test_show_value_by_kind`,
  `test_case_d_reply_names_every_component`, `test_same_tool_needs_a_collecting_record`,
  `test_case_b_through_the_node`, `test_node_passes_the_message_text`,
  `test_message_text_joins_text_parts` y `test_one_log_line_per_turn`.
- **Pendiente:** D11.1 sigue sin implementar (tarea 08b): dos operaciones de la tool pendiente
  siguen con la pregunta de D11 [resuelto en D54]. Redacción, sin tarea (revisión de la tarea 08, nota 7): un
  valor que termina en punto da "..", la nota usa la grafía que llegó y un `relative_days`
  negativo se muestra "-1 días desde hoy"; la 09a ya tiene el recorte de espacios. [recorte
  de espacios resuelto en D44]

**D43 · Precisa D17: la respuesta nombra los inválidos con sus valores; `last_question`, solo
etiquetas** (2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** la respuesta de un turno de recopilación lleva, en este orden, las líneas de
  cambio, nota y rechazo (D39), una línea por campo inválido y la pregunta. La línea de un
  inválido puede listar los valores permitidos ("No reconozco la prioridad que indicaste; puede
  ser baja, media o alta."). `last_question` guarda solo la pregunta (`missing_question`,
  `core/replies.py`): nombra por su `label` los campos faltantes **e inválidos** de cada paso,
  en el orden del registro, sin valores recopilados ni permitidos. Vale también para la 10a: la
  pregunta de confirmación que se guarde tampoco lleva valores, aunque la respuesta los muestre.
- **Motivo:** RF-06 pide identificar el problema y pedir la corrección (TASK.md líneas
  186-200); ante "urgente" el usuario necesita saber qué puede escribir. El intérprete no ve la
  respuesta (`include_contents='none'`, D2), solo `last_question` en el resumen de D14, así que
  las opciones siguen fuera de su vista y el modelo no convierte "urgente" en "alta" (líneas
  166-168 y 184).
- **Lo que ve el usuario:** los valores permitidos aparecen solo cuando dio uno que no está en
  la lista; la pregunta de faltantes sigue sin opciones (caso B, línea 319). Lo preveía la fila
  09a del backlog; no cambia cuándo se ejecuta nada ni qué se guarda.
- **Precisa D17:** reabre en parte su segunda descartada ("listarlas solo en la respuesta"):
  las opciones van en la línea del inválido, nunca en la pregunta, y desde D39 `last_question`
  ya no es todo lo que el usuario leyó. El texto de D17 no se edita.
- **Precisa D38:** su condición ("vale mientras `last_question` no lleve valores") queda fijada
  también para la confirmación de la 10a.
- **Descartada:** listar las opciones dentro de la pregunta y guardarlas en `last_question` (la
  primera descartada de D17).
- **Lo prueban:** `test_invalid_and_missing_in_one_reply` y
  `test_missing_question_has_no_collected_value_and_no_options` (que suma `X1`).

**D44 · Tarea 09a: el validador por tipo de campo conectado al turno** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** `core/validation.py` (`validate(tool, args) -> Validation`, Python puro, sin
  ADK) implementa D5 salvo fechas y horas, y `core/turn.py` lo llama por paso en R2 y tras cada
  fusión de *Misma tool*. Solo la última línea toca lo que ve el usuario, y solo en redacción,
  así que van en una línea cada una (regla del autor desde la tarea 07):
  - *Registro:* `pending` gana `invalid: [{tool, field, cause}]`, sin valores; `missing` sigue
    siendo solo lo ausente [precisado en D56: también una fecha u hora incompleta]; `status`
    sigue en `collecting`. Los normalizados y `unchecked` no se guardan: se recalculan (la 10a,
    para mostrar y para el hash de D7.2).
  - *Causas:* `not_allowed` (una `word` sin sinónimo, "urgente"), `out_of_range` (un `int` fuera
    de `int_range`) y `wrong_type` (cualquier tipo equivocado; un `bool` no es `int`, como D13).
  - *Reglas:* una por `FieldKind` en `RULES`; `text` y `person` recortan espacios, `word` da el
    valor canónico (`resolve_word`) y `person_list` recorta cada nombre. Cada campo pasa por
    ausente → sin regla → regla de su tipo, en el orden del registro.
  - *Parámetros:* `FieldSpec` gana `int_range` (`duration_minutes`: de 1 a 480, D5.3) y
    `value_labels` (baja/media/alta; correo/SMS/push), que reusarán la 10a y la 10b. Ninguna
    regla nombra una tool ni un campo (TASK.md línea 562). Precisa D5: las reglas viven por
    tipo en `core/validation.py`; el registro declara el tipo y sus parámetros.
  - *Fechas y horas:* presentes, quedan `unchecked`, nunca válidas, hasta la 09b; un paso
    completo con una de ellas deja `last_question` nulo y cierra con la plantilla provisional
    "Tengo los datos; todavía no puedo validar fechas ni horas en esta versión.". [resuelto en
    D56 y D58]
  - *Completo y válido:* `last_question` nulo y la plantilla de D19; nada se ejecuta (D7.1).
    [reemplazado en D47: `awaiting_confirmation`]
  - *Log:* todo turno entendido suma `invalid=campo:causa`, sin valores ni tool, antes de
    `changes=`, aun vacío. Sin la tool, dos pasos con un campo del mismo nombre no se
    distinguen; lo decide la 14a.
  - *Sin cambios:* un turno *Misma tool* que no cambia nada deja el estado igual (D42) y repite
    las líneas de inválido guardadas y la pregunta.
  - *Resumen del intérprete:* `pending_summary` sigue con sus cuatro claves (D14, D16) y no
    muestra `invalid`, así que su `Missing fields` lista solo lo ausente mientras
    `last_question` nombra también los inválidos. Es lo decidido, no una incoherencia: los
    registros de la fase 0 (D27) siguen dando el resumen con el que se midió D32.
  - *`missing_fields`:* se borra de `core/presence.py`; su test es ahora
    `test_missing_comes_from_the_validator`.
  - *Espacios (nota (hh)):* `show_value` recorta los de una cadena al mostrarla, como el
    validador; `args` guarda el valor tal como llegó.
  - *Redacción:* "No reconozco {etiqueta} que indicaste; puede ser {valores en español, con
    'o'}.", "{Etiqueta} debe ser un número entre {mín} y {máx}." y "No pude leer {etiqueta}.";
    por causa, nunca por tool, y sin el valor dado.
  - *Inválido y `fill`:* un campo inválido conserva su línea "Anoté …" de D39 y después va su
    línea de inválido ("Anoté la prioridad: urgente." y "No reconozco la prioridad que
    indicaste; puede ser baja, media o alta."). Es D39 al pie de la letra y se lee algo raro;
    cambia solo la redacción que ve el usuario, no cuándo se ejecuta nada ni qué se guarda.
- **Motivo:** RF-04 pide validar presencia, tipos, valores permitidos y restricciones de
  negocio de forma determinística (TASK.md líneas 151-168); RF-05, volver a validar tras cada
  mensaje y no ejecutar ni inventar (líneas 174-184); RF-06, nombrar el problema (líneas
  186-200), y RF-07, guardar el estado de validación (línea 218). Por D4 regla 7 la fusión
  guarda las palabras del usuario y el validador las reporta.
- **Precisa D19:** una reunión completa cierra con la plantilla de fechas, no con la de D19, y
  `awaiting_confirmation` lo crea la 10a, no la 09b. El texto de D19 no se edita.
- **Precisa D41:** la línea de D10 también precede a la plantilla de fechas; ningún test lo
  fija, y la 10a y la 12 lo deciden.
- **Lo prueban:** los nueve de `tests/test_validation.py`, `test_invalid_and_missing_in_one_reply`,
  `test_correction_clears_invalid`, `test_duration_out_of_range`,
  `test_repeated_invalid_request_changes_nothing`, `test_complete_meeting_waits_for_dates`,
  `test_show_value_trims_spaces`, `test_value_labels_and_int_ranges_are_declared_per_field`,
  `test_invalid_word_through_the_node` y `test_complete_meeting_does_not_reach_confirmation`.
- **Pendiente:** un "sí" con faltantes o inválidos responde la plantilla provisional sin tocar
  el estado, no la pregunta que pide D7.1; la 10a lo cambia y repite también las líneas de
  inválido (revisión de la tarea 09a, ronda 2, nota 1) [resuelto en D47]. La confirmación y el
  hash de D7.2 deben salir de `validate(...).normalized`, nunca de `args`, que conserva
  " Carlos " (nota 3) [resuelto en D46 y D47]. El
  caso D sigue sin cumplirse hasta la 09b ("40 de noviembre a las 25:90" se anota y no se
  reporta; TASK.md líneas 159, 163 y 365-371), que además quita el `validate` repetido de
  `_closing_line` [resuelto en D56 y D58]. `test_complete_meeting_does_not_reach_confirmation`
  solo puede fallar desde la 10a: hoy nada crea `awaiting_confirmation` (ronda 1, nota 3).
  [reemplazado en D58 por `test_meeting_through_the_node`]

**D45 · Un "sí" que trae otra tool, o del que el filtro descartó algo, no ejecuta** (2026-11-17)
· decidida por el autor

- **Decisión:** con un registro pendiente (`collecting` o `awaiting_confirmation`), un `confirm`
  que trae una operación de una tool que no está pendiente, con cualquier `certainty`, o del que
  el filtro del registro descartó alguna operación (D10), no ejecuta ni cambia nada [precisado
  en D57: nada de lo que trae el mensaje; el paso del tiempo sí puede cambiar la validez]:
  repite la confirmación o la pregunta pendiente, con la línea fija de D10 delante si el filtro
  descartó algo. Es el mismo trato que el veto de D7.4, y se evalúa antes que él y antes de la
  fusión.
  Respuesta del autor a la brief 10a, opción (a).
- **Motivo:** un "sí" nunca autoriza otra tool (TASK.md líneas 282-284), y un "sí" que mezcla
  otra solicitud ya no responde solo a lo que se mostró: el mismo motivo por el que un turno
  con cambios no ejecuta (D4, *Lo que D4 exige a D7*). Cuesta un turno, nunca una ejecución.
- **Lo que ve el usuario:** con una notificación en confirmación, "Sí, y agenda una reunión con
  Juan" repite la confirmación y "Sí, y pide una pizza" la repite tras la línea de D10. Nada
  corre y puede pedir lo otro aparte.
- **Precisa D3 y D7:** D3 (*Compuerta de entrada*, paso 3, *Resto*) manda `confirm` a D7 sin
  mirar otras tools, y D7 solo habla de la tool pendiente. Tampoco es *Otra tool* (D3.1), que
  no lista `confirm`. El texto de D3 y D7 no se edita.
- **Descartadas:** ejecutar lo pendiente e ignorar lo demás con una línea fija ("No inicié lo
  otro; pídemelo aparte."); tratarlo como *Otra tool* (D3.1: archiva lo pendiente e inicia lo
  nuevo, y pierde una operación que el usuario acaba de confirmar).
- **Lo prueba:** `test_yes_with_another_tool_never_runs` (una tool del catálogo, una fuera de
  él y un registro en `collecting`).

**D46 · Costura de ejecución: el núcleo llama a la tool desde un solo punto** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** `_run_confirmed` (`core/turn.py`) es el único sitio del código que llama a una
  tool: por cada paso, `TOOLS[tool].execute(normalized)` con los valores normalizados que cubre
  el hash de D7.2, y el mock los valida contra su contrato (D12). Devuelve
  `state_delta={"pending": None}`, `route="confirmed"` y, como respuesta, el `message` de cada
  resultado. El nodo del turno no llama a ninguna tool y sigue haciendo una sola llamada al
  núcleo. El registro de ejecuciones de D9 llega en la tarea 11 como parámetro `executions` de
  `handle_turn` y de `build_root_agent(interpreter, executions)`: un almacén sin reglas que el
  núcleo consulta antes y después de la llamada (reserva con `SET NX`, `done`, liberación ante
  `success=false`); el nodo solo lo pasa. Opción A de la brief 10a.
- **Lo que ve el usuario:** nada que D7.1 no decidiera ya: desde la 10a el mock corre tras un
  "sí" válido y `pending` queda vacío, así que un "sí" reenviado responde "No hay ninguna
  operación pendiente." (TASK.md línea 284) y no ejecuta otra vez.
- **Motivo:** D1.1 pide una sola llamada al núcleo por turno y un nodo sin lógica (*Frontera*).
  Si el nodo ejecutara, tendría que recorrer el plan, cortar en el primer fallo y pasar el
  `ticket_id` (D8.2, D8.3): lógica de negocio en el adaptador. Así D9.2 y D9.3 quedan en Python
  puro y se prueban sin Redis (TASK.md líneas 270, 286, 558 y 568-570).
- **Costo aceptado:** el núcleo es síncrono, así que la variante Redis de la 11 usará el cliente
  síncrono de redis-py dentro de un nodo async (milisegundos, en una CLI de un usuario); la
  brief 11 lo confirma. [resuelto en D62]
- **Descartadas:** que el núcleo devuelva una orden (`to_execute`) y el nodo llame al mock y
  vuelva a llamar al núcleo con el resultado; esconder la llamada tras una bandera hasta la 10b
  (una plantilla provisional más que la 10b tiraría); agregar `executions` ya en la 10a (nada
  lo usaría hasta la 11).
- **Lo prueban:** `test_yes_runs_the_pending_operation_once`, `test_case_g_through_the_node`,
  `test_confirmed_operation_is_gone_after_reload` y `test_no_turn_executes_a_tool`, sin cambios;
  `grep -rn "\.execute(" core assistant` da solo `_run_confirmed`.
- **Pendiente:** hasta la 11 no hay idempotencia: una solicitud idéntica repetida después de
  ejecutarse vuelve a pedir confirmación y, con otro "sí", ejecuta de nuevo (D9.1; TASK.md
  líneas 443-449) [resuelto en D62 y D63]. `_run_confirmed` no corta en un fallo, vacía `pending` sin mirar `success` y
  no captura una excepción de la tool; las tareas 11 y 14b lo cambian antes de un plan de dos
  pasos (TASK.md línea 270; revisión de la tarea 10a, ronda 2, nota 5) [resuelto en D60 y D63]. Sin `history` (D6) ni
  respuesta propia del resultado (línea 246): tarea 10b. [resuelto en D48 y D49]

**D47 · Tarea 10a: confirmación y "sí" en el turno** (2026-11-17) · autodecidida por el
orquestador; revisable

- **Decisión:** `core/turn.py` crea `awaiting_confirmation` y la rama `confirm`;
  `core/confirmation.py` (`shown_values`, `confirmation_hash`) calcula lo que se muestra y se
  ata; `core/replies.py` (`confirmation_summary`, `confirmation_question`) lo escribe. Ninguno
  de estos detalles cambia cuándo se ejecuta una tool más allá de D7, D45 y D46, así que van en
  una línea cada uno (regla del autor desde la tarea 07):
  - *Hash distinto: se revalida (precisa D7.2):* los pasos se evalúan otra vez, como tras un
    cambio: siguen completos y válidos → la confirmación con los valores actuales y el hash
    nuevo; uno dejó de serlo → `collecting` sin `confirmation_hash`, con sus líneas de inválido
    y su pregunta, y nada corre. Sin revalidar, la confirmación omitía el campo inválido y el
    "sí" siguiente llegaba al contrato del mock incompleto (revisión de la tarea 10a, ronda 1,
    should-fix 1; TASK.md líneas 239-241). La brief 10a pedía solo volver a mostrar.
  - *Orden de un `confirm` con algo pendiente:* D45; veto de D7.4; fusión (un cambio, un nombre
    rechazado o una nota nunca ejecutan, D4 y D38); registro en `collecting` → las líneas de
    inválido y la pregunta, sin tocar el estado (D7.1); hash; `_run_confirmed` (D46).
  - *`operation_id`:* `uuid4().hex`, nace en R2 en todo registro y se conserva en cada fusión;
    lo lee el log como `op=`, después de `session=` y también en un turno con fallo del modelo,
    y desde la 10b `history`. No se compara al llegar el "sí": lo ata el registro único (D1) y
    lo protege el hash.
  - *`confirmation_hash`:* sha256 del JSON canónico (`sort_keys`, `ensure_ascii=False`) de
    `[{tool, normalized}]` por paso; se guarda en el registro y `pending_summary` no lo muestra
    (D14). Los normalizados se recalculan, nunca se guardan (D44).
  - *Vuelta a `collecting`:* una corrección que deja un paso incompleto o inválido borra
    `confirmation_hash` (D7.1).
  - *Misma tool en confirmación:* la rama acepta también `awaiting_confirmation`; una corrección
    vuelve a mostrar la confirmación con hash nuevo y un mensaje sin cambios la repite con el
    estado intacto (D9 *Caso H*, D40).
  - *Valores dos veces:* una solicitud que llega completa muestra los `fill` de D39 (palabras
    del usuario) y después el resumen normalizado, así que se ve la conversión "Alta" → "alta".
  - *Resumen:* "Voy a {acción} con estos datos:" y una línea "- {Etiqueta}: {valor}" por campo,
    en el orden del registro; una `word` por su `value_labels` ("correo", nunca `email`).
  - *Pregunta guardada:* "¿Confirmas que quieres {acciones}? Responde sí o no.", sin valores
    recopilados ni permitidos (D43, D38).
  - *Hash distinto y válido:* la confirmación vuelve a mostrarse sin una línea que diga por qué.
    [reemplazado en D55: una línea fija lo dice]
  - *Un "sí" que repite un valor guardado* ("Sí, por correo"; "Sí, envíalo a Carlos" con el
    nombre escrito) no es un cambio (D4 regla 5) y cuenta como "sí".
  - *Tras ejecutar:* la respuesta es el `message` del mock, con los valores canónicos ("high",
    "email"), hasta la 10b. [resuelto en D48]
  - *Fase 0:* `test_the_23_scenarios_of_d2` compara los registros sin `operation_id`, que es
    aleatorio; el resumen no lo muestra, así que los prompts con que se midió D32 no cambian.
  - *Plantillas:* se borra `READY_NOT_AVAILABLE_YET` (D19); un registro `collecting` sin
    pregunta solo existe con una fecha u hora sin regla (`DATE_TIME_NOT_CHECKED_YET`, hasta la
    09b). [borrada en D58]
- **Motivo:** RF-08 y RF-10 piden ejecutar solo con argumentos completos, validados y
  autorizados, y atar el "sí" a la operación pendiente identificada (TASK.md líneas 233-243 y
  276-286); el caso G confirma antes de ejecutar sin volver a pedir lo dado (líneas 420-438), y
  el B ejecuta solo con todo completo (línea 335).
- **Precisa D19 y D44:** la plantilla de D19 desaparece: una solicitud completa y válida pasa a
  `awaiting_confirmation`. Su texto no se edita.
- **Precisa D41:** con algo pendiente, la línea de D10 precede también a la confirmación
  mostrada o repetida. Las plantillas provisionales que quedan (`reject`, `cancel`, `question`,
  `other`, *Otra tool*) y la redacción "Parte de tu solicitud" siguen en la 12. [resuelto en
  D71]
- **Lo prueban:** `test_complete_request_awaits_confirmation`,
  `test_case_b_reaches_confirmation`, `test_yes_runs_the_pending_operation_once`,
  `test_yes_while_collecting_repeats_the_question`, `test_yes_with_a_change_never_runs`,
  `test_yes_with_a_resent_name_is_rejected` (D38), `test_confirms_tool_veto` (D7.4),
  `test_hash_mismatch_asks_again`,
  `test_hash_mismatch_with_an_invalid_value_goes_back_to_collecting`,
  `test_repeated_request_while_awaiting`, `test_correction_while_awaiting`,
  `test_unknown_tool_while_awaiting_adds_the_d10_line`, `test_yes_with_a_note_never_counts`,
  `test_yes_repeating_a_stored_value_counts`, `test_case_g_through_the_node` (con `op=` en el
  log) y `test_case_b_through_the_node` (termina con un "sí" que ejecuta `create_ticket`).
- **Pendiente:** "una nota nunca ejecuta" se prueba sobre una reunión en `collecting`; el caso
  que importa (una reunión en confirmación y "sí, y quita a Ana") existe desde la 09b y va como
  criterio de su brief (revisión de la tarea 10a, ronda 2, nota 2) [resuelto en D58]. La 09b
  decide si un hash distinto con valores válidos ("mañana" que cambió de día) lleva una línea
  que lo explique (nota 4) [resuelto en D55]. En el "sí" que ejecuta, cada paso se valida tres
  veces (nota 3). `core/turn.py` tiene 406 líneas (umbral de la brief: ~350), y `record_tools` y
  `without_id` están repetidos en los tests (nota 6). `reject` y `cancel` (D7.3) son la 12.
  [resuelto en D66, D69 y D71]

**D48 · Respuesta tras ejecutar: una línea de plantilla por paso, con el ID del resultado**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** tras un "sí" que ejecuta, la respuesta es una línea por paso que escribe
  `result_line` (`core/replies.py`): "Listo: {done_phrase} {ID}.". El ID se copia tal como lo
  devolvió la tool, de `result[tool.result_id_field]`, sin suponer su formato. `ToolSpec` gana
  `done_phrase` ("creé el ticket", "programé la reunión", "envié la notificación"), como
  `action_phrase` y `short_name` (D3). El `message` del mock ya no se muestra: queda en
  `history`, dentro de `result` (D49). Opción (a) de la brief 10b.
- **Lo que ve el usuario:** en el caso G, "Listo: envié la notificación NTF-…" en lugar de
  "Notificación NTF-… enviada a Carlos por email." (D47, *Tras ejecutar*). Ya no aparecen los
  valores canónicos ("high", "email"), que el usuario vio en español en la confirmación del turno
  anterior. No cambia cuándo se ejecuta nada.
- **Motivo:** el agente debe interpretar los resultados y dar feedback (TASK.md líneas 24 y 246;
  §6, líneas 497-499). Es una plantilla del núcleo (D2), sin texto de la tool, y la frase y el
  campo de ID salen del registro, no de un nombre de tool en el código (línea 562). La 14b reusa
  la línea por paso para informar el resultado de ambas operaciones (línea 392).
- **Descartadas:** mostrar el `message` del mock con etiquetas en español (el mock tendría que
  conocer "alta" y "correo": duplicaría `value_labels` o importaría el registro, que ya importa
  los mocks); la línea más el `message` del mock (repite lo que se acaba de confirmar). Repliegue
  si se prefiere no sumar una etiqueta: "Listo: {short_name} {ID}.".
- **Lo prueban:** `test_result_line_per_tool`, `test_result_line_reads_the_registry_id_field`
  (una tool falsa con `result_id_field` propio), `test_yes_reply_reads_the_result`,
  `test_case_g_through_the_node` y `test_case_b_through_the_node`.
- **Pendiente:** "Listo" no mira `success` (D50, *Pendiente*). [resuelto en D60]

**D49 · Forma de `history` y cómo llega al núcleo** (2026-11-17) · autodecidida por el
orquestador; revisable

- **Decisión:** al ejecutarse, `_run_confirmed` vacía `pending` y agrega a
  `session.state["history"]` la entrada `{operation_id, status: "completed", steps: [{tool,
  values, result}]}`, en el orden de los pasos. `values` son los valores normalizados que recibió
  la tool (" Carlos " → "Carlos", "correo" → "email") y `result` es el dict tal como lo devolvió,
  `message` incluido. La más nueva va al final y se guardan las últimas `MAX_HISTORY = 10`.
  `core/history.py` (Python puro) arma la entrada (`closed_entry`) y la lista nueva (`appended`,
  sin mutar la recibida); los cierres `failed`, `uncertain`, `cancelled` y `abandoned` de las
  tareas 11 y 12 usarán los mismos ayudantes. El núcleo la recibe como
  `handle_turn(..., history=())`: el nodo la lee con `ctx.state.get("history") or []` y la lista
  completa vuelve en el `state_delta` del evento final, como `pending` (D1.1, *Estado*).
- **Lo que se guarda:** `history` aparece en la sesión de Redis desde el primer "sí" que ejecuta.
  Ninguna regla la lee (D6): ni la fusión (D4), ni la compuerta (D3), ni `pending_summary`
  (D14), así que el modelo nunca la ve. El usuario no ve nada de ella.
- **Motivo:** RF-07 pide mantener el estado de ejecución y los resultados de las tools, y
  diferenciar el historial conversacional del estado estructurado (TASK.md líneas 220-222 y
  229): `history` es estado estructurado, no conversación. Un parámetro con nombre deja intactos
  los tests anteriores y deja la lógica en el núcleo, no en el adaptador (D1.1, *Frontera*).
- **Precisa D44:** en `pending` los normalizados no se guardan porque pueden cambiar (una fecha
  relativa); en `history` sí, porque son lo que se ejecutó. El texto de D44 no se edita.
- **Precisa D42:** `TurnInput` lleva lo que no es estado (`message`, y luego `today` y
  `session_id`) [`today` es `now`; ver D58]; `pending` y `history`, que sí lo son, llegan como
  parámetros propios de `handle_turn`. El texto de D42 no se edita.
- **Descartadas:** meter `history` en `TurnInput` (D42 lo reservó para lo que no es estado); que
  el nodo agregue la entrada (lógica en el adaptador).
- **Lo prueban:** `test_confirmed_operation_goes_to_history` (también con " Carlos "),
  `test_history_keeps_the_last_ten`, `test_only_a_confirmed_yes_writes_history`,
  `test_history_accumulates_and_is_never_read` (el nodo pasa lo guardado y el modelo no lo ve),
  `test_case_b_through_the_node`, `test_case_g_through_the_node` y
  `test_confirmed_operation_is_gone_after_reload` (una instancia nueva de `RedisSessionService`
  recarga `history`; línea 227).
- **Pendiente:** el `completed` no mira `success` (D50, *Pendiente*). [resuelto en D59 y D60]

**D50 · Tarea 10b: resultado, `history` y log** (2026-11-17) · autodecidida por el orquestador;
revisable

- **Decisión:** la 10b cierra la ejecución confirmada con D48 y D49. Los demás detalles no
  cambian lo que ve el usuario, cuándo se ejecuta una tool ni qué se guarda, así que van en una
  línea cada uno (regla del autor desde la tarea 07):
  - *Log (precisa D21 y D42):* un turno que ejecuta suma `results=tool:ID` (uno por paso,
    separados por coma) después de `route=`; solo aparece si corrió una tool. El ID lo da la
    tool, no el usuario, y se escribe como `str(result[id_field])`. La 11 le suma el estado y la
    clave. [resuelto en D63]
  - *`TurnOutcome`:* gana `results: list[tuple[str, str]]`, `(tool, ID)` por paso, para el log.
  - *Fijo `B4`:* "Sí, créalo" como `confirm` sin operaciones y con `confirms_tool="create_ticket"`,
    igual que `G2` volvió del modelo real (D32); así el caso B termina con su propio "sí" por el
    nodo y prueba que D7.4 deja pasar la tool correcta. Solo se añade esa entrada a
    `tests/interpretations.py`.
  - *`values`:* es el mismo dict que recibió la tool; los mocks no lo mutan.
  - *`result`:* se guarda tal cual; tiene que poder serializarse en JSON (la 11 puede vigilarlo).
    [resuelto en D60]
  - *Tools falsas de los tests:* sus `done_phrase` son "hice algo", "pedí la revisión" y
    "reservé la sala".
- **Motivo:** la línea de log es la vía de observabilidad que TASK.md línea 471 pide justificar,
  y le faltaba qué se ejecutó; el caso B ejecuta solo con todo completo y tras su "sí" (línea
  335; D7.1, F4).
- **Lo prueban:** `test_case_g_through_the_node` (`route=confirmed`, `op=` y
  `results=send_notification:<ID>` en la línea de log), `test_case_b_through_the_node` (`B4`;
  `create_ticket` corre una vez), `test_fixed_interpretations_validate` y
  `test_no_turn_executes_a_tool` (ambos recorren `B4` por `FIXED`).
- **Pendiente:**
  - *`success` (should-fix 1 de la revisión de la tarea 10b, ronda 1):* `_run_confirmed` no lee
    `success`. Un resultado con `success: false` respondería igual "Listo" (con un ID nulo,
    "Listo: … None.") y se guardaría como `completed`; un resultado sin el campo de ID lanza
    `KeyError` y no escribe ni `pending` ni `history`. Con los mocks no se alcanza (siempre
    devuelven `success: True`). Lo resuelve la 11: el fallo de ejecución, el plan que se
    detiene y el registro de ejecuciones (D8.3, D9, D9.3; TASK.md líneas 24-25, 246 y 270). La
    frase de defensa de la brief 10b ("el código lee `success`") no es cierta en la 10b: hoy lee
    solo el ID que declara el registro. [resuelto en D60]
  - *Excepción de una tool:* si una tool lanza, ni `pending` ni `history` se escriben y, con dos
    pasos, el primero ya habría corrido (líneas 270 y 286). Hoy no se alcanza (un solo paso: D11,
    R3); las tareas 11 y 14b escriben el cierre parcial con `closed_entry` antes de permitir un
    plan de dos pasos (revisión de la tarea 10b, ronda 2, nota 5). [resuelto en D60 y D63]
  - *ID del log:* `results` lee el ID por segunda vez en `_run_confirmed` y ningún test con una
    tool falsa lo fija (ronda 2, nota 1); puede ir con la 11, que toca ese bucle. [resuelto en
    D63]
  - *Tamaño:* `core/turn.py` tiene 425 líneas (ronda 2, nota 2).
  - Sin idempotencia tras ejecutar (D46, *Pendiente*; tarea 11). [resuelto en D62 y D63]

**D51 · Tarea 10c: dos rondas como mucho, 10 llamadas y 30 peticiones** (2026-11-17) ·
decidida por el autor

- **Decisión:** la 10c mide con 5 llamadas reales por ronda, una por id: `C1`, `N1`, `S3`, `A1`
  y `E1`, las que proponía D34. Opción (b) del hallazgo 4 de la revisión del backlog
  (`backlog-06.md`), ampliada en la segunda parada de la 10c: si en la ronda 1 falla alguno de
  `C1`, `N1`, `A1` o `E1`, sin fallo de forma ni corrida detenida, hay una ronda 2 con los
  mismos 5 ids y los mismos tres textos, dentro de la hora. Si la ronda 2 también falla, se
  revierten las descripciones. Si solo falla `S3`, no hay ronda 2; no hay ronda 3.
  - *Techo:* 10 llamadas en la tarea; una llamada cuenta aunque falle. El arnés no cambia (D28,
    D30): cada llamada puede ser hasta 3 peticiones HTTP, con techo de 15 por ronda y 30 en
    total. La nota de cada ronda cuenta llamadas y peticiones (llamadas más líneas de reintento
    en stderr, D31).
  - *Corrida detenida:* sin reintento ni `--resume`; otra llamada necesita otra aprobación.
  - *Nota (y), confirmada:* ningún paso con `gemini-3.8-flash` (D33, *Precisa D2*).
- **Motivo:** las descripciones las comparten los 23 escenarios, así que `A1` y `E1` vigilan
  el caso A y el caso E (TASK.md líneas 296 y 378) junto al caso C y su contraste (líneas
  338-355 y 117). La ronda 2 da al ajuste un intento más sin pasar de la hora ni gastar más
  cuota de la capa gratuita: el foco es la orquestación, no el modelo (línea 597).
- **Precisa D33:** D33 revertía tras un solo intento; con (b), tras la ronda 2. La primera
  pregunta decía hasta 2 peticiones por llamada, por D1.2, que es el reintento del intérprete
  del agente y no el del arnés; el autor fijó 3. El texto de D33 no se edita.
- **Descartadas:** (a) un intento y, si falla, limitación (D33 tal como está); (c) 2 llamadas
  (`C1`, `N1`) con `gemini-3.8-flash` antes de declarar la limitación.

**D52 · Resultado de la 10c: el caso C queda como limitación con `gemini-3.5-flash-lite`**
(2026-11-17)

- **Decisión:** por D33 y D51 se revierten los tres textos: `core/interpretation.py` queda
  igual que en `26b054c`, con los de la 06. El caso C (TASK.md líneas 338-355) queda como
  limitación documentada con `gemini-3.5-flash-lite`. Dos rondas, cada una con su nota del
  mismo nombre en `phase0/runs/`:
  - `20261117T164006Z-gemini-3.5-flash-lite.jsonl` (ronda 1): 5 llamadas, 6 peticiones HTTP
    (un 503 que absorbió el reintento del arnés) y 0 fallos de forma. `C1` en R2 → `ronda_2`.
    Las 5 salidas son iguales, campo por campo, a las de la 06.
  - `20261117T164255Z-gemini-3.5-flash-lite.jsonl` (ronda 2): 5 llamadas, 5 peticiones y 0
    fallos de forma. `C1` otra vez en R2 → `revertir`.

  En total, 10 de 10 llamadas y 11 de 30 peticiones. Ninguna corrida se detuvo y el gate de
  F0.1 no saltó (D34). El juicio es del juez de D53; `test_10c_runs_as_recorded` lo reproduce
  sin modelo. Los textos probados están literales en cada nota.
- **Lo que ve el usuario:** con el mensaje del caso C, el modelo devuelve una sola
  `send_notification` `explicit`, sin `schedule_meeting` ni `ambiguity_reason`. El agente
  inicia la notificación y pide el canal en vez de aclarar; la reunión nunca se ofrece (D3,
  *Limitación para el README (omisión)*). No ejecuta varias tools por asociación (línea 353) y
  nada corre sin la confirmación de D7.1, pero no pide la aclaración de la línea 355.
- **Sin cambio en las dos rondas:** `N1` en R2, solo con `send_notification` y sin canal
  inventado (línea 117); `A1` en R2 sin `title` inventado (líneas 184 y 304); `E1` en R3 con
  `create_ticket` y `send_notification` `explicit` y con `depends_on` (línea 378).
- **`S3`:** con los textos vigentes cae en la regla 2 de la *Lectura de la respuesta* de D3 y
  resolvería la aclaración a la notificación (06 y ronda 1), que D2 (*Sondas*) llama fallo
  crítico; nada se ejecuta (D7.1). Solo pasó en la ronda 2 (regla 5: `send_notification`
  `explicit` y `schedule_meeting` `plausible`), con textos ya revertidos. La 13 parte de la
  regla 2.
- **Con qué textos se midió cada cosa (D34):** el 0/23 de D32 y sus lecturas, con los de la
  06, que son los vigentes; `C1`, `N1`, `S3`, `A1` y `E1` de la 10c, con los de su ronda.
- **Motivo:** D33 manda revertir si falla `C1`, y D51 lo aplica tras la ronda 2. Solo
  descripciones cortas y genéricas podían intentarlo, sin un prompt que decida ni reglas por
  palabra clave (TASK.md líneas 109 y 587). Ni una pista cercana a la forma del caso C
  ("something it says will happen") hizo que el modelo ofreciera la reunión sin nada
  pendiente (revisión de la tarea 10c, ronda 1, nota 3). Should-fix 1 de esa revisión: la
  limitación queda explícita.
- **Para la defensa** (preguntas 3 y 8 de §9, TASK.md líneas 555 y 560): el fallo es de
  lectura del modelo, no de la tabla. Con las dos lecturas reportadas, el núcleo cae en R4 o
  R5 y pregunta (`test_fixed_outputs_pass`, con el fijo de `C1`).
- **Pendiente:** la 17 lo dice en la transcripción de C y la 20 en el README (D3, *Limitación
  (omisión)*); la 19 lo usa en las preguntas 3 y 8 y dice con qué textos se midió cada cosa.
  El techo de la 10c está agotado: otra llamada real necesita una aprobación nueva del autor.

**D53 · Tarea 10c: `--only` en el arnés y el juez del caso C** (2026-11-17) · autodecidida por
el orquestador; revisable

- **Decisión:** la 10c cierra con D51 y D52. Lo demás no cambia lo que ve el usuario, cuándo se
  ejecuta una tool ni qué se guarda, así que va en una línea cada uno (regla del autor desde la
  tarea 07):
  - *`--only ID,…`* (`select_scenarios`, `phase0/run.py`): un filtro sin lógica nueva de
    veredicto (decisión del autor, hallazgo 10 de `backlog-06.md`). Conserva el orden de
    `SCENARIOS` y el registro pendiente de cada escenario; sin `--run` lista solo esos ids.
  - *Errores de `--only`:* un id desconocido o repetido, una lista vacía o `--only` con
    `--resume` → código 2, antes de leer `.env`, de llamar o de crear un archivo.
  - *Informe:* da los fallos de forma sobre los ids pedidos y "Sin veredicto de F0.1" (la rama
    que ya tenía `format_report`; nota (z)).
  - *Juez* (`tests/case_c_judge.py`; `uv run python -m tests.case_c_judge RUTA [--round 1|2]`):
    pasa cada salida guardada de `C1`, `N1`, `A1` y `E1` por `handle_turn` y
    `filter_by_registry` reales (D3, *Método*) y da el resultado de la ronda (`pasa`,
    `solo_s3`, `ronda_2` o `revertir`). Vive en `tests/` porque el arnés no da veredictos y
    `core/` no cambia en la 10c.
  - *`S3`:* el juez lleva una copia de la *Lectura de la respuesta* de D3 (reglas 1–5) hasta
    que la 13 la implemente en el núcleo; después, el juez llama al núcleo.
  - *Límites de los textos:* `test_certainty_texts_stay_short_and_generic` exige ≤ 250
    caracteres, ASCII, sin nombres de tools ni palabras de los mensajes medidos, sin comillas
    ni "then", y los mismos campos del esquema que midió F0.1. Hoy vigila los de la 06.
- **Precisa D33:** "`A1` y `E1` dan lo mismo que su interpretación fija" se juzga por lo que
  haría el código, no por el JSON exacto (D2): `A1` y `N1` en R2 con su tool y el mismo
  `missing` que su fijo; `E1` en R3 con las dos tools `explicit`. `depends_on` se informa y no
  decide (D8.4). El texto de D33 no se edita.
- **Motivo:** el resultado lo decide el código del núcleo sobre salidas guardadas, no una
  lectura a mano, y se puede repetir sin modelo (D3, *Método*). El test de los textos impide
  que el ajuste se vuelva un prompt que decide o una regla por palabra clave (TASK.md líneas
  109 y 587).
- **Lo prueban:** `test_only_calls_just_the_ids_asked`,
  `test_only_rejects_unknown_or_repeated_ids`, `test_only_and_resume_do_not_mix`,
  `test_only_listing_calls_nothing`, `test_fixed_outputs_pass`,
  `test_06_run_reproduces_the_hand_reading`, `test_judge_edges`, `test_round_outcomes`,
  `test_10c_runs_as_recorded` y `test_certainty_texts_stay_short_and_generic`.
- **Pendiente:**
  - *Ids ausentes:* el juez no juzga los ids que faltan en el archivo, así que una corrida
    detenida puede dar `pasa` o `solo_s3`; el orden "corrida detenida → fallo de forma → juez"
    lo sostiene el procedimiento. Si la 13 lo reutiliza, la nota dice cuántos ids se juzgaron
    (revisión de la tarea 10c, ronda 1, nota 4).
  - *Copia de D3:* cuatro actos sueltos (`cancel` en la regla 1, `reject` y `provide_info` en
    la 4, `provide_info` en la 2) no tienen test propio; la 13 pone un caso por acto al
    reemplazar la copia (ronda 3, nota 1).

**D54 · Tarea 08b: la fusión de D11.1 en el turno** (2026-11-17) · autodecidida por el
orquestador; revisable

- **Decisión:** `core/turn.py` aplica D11.1 y D35 tal como están escritas: en `_decide`, antes de
  la pregunta de D11 y solo si el turno es *Misma tool*, `_join_split_operations` une las
  operaciones de una tool pendiente cuyos `args` no comparten ninguna clave y traen el mismo
  `depends_on`, y la operación unida sigue por la fusión, la validación y la respuesta de una sola
  operación (D4, D39, D42, D47). Los detalles no cambian lo que D11.1 decide, así que van en una
  línea cada uno (regla del autor desde la tarea 07):
  - *Predicado único:* la condición de *Misma tool* (registro en `collecting` o
    `awaiting_confirmation`, `act` `request_action` o `provide_info`, toda operación de una tool
    pendiente) se escribe una vez, en `_is_same_tool`, y la usan la unión y la rama.
  - *Claves (pendiente (a) de D11.1):* se cuentan tras `filter_by_registry` y tras quitar los
    ausentes (`drop_absent`, D4 regla 2); los `args` unidos son los de cada operación sin
    ausentes, así un `""` de una operación nunca pisa el valor de otra. Cada cubeta `_add` y
    `_remove` es una clave propia.
  - *`depends_on` (pendiente (b)):* nulo frente a uno con valor cuenta como distinto (D35).
  - *Candidatas (pendiente (c)):* `awaiting_clarification` no entra en `_is_same_tool`, así que
    una candidata de una aclaración no es tool pendiente; la 13 lo conserva.
  - *Tres o más operaciones:* se une el grupo entero de una tool solo si ninguna pareja repite
    clave y todas traen el mismo `depends_on`; si no, el grupo queda como llegó y pregunta D11.
    Nunca se une una parte.
  - *`certainty`:* la operación unida toma la de la primera; *Misma tool* no la lee (D2, D35).
  - *Orden:* los `args` unidos conservan el orden de llegada; solo hace determinístico el
    resultado, porque las claves son disjuntas.
  - *Log:* sin clave nueva. `ops=` lista las operaciones como las devolvió el intérprete, así que
    dos de la misma tool junto a `route=same_tool` muestran la unión (D21, D42).
  - *Dónde vive:* en `core/turn.py`, junto a `_decide`; no en `core/merge.py`, que trabaja sobre
    los `args` de un paso y no sabe de operaciones ni de lo pendiente (D4 regla 1).
  - *Evidencia:* `test_measured_l4_is_joined` lee la salida cruda de `L4` del JSONL versionado
    `phase0/runs/20261117T073844Z-gemini-3.5-flash-lite.jsonl` (D32), con `"message": null`.
- **Lo que ve el usuario:** con la reunión con Juan pendiente, "Quita a Juan y agrega a Ana"
  partido en dos operaciones recibe la misma respuesta y el mismo estado que una sola operación
  (la de `L4`), en vez de "¿por cuál empiezo?". Con `awaiting_confirmation`, una corrección
  partida vuelve a mostrar la confirmación con hash nuevo. Dos operaciones de la tool pendiente
  que quedan sin ninguna clave tras quitar ausentes se unen en una operación vacía: el turno
  repite la pregunta pendiente sin tocar el estado, como D40, donde antes respondía D11
  (revisión de la tarea 08b, ronda 1, nota 3). Con una tool fuera del catálogo, la línea de D10
  se antepone como hoy (D41). Nada se ejecuta: un `confirm` con operaciones partidas sigue en
  D11 (TASK.md líneas 182 y 282-284).
- **Alcance real:** "dos tickets pedidos no se vuelven uno" vale solo fuera de *Misma tool*. Con
  un ticket pendiente, "Crea un ticket titulado Servidor caído y otro de prioridad alta" como dos
  `create_ticket` de claves disjuntas entra por *Misma tool* y las dos se unen en el ticket
  pendiente. Es D11.1 al pie de la letra y la limitación que D4 ya lista ("Crea otro ticket" con
  un ticket pendiente actualiza el pendiente); nada corre sin la confirmación que muestra los
  valores (TASK.md líneas 128-134, 282 y 413). Revisión de la tarea 08b, ronda 1, nota 2.
- **Motivo:** TASK.md líneas 143-145 y 179-180 piden actualizar lo recopilado con lo nuevo y
  volver a validar; la línea 413, no mezclar argumentos de dos operaciones, y la unión solo
  ocurre dentro de una tool ya pendiente. La regla lee claves y `depends_on`, nunca nombres de
  tool (línea 562).
- **Resuelve:** los tres pendientes de D11.1 y el *Pendiente* de D42 ("D11.1 sigue sin
  implementar").
- **Lo prueban:** `test_measured_l4_is_joined` (`request_action` y `provide_info`; la segunda
  operación `explicit` y `plausible`), `test_split_ops_ignore_absent_keys`,
  `test_split_ops_while_awaiting_confirmation`, `test_overlapping_split_ops_follow_d11` (claves
  repetidas, `depends_on` nulo frente a uno con valor y dos valores distintos),
  `test_split_ops_outside_same_tool_follow_d11` (turno mixto, nada pendiente, otra tool pendiente
  y `confirm`) y `test_two_operations_of_one_tool_follow_d11`, sin cambios.
- **Pendiente:**
  - *README (tarea 19):* la limitación del *Alcance real*, y la frase de defensa acotada a "fuera
    de la rama *Misma tool*"; el docstring de `core/turn.py` (paso 2) dice "two requested
    tickets never become one" sin esa salvedad.
  - *Tres operaciones:* ningún test trae tres operaciones de la misma tool; una regresión que
    uniera solo el subgrupo disjunto no la vería nadie (nota 1). La operación vacía de *Lo que ve
    el usuario* tampoco tiene test (nota 3).
  - *Tamaño:* `core/turn.py` tiene 471 líneas (D50, *Pendiente*).

**D55 · Hash distinto con el registro todavía válido: una línea fija lo dice** (2026-11-17) ·
decidida por el autor

- **Decisión:** si al llegar un "sí" el hash de los valores que se mostrarían ahora no coincide
  con el guardado (D7.2) y el registro sigue completo y válido, la respuesta antepone a la
  confirmación repetida la línea fija "Los datos cambiaron desde que te los mostré; revísalos
  antes de confirmar." (`DATA_CHANGED_LINE`, `core/replies.py`). Solo en ese camino
  (`route=confirm_hash_changed` con el registro en `awaiting_confirmation`); si el registro
  vuelve a `collecting`, la línea de inválido ya lo explica y no se añade. Nada corre: el "sí"
  siguiente ejecuta los valores nuevos. Respuesta del autor a la brief 09b, opción (a) (nota
  (ddd)).
- **Motivo:** desde la 09b el caso es real: una reunión "mañana" confirmada antes de medianoche
  y un "sí" después, o "el 17 de noviembre" sin año, que tras medianoche pasa al año siguiente
  (D5.1). El usuario sabe por qué se repite y mira la fecha antes del segundo "sí", que se ata a
  lo mostrado (TASK.md líneas 276-284).
- **Lo que ve el usuario:** una línea más antes de la confirmación repetida, solo en ese caso. No
  cambia cuándo se ejecuta nada ni qué se guarda.
- **Precisa D47:** su línea *Hash distinto y válido* ("sin una línea que diga por qué") queda
  reemplazada. El texto de D47 no se edita.
- **Descartada:** (b) sin línea, como D47, y listarlo como límite en la tarea 19.
- **Lo prueban:** `test_tomorrow_changes_the_hash_on_the_next_day` (la línea y su texto literal;
  el segundo "sí" corre una vez con `2026-11-19`), `test_hash_mismatch_asks_again` (cambiado:
  ahora espera la línea) y `test_time_that_passed_while_pending` (vuelve a `collecting` sin la
  línea).
- **Pendiente:** *rutas que repiten (limitación para las tareas 19 y 20):* al día siguiente de
  un "mañana", repetir el mensaje o un "sí" que D45 o D7.4 rechazan muestra la confirmación con
  la fecha nueva y guarda el hash nuevo (D57), sin esta línea; el "sí" siguiente ejecuta la
  fecha que se mostró (D7.2). Revisión de la tarea 09b, ronda 2, nota 6, y ronda 3, nota 2.

**D56 · Reglas de fecha y hora: lecturas de D5** (2026-11-17) · autodecidida por el
orquestador; revisable

- **Decisión:** `core/validation.py` gana las reglas `date` (`_date`) y `time` (`_time`) de
  D5.1, D5.1b, D5.1c y D5.2, una por `FieldKind` como en D44: convierten los componentes que
  copió el modelo a `YYYY-MM-DD` y `HH:mm` contra el `now` del turno (D58) y dan una causa por
  problema. Lo que D5 no fija va en una línea cada uno (decisiones B, C y D de la brief 09b, sus
  "lecturas de D5" y lo que fijaron las revisiones):
  - *Orden de la fecha:* ilegible → `wrong_type`; día fuera de 1–31 o mes fuera de 1–12 →
    `nonexistent`, aun sin la otra parte (`{day: 40}` solo); calendario incompleto → faltante;
    cada forma que fija un día da el suyo; formas que no coinciden → `mismatch`; antes de hoy →
    `past`.
  - *Incompleta es faltante (precisa D44):* día sin mes, mes sin día o año sin los dos van a
    `missing`, no a `invalid`, igual que una hora sin `hour` (D5.1 y D5.2: "el campo falta");
    `missing` ya no es solo lo ausente. Ni el mes ni la hora se completan (TASK.md línea 184).
  - *Día sin mes junto a un día de la semana (B):* "el viernes 27" deja la fecha incompleta y se
    pregunta; "el viernes 27 de noviembre" sí se comprueba. Descartadas: buscar la próxima fecha
    con ese número y ese día; comparar con el próximo viernes.
  - *Próxima ocurrencia:* día y mes sin año → este año si (mes, día) ≥ hoy y, si no, el
    siguiente; hoy cuenta (hoy no es pasado, D5.1b). Un día de la semana solo → de 1 a 7 días
    adelante, sin contar hoy (D5.1).
  - *Día de la semana con otra forma (D5.1c):* se comprueba contra el día que fija la otra
    forma, no se resuelve aparte ("mañana viernes" un martes → `mismatch`).
  - *Día que el año no tiene:* el 31 de abril o el 29 de febrero de un año no bisiesto →
    `nonexistent`.
  - *Enteros fuera del calendario:* un `relative_days` o un `year` que `date` no alcanza
    (`10**7`, `10**10`, `10000`) → `nonexistent`, nunca una excepción; el esquema acepta
    cualquier entero (revisión de la tarea 09b, ronda 1, should-fix 2, y ronda 2, should-fix 1).
  - *Día de la semana desconocido:* `wrong_type` ("juernes"); la tabla tiene español e inglés y
    no distingue acentos ni mayúsculas.
  - *Hora:* sin `hour` → faltante; sin `minute` → `:00`; `hour` fuera de 0–23 o `minute` fuera
    de 0–59 → `bad_format`; una hora de 1 a 12 se toma como llegó (D5.2).
  - *Fecha y hora juntas en el pasado (D):* solo puede pasar con la fecha de hoy (una anterior
    ya es `past`). Se reporta `time` con `past`, sale de `normalized` y la pregunta pide la hora;
    la fecha válida se conserva. El instante exacto de `now` no es pasado ("hoy a las 9" con
    `now` a las 09:00 es válido). Descartada: marcar los dos campos (pide otra vez una fecha
    válida).
  - *Redacción (C):* `nonexistent` → "{Etiqueta} indicada no existe."; `bad_format` →
    "{Etiqueta} está fuera del formato válido."; `past` → "{Etiqueta} indicada ya pasó.";
    `mismatch` → "Las partes de {etiqueta} que indicaste no dan el mismo día."; `wrong_type`
    sigue con "No pude leer {etiqueta}.". Las dos primeras son las frases de TASK.md línea 198;
    "indicada" concuerda con "la fecha" y "la hora", las únicas etiquetas con esas causas.
  - *Caso D (C):* la respuesta no añade la cabecera "Hay un problema con la fecha y la hora." ni
    cambia la pregunta por "¿Qué fecha y hora deseas utilizar?" (TASK.md línea 198): la
    estructura la fijan D43 y D17, con líneas de inválido y una pregunta por etiquetas que nombra
    también los tres faltantes.
- **Lo que ve el usuario:** el caso D (TASK.md líneas 361-371) responde "Anoté la fecha: día
  40, mes 11.", "Anoté la hora: 25:90.", "La fecha indicada no existe.", "La hora está fuera del
  formato válido." y la pregunta, que nombra el título, la fecha, la hora, la duración en minutos
  y los asistentes; nada se ejecuta. Una reunión completa y válida llega a la confirmación con
  la fecha ISO y la hora `HH:mm`.
- **Motivo:** RF-04 pide validar formatos, restricciones de negocio y coherencia de forma
  determinística (TASK.md líneas 159-168), y §2.1 fija `YYYY-MM-DD` y `HH:mm` (líneas 71-72);
  RF-05 y RF-06, no inventar e identificar el problema (líneas 184 y 186-200). Las reglas leen
  tipos, nunca nombres de tool ni de campo (línea 562).
- **Precisa D5 y D44:** D5.2 llama a la fecha y hora en el pasado error de coherencia sin nombrar
  el campo; D44 dice que `missing` es solo lo ausente. El texto de D5 y de D44 no se edita.
- **Lo prueban:** `test_every_kind_has_a_rule`, `test_date_forms`, `test_invalid_dates`,
  `test_incomplete_date_or_time_is_missing`, `test_time_forms`, `test_unreadable_time`,
  `test_date_and_time_together_in_the_past` (con el orden de `invalid` y el instante exacto),
  `test_ready_values_pass_the_contract`, `test_case_d_reports_date_and_time`,
  `test_case_d_reply_names_every_component` (sin cambios), `test_date_invalid_lines_in_the_reply`,
  `test_weekday_meeting` y `test_case_d_through_the_node`.
- **Pendiente** (limitaciones para las tareas 19 y 20):
  - *Caso D frente a la línea 198:* es lo primero que un evaluador compara con el enunciado
    (revisión de la tarea 09b, ronda 1, nota 5, y ronda 3, nota 4).
  - *Fecha incompleta:* "el viernes 27" pide la fecha sin decir que falta el mes; si el modelo
    devuelve después solo `{month: 11}`, D4 regla 5 reemplaza el valor entero y la fecha sigue
    faltando. Depende de que el modelo reenvíe la fecha completa (TASK.md líneas 145 y 174-178;
    ronda 3, nota 3). Si el autor la quiere, una línea "Falta el mes de la fecha.".
  - *Lecturas de la brief:* una fecha sin año que ya pasó se lee como la del año siguiente (se
    ve en la confirmación); el 29 de febrero sin año en un año no bisiesto no existe; "hoy" es la
    fecha local de la máquina, sin zona horaria.

**D57 · La validez cambia con el tiempo: los turnos que repiten lo pendiente vuelven a evaluar**
(2026-11-17) · autodecidida por el orquestador; confirmada por el autor (2026-11-17)

- **Decisión:** un turno que repite lo pendiente sin aplicar nada del mensaje evalúa otra vez los
  pasos guardados contra el `now` del turno (`_repeat_pending`, `core/turn.py`) y escribe el
  registro solo si el resultado difiere del guardado (estado, faltantes, inválidos, pregunta o
  hash); si no difiere, el delta sigue vacío. Vale para la rama *Misma tool* sin cambios
  (decisión F de la brief 09b: un mensaje repetido, un turno sin operaciones, solo una nota o un
  nombre rechazado) y para un "sí" que D45 o D7.4 rechazan (`confirm_other_tool`,
  `confirm_vetoed`; decisión AT, por el should-fix 1 de la revisión de la tarea 09b, ronda 1).
  Ninguna tool corre en ningún caso.
- **Cambia lo que se guarda:** solo cuando la validez ya cambió con el tiempo. "Hoy a las 10"
  confirmado a las 09:00 y repetido a las 10:30 pasa a `collecting` sin `confirmation_hash`, con
  "La hora indicada ya pasó." y la pregunta; "mañana" repetido al día siguiente guarda el hash de
  la fecha nueva. Por eso queda a la vista del autor como revisable, como D41. [el autor la
  confirmó el 2026-11-17]
- **Motivo:** desde la 09b la validez depende del momento del turno. Sin reevaluar,
  `_closing_line` recalcula el resumen contra `now` y omite la hora ya inválida: una
  confirmación sin la línea de la hora, con el registro en `awaiting_confirmation` (TASK.md
  líneas 186-188 y 280-284). Es lo que D47 ya hace con un "sí" de hash distinto.
- **Precisa D7.4, D9, D40, D42 y D45:** "no cambia nada" (D7.4, D45) y "el estado no cambia"
  (D40, D42 *Sin cambios*, D9 *Caso H*) se leen como "nada de lo que trae el mensaje": el mensaje
  nunca entra al registro (el test compara `steps`), pero el paso del tiempo sí puede invalidar
  la hora. Su texto no se edita.
- **Descartada:** dejarlo y listarlo como límite (el "sí" sigue protegido por D47, que nunca
  ejecuta con el hash distinto).
- **Lo prueban:** `test_time_that_passed_while_pending` (el mensaje repetido a las 10:30, que
  falla si la rama sin cambios devuelve `{}`, y un "sí" sobre el registro original, que vuelve a
  `collecting` por D47) y `test_turned_away_yes_reassesses_against_now` (las dos rutas de AT a
  las 10:30 y, con delta vacío, a las 09:30).
- **Pendiente:** *`confirm_incomplete` (limitación para la tarea 12 o para las 19 y 20):* un
  "sí" sobre un registro `collecting` no reevalúa. Con "hoy a las 10" recopilado a las 09:00 y
  faltando el título, un "sí" a las 10:30 repite la pregunta guardada y no pide la hora; el turno
  siguiente lo corrige (revisión de la tarea 09b, ronda 2, nota 2, y ronda 3, nota 1). Las
  plantillas provisionales tampoco reevalúan, pero no muestran resumen. [resuelto en D67, D68 y
  D71]

**D58 · Tarea 09b: el validador de fechas y horas conectado al turno** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** la 09b cierra D5 con D55, D56 y D57. Los demás detalles no cambian lo que ve el
  usuario, cuándo se ejecuta una tool ni qué se guarda, así que van en una línea cada uno (regla
  del autor desde la tarea 07):
  - *`now` (decisión E; precisa D42):* `TurnInput` gana `now: datetime`, local y naive,
    obligatorio y sin valor por defecto, en lugar del `today` que D42 reservaba, porque la
    coherencia de D5.2 necesita también la hora. El nodo lo lee con `datetime.now()`
    (`assistant/agent.py`), el único sitio del código que lee el reloj; el núcleo nunca lo lee y
    cada test dice contra qué momento valida (D5.1: "los tests la inyectan"). Descartado: un
    `default_factory=datetime.now` (un test de fechas que lo olvide depende del día).
  - *Firmas:* `validate(tool, args, now)`; cada regla de `RULES` recibe `(spec, value, now)` y
    las de la 09a lo ignoran; `shown_values(steps, now)`. `_assessment`, `_closing_line` y
    `_pending_outcome` reciben `now`, así que el resumen, el hash y los valores que recibe la
    tool salen del mismo momento.
  - *Fuera lo provisional:* se borran `UNCHECKED_KINDS`, `Validation.unchecked` y
    `DATE_TIME_NOT_CHECKED_YET`; `ready` es nada faltante ni inválido; `_closing_line` da la
    confirmación o la pregunta, y `last_question` ya nunca es nulo en `collecting`.
  - *Causas:* `Cause` gana `nonexistent`, `past`, `mismatch` y `bad_format`. El log no gana
    claves: `invalid=` las lleva (`invalid=date:nonexistent,time:bad_format`).
  - *Tabla de días:* `WEEKDAYS` vive en `core/validation.py`, junto a la regla, no en el
    registro, y compara con `core.text.normalize`.
  - *Guardas de forma:* una clave fuera de los componentes o una parte que no es entera →
    `wrong_type`. Por el esquema (`DateParts`, `TimeParts`) y el filtro del registro no llegan;
    el validador no confía en la forma, y tienen test (revisión de la tarea 09b, ronda 1, nota 3).
  - *Coherencia por tipo:* `_time_in_the_past` empareja fecha y hora por `FieldKind`, nunca por
    nombre, y solo si la tool tiene exactamente un campo de cada tipo: con más no está declarado
    qué hora va con qué fecha (TASK.md línea 562).
  - *Resumen:* la confirmación muestra los normalizados tal cual ("- La fecha: 2026-11-20",
    "- La hora: 10:30"); el día de la semana o la fecha en palabras quedan fuera. Las líneas
    "Anoté …" siguen con los componentes como llegaron (D42).
  - *`NOW` de los tests:* `datetime(2026, 11, 17, 9, 0)`, un martes, en
    `tests/interpretations.py`. `phase0/scenarios.py` y `tests/case_c_judge.py` lo pasan, así
    que el arnés y el juez nunca leen el reloj; `B1` y `F1` no traen fecha, y los registros y
    resúmenes con que se midió D32 no cambian.
  - *Tests por el nodo:* usan el reloj real, así que solo pasan fijos que no dependen del día:
    `D1` (inválido cualquier día), `C1` (su "mañana" no llega al validador) y la reunión de
    `test_meeting_through_the_node`, en el año 2099. `test_case_d_through_the_node` quita `op=`
    antes de buscar "40", "25" y "90" en la línea de log, porque el uuid hex puede contenerlos.
- **Motivo:** RF-04 pide validación determinística (TASK.md líneas 166-168) y §7, tests
  automatizados (línea 522): con `now` inyectado, un test de fechas no depende del día en que
  corre. RF-07 guarda el estado de validación (línea 218) y RF-08 ejecuta solo con todo validado
  (líneas 239-241): una reunión llega por fin a `awaiting_confirmation` y, con un "sí", al mock.
- **Resuelve:** el *Pendiente* de D44 (el caso D, el `validate` repetido de `_closing_line` y
  `test_complete_meeting_does_not_reach_confirmation`, reemplazado por
  `test_meeting_through_the_node`) y el de D47 (nota 2: `test_yes_with_a_note_never_counts`
  reescrito sobre una reunión en `awaiting_confirmation` con "Sí, y quita a Ana", que no
  ejecuta; nota 4: D55).
- **Lo prueban:** `test_validation_depends_only_on_now`, `test_complete_meeting_awaits_confirmation`
  (el mismo mensaje otra vez da delta vacío), `test_yes_with_a_note_never_counts` (después, un
  "sí" corre una vez con `2026-11-20` y `10:30`), `test_meeting_through_the_node`
  (`schedule_meeting` corre una vez por el `Runner`, con su línea de resultado y su entrada en
  `history`), `test_weekday_meeting` (el hash tras una fusión con cambios es el de lo mostrado) y
  los de D55, D56 y D57; `grep -rn "now()\|today()" core` da vacío y `datetime.now()` sale una
  vez, en `assistant/agent.py`.
- **Pendiente:**
  - *Reloj del nodo:* ningún test lo fija; la única mutación que sobrevive a la revisión es un
    `now` fijo en el nodo, y solo la vigila el grep (revisión de la tarea 09b, ronda 3, nota 5).
    La cerraría un test que parchee `datetime` en `assistant.agent` y pase "mañana" por el
    `Runner`. [resuelto en D63]
  - *Tamaño:* `core/turn.py` tiene 510 líneas (tarea 15) y `core/validation.py`, 263 (ronda 3,
    nota 7).

**D59 · Cada paso de `history` dice qué pasó: `outcome`, `result` y `error`** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** cada paso de una entrada de `history` (D49) es `{tool, values, outcome, result}`
  (`step_entry`, `core/history.py`), más `error` tras una excepción. `outcome` es `ran` (la tool
  corrió y respondió un éxito con su ID), `reused` (la misma ejecución ya estaba hecha: el
  resultado guardado bajo la clave, sin ejecutar; D9.1), `failed` (`success` falso; D9.3) o
  `uncertain` (D9.2, D60). `result` es el dict de la tool (el guardado si `reused`, el del fallo
  si `failed`) o nulo si no hay uno legible; `error` es el nombre del tipo de la excepción, nunca
  su texto. El `status` de la entrada sale de sus pasos (`closed_status`): `completed` si todos
  son `ran` o `reused`; si no, el `outcome` del paso que cortó. Solo se listan los pasos
  intentados. Decisión A de la brief 11, opción (a).
- **Lo que se guarda:** desde la 11, todo cierre de un "sí" va a `history` como `completed`,
  `failed` o `uncertain`, con la marca de cada paso. Ninguna regla la lee (D6) y el modelo no la
  ve (D14). El usuario no ve nada de ella.
- **Motivo:** RF-07 pide mantener el estado de ejecución y los resultados de las tools (TASK.md
  líneas 220-222). Un `status` por registro no distingue, en un plan, un paso reutilizado de uno
  que corrió, que es lo que D9.3 describe para el reintento ("el paso 1 encuentra `done` … y la
  notificación corre de nuevo"; líneas 270-272). La 14b lo reusa sin cambiarlo.
- **Precisa D49:** sus pasos `{tool, values, result}` ganan `outcome` y, tras una excepción,
  `error`; `result` puede ser nulo. El texto de D49 no se edita.
- **Descartadas:** (b) solo `reused: true` en los pasos reutilizados (no distingue un paso fallido
  de uno incierto dentro de un plan); (c) un `status` nuevo, `duplicate`, sin marca por paso (no
  sirve a un plan con un paso reutilizado y otro ejecutado).
- **Lo prueban:** `test_confirmed_operation_goes_to_history` (ahora con `"outcome": "ran"`),
  `test_case_h_same_request_after_running` (dos entradas `completed`, la segunda con el paso
  `reused` y el mismo `result`), `test_started_without_done_is_uncertain`,
  `test_failed_result_releases_the_key` y `test_steps_after_one_that_does_not_complete_never_run`.
- **Pendiente:** la 14b decide cómo se ven los pasos no intentados de un plan, que hoy no se
  listan.

**D60 · Solo un éxito legible cierra `done`; una excepción o un resultado mal formado es
`uncertain`** (2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** `classified` (`core/executions.py`) lee la respuesta de la tool una sola vez y da
  su `outcome` y su ID:
  - *`ran`:* `success` exactamente `True` y un ID no vacío tras quitar espacios en el campo que
    declara el registro; la clave pasa a `done` con el resultado (`settled`).
  - *`failed`:* `success` exactamente `False`; la clave se borra (D9.3).
  - *`uncertain`:* cualquier otra cosa: una excepción de la tool (`TimeoutError`, `RuntimeError`
    y también el `ValidationError` del contrato del mock, D12), un resultado sin `success`, con
    `success: True` sin ID o con un ID vacío o en blanco, uno que no es dict o uno que no pasa por
    `json.dumps`. La clave queda `started` y nada se reejecuta (D9.2). Un resultado que no es
    dict o no es JSON se guarda en `history` como nulo.
  - *Clave ya guardada:* un `done` cuyo resultado no se lee como `ran` también es `uncertain`
    (`from_stored`).

  Decisión B de la brief 11, opción (a), con dos ajustes de la revisión de la tarea 11: el ID
  vacío (ronda 1, nota 3) y el resultado que no es JSON (ronda 1, should-fix 1).
- **Lo que ve el usuario:** donde antes una excepción reventaba el turno con una traza y sin
  escribir `pending` ni `history` (D50, *Pendiente*), ahora recibe la plantilla de estado
  incierto (D61), `pending` se vacía y el cierre va a `history`. Un "Listo: … None." ya no es
  posible.
- **Motivo:** es la mecánica literal de D9.2: sin `done` no se sabe si el efecto ocurrió, y ante
  la duda se ejecuta a lo más una vez (TASK.md líneas 286, 451 y 580). Solo un fallo que la tool
  declara dice que nada ocurrió, y solo ese libera la clave (D9.3; líneas 270-272). La respuesta,
  `history` y el log usan el ID de esa única lectura.
- **Precisa D9.2, D9.3 y D12:** D9.2 nombra "caída o timeout"; aquí cuenta toda excepción y todo
  resultado ilegible. D9.3 libera la clave con `success=false`; un `success: False` que no es
  JSON queda `uncertain` y su clave no se libera, porque la comprobación de JSON va primero
  (inalcanzable con los mocks; revisión de la tarea 11, ronda 2). Un `ValidationError` del
  contrato, que D12 quiere ver fallar, no revienta el turno: cierra `uncertain`, con
  `tool_error=ValidationError` en el log y `error` en `history`. El texto de D9.2, D9.3 y D12 no
  se edita.
- **Descartadas:** (b) que una excepción libere la clave como un fallo (repetir reejecuta, con
  riesgo de duplicado si el efecto ocurrió; contra D9.2); (c) dejar salir la excepción, como hasta
  la 10b (el turno falla sin escribir nada, la clave queda `started` y el usuario ve una traza).
- **Lo prueban:** `test_malformed_result_is_not_a_success` (11 formas: sin ID, ID nulo, vacío o en
  blanco, un `datetime` dentro, sin `success`, `success` `"yes"` o `1`, un texto, `None`, y
  `{"success": False}` sin ID, que es `failed`), `test_timeout_is_uncertain_and_never_retried`
  (`TimeoutError` y `RuntimeError`), `test_started_without_done_is_uncertain`,
  `test_failed_result_releases_the_key` y `test_results_read_the_registry_id_field` (`.get` y
  `[]` contados: una sola lectura).
- **Pendiente** (limitaciones para las tareas 14b y 19):
  - *`ValidationError` del contrato:* se sabe que nada corrió y aun así la clave queda `started`
    24 h y el usuario lee "No pude confirmar…". Es inalcanzable (los valores ya pasaron el
    validador); la 14b lo vuelve a mirar si añade el `ticket_id` al `message` después de validar
    (revisión de la tarea 11, ronda 1, nota 9).
  - *Un `done` ilegible:* la rama de `from_stored` que lo da por `uncertain` no tiene test (ronda
    2, nota 2).
  - *Resto en Redis:* un resultado JSON que Redis no puede codificar en UTF-8 (un sustituto
    suelto, `"\ud800"`) lanza en `complete`, después de que la tool corrió: el turno revienta sin
    escribir `pending` ni `history`, la clave queda `started` y el "sí" siguiente da `uncertain`
    (ronda 2, nota 1). Es el límite de un error de Redis a mitad del turno (D62).

**D61 · Redacción de las respuestas tras un "sí": reutilizado, fallido e incierto** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** `step_line` (`core/replies.py`) escribe una línea por paso intentado, con frases
  del registro y sin el `message` de la tool (D48):
  - `ran`: "Listo: {done_phrase} {ID}." (`result_line`, el mismo texto de D48);
  - `reused`: "Ya {done_phrase} {ID} con estos mismos datos; no lo repetí.";
  - `failed`: "No pude {action_phrase}: la herramienta respondió con un error. Si me lo pides de
    nuevo, lo intento otra vez.";
  - `uncertain`: "No pude confirmar si llegué a {action_phrase}; no lo voy a reintentar para no
    duplicarlo."

  `result_line` recibe el ID ya leído (`result_line(tool, result_id)`), no el resultado.
  Decisión C de la brief 11.
- **Lo que ve el usuario:** "Ya creé el ticket TCK-… con estos mismos datos; no lo repetí." al
  confirmar otra vez una solicitud ya ejecutada; "No pude crear un ticket: la herramienta
  respondió con un error. …" con `MOCK_FAIL`; "No pude confirmar si llegué a crear un ticket; no
  lo voy a reintentar para no duplicarlo." tras una excepción o con la clave `started`. Solo
  cambia la redacción; el contenido lo fijan D9.1–D9.3.
- **Motivo:** D9.1 manda devolver el resultado guardado, D9.2 decir que no se pudo confirmar y que
  no se reintentará, y D9.3 que repetir la solicitud la ejecuta; el agente da feedback del
  resultado y del error (TASK.md líneas 24, 246, 497 y 499). Las frases salen del registro, no de
  un nombre de tool (línea 562), y no llevan género gramatical: con `short_name`, "Ese ticket ya
  existía" daría "Ese reunión".
- **Precisa D9.1:** su ejemplo "Ese ticket ya existía: TCK-0001" se dice "Ya creé el ticket
  TCK-0001 con estos mismos datos; no lo repetí.". El texto de D9.1 no se edita.
- **Lo prueban:** `test_case_h_same_request_after_running`, `test_case_h_through_the_node`,
  `test_failed_result_releases_the_key`, `test_started_without_done_is_uncertain`,
  `test_timeout_is_uncertain_and_never_retried`, `test_result_line_per_tool` y
  `test_result_line_reads_the_registry_id_field`.
- **Pendiente:** la redacción del reporte de un plan con un paso hecho y otro fallido es de la 14b
  (TASK.md línea 268); hoy serían dos líneas, "Listo: …" y la de fallo.

**D62 · Registro de ejecuciones: la clave, Redis y la variante en memoria** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** decisión D de la brief 11, con una desviación del implementador (*Redis*, más
  abajo):
  - *Clave:* `execution_key` es el sha256 del JSON canónico (`sort_keys`, `ensure_ascii=False`) de
    `[session_id, tool, normalized]`, como `confirmation_hash` (D47). `normalized` son los valores
    que cubre el hash de D7.2 y que recibe la tool (D46).
  - *Protocolo* (`Executions`, `core/executions.py`, sin redis ni ADK): `reserve(key)` devuelve
    `None` si la reservó ahora y, si no, lo guardado (`started`, o `done` con el resultado), sin
    cambiar nada; `complete(key, result)`; `release(key)`. El registro no tiene reglas: las de
    D9 están en el núcleo (`_run_confirmed`, `settled`, `from_stored`; D46). Un valor guardado
    ilegible cuenta como `started`.
  - *Redis* (`RedisExecutions`, `assistant/executions.py`, cliente síncrono de redis-py): clave
    `idem:<hex>` con valor JSON. `reserve` es `SET NX GET EX 86400`, una sola orden atómica
    (Redis ≥ 7.0): no hay hueco en el que la clave venza o se libere entre `SET NX` y `GET`, y
    dos turnos nunca reservan la misma clave. `complete` es `SET XX EX 86400`: las 24 h cuentan
    desde que la tool terminó y nunca revive una clave liberada o vencida. `release` es `DEL`.
    Un error de Redis no se captura: llega al turno como cualquier error de infraestructura.
  - *En memoria* (`InMemoryExecutions`): un dict que guarda JSON (lo que devuelve es una copia, y
    un resultado que no es JSON falla como en Redis), con `complete` como `SET XX` y sin
    vencimiento.
  - *Quién usa cuál:* la CLI pasa `RedisExecutions` sobre un cliente síncrono del mismo
    `REDIS_URL` que las sesiones, cerrado al salir; `build_root_agent` sin `executions` crea uno
    en memoria por agente construido (el `app` del módulo, que usa `adk web`, y cada runner de
    test); `handle_turn` sin `executions` usa uno de usar y tirar por llamada.
- **Lo que se guarda:** en la CLI, una clave `idem:` por paso ejecutado, en el mismo Redis que las
  sesiones y fuera de `session.state`, con TTL de 24 h. `docker compose down -v` la borra junto
  con las sesiones.
- **Motivo:** D9.1 pone el registro en Redis porque el estado solo se escribe con el evento final
  (D1.1) y una caída entre la tool y ese evento lo perdería (TASK.md líneas 286, 443-449 y 558).
  Con el cliente síncrono el núcleo sigue síncrono y en Python puro (D46, *Costo aceptado*, que la
  brief 11 confirma). La variante en memoria deja probar D9.1–D9.3 sin Redis (línea 522) y sirve
  a `adk web`, cuyas sesiones tampoco viven en Redis.
- **Precisa D9.1, D9.2 y D46:** D9.1 dice "registro en Redis"; con `adk web` el registro vive en
  memoria, no vence y se pierde al reiniciar, así que ahí la clave de D9.2 no "expira con el TTL".
  D46 decía "reserva con `SET NX`". El texto de D9.1, D9.2 y D46 no se edita.
- **Descartadas:** `SET NX` seguido de `GET` (la clave puede vencer o liberarse entre las dos
  órdenes); un registro en memoria compartido por todos los agentes del proceso (los tests
  compartirían claves).
- **Lo prueban:** `test_execution_key_binds_session_tool_and_values`,
  `test_in_memory_reserve_complete_release`, `test_complete_never_brings_back_a_released_key`,
  `test_an_unreadable_record_counts_as_started`, `test_redis_executions_reserve_is_set_nx_with_ttl`
  (`NX`, `XX`, TTL en (0, 86400], prefijo, y otra instancia con un cliente nuevo ve `done`),
  `test_resent_yes_survives_a_new_process`, `test_same_values_in_another_session_run_again`,
  `test_without_executions_each_call_has_its_own_registry` y
  `test_cli_uses_the_redis_registry_and_mock_fail`.
- **Pendiente** (limitaciones para la tarea 19):
  - *Sesión, no app ni usuario:* la clave lleva el id de sesión, y ADK identifica una sesión por
    app, usuario e id. En la CLI el usuario es fijo (D24); con `adk web`, dos usuarios con el
    mismo id de sesión compartirían claves (revisión de la tarea 11, ronda 1, nota 8).
  - *En memoria:* no vence y se pierde al reiniciar; un `uncertain` bloquea esa ejecución hasta
    reiniciar el proceso.
  - *Un `uncertain` bloquea 24 h en la CLI:* la misma solicitud, con los mismos valores en la misma
    sesión, responde "No pude confirmar…" hasta que la clave vence; hay que cambiar algún valor,
    como en la limitación de D9.
  - *Error de Redis:* si Redis cae después de que la tool corrió, la clave queda `started`, el
    turno falla sin escribir `pending` ni `history`, y el "sí" siguiente da `uncertain` (D9.2).
  - *Sin test:* que la CLI cierre el cliente síncrono al salir (ronda 2, nota 5) y que cada
    `build_root_agent` cree el suyo (no verificado por mutación); dos procesos sobre la misma
    clave se apoyan en la atomicidad de `SET NX GET`, sin prueba de carrera.

**D63 · Tarea 11: idempotencia en el turno, `MOCK_FAIL` y log** (2026-11-17) · autodecidida por
el orquestador; revisable

- **Decisión:** la 11 cierra D9.1–D9.3 y la parte de D8.3 que no es del plan con D59–D62. Los
  demás detalles no cambian lo que ve el usuario, cuándo se ejecuta una tool ni qué se guarda,
  así que van en una línea cada uno (regla del autor desde la tarea 07):
  - *`_run_confirmed`:* sigue siendo el único `.execute(` (D46). Por cada paso calcula la clave y
    la reserva; con la clave nueva llama a la tool dentro de `try/except Exception`, y con una ya
    guardada no llama. Corta en el primer paso que no termina `ran` o `reused` (D8.1; TASK.md
    línea 270), vacía `pending` en todo cierre y agrega la entrada a `history` (D6).
  - *`_confirm`:* devuelve `None` para decir "ejecutar" y `_decide` llama a `_run_confirmed`; el
    orden de sus comprobaciones (D45, D7.4, D38, D7.1, D47, D55) no cambia. Desviación del
    implementador, para no pasar `history` y `executions` por `_confirm`.
  - *`session_id` (decisión E; precisa D42):* `TurnInput` gana `session_id: str`, obligatorio y
    sin valor por defecto, como `now` (D58): un `session_id` olvidado haría que dos sesiones
    compartieran claves en silencio. El nodo lo toma de `ctx.session.id`; los tests usan
    `SESSION = "s-test"` (`tests/interpretations.py`). Descartado: un valor por defecto `""`.
  - *Fallo simulado (decisión F; D8.3):* `simulated(contract, id_field, "failure" | "timeout")`
    (`core/mock_tools.py`) valida el contrato y responde `{"success": False, <ID>: None,
    "message": …}` o lanza `TimeoutError`. Los tres mocks por defecto no cambian y el núcleo no lee
    variables de entorno.
  - *`MOCK_FAIL` (decisión F; precisa D24):* la CLI lo lee tras comprobar Redis y
    `GOOGLE_API_KEY`, justo antes de armar el agente. Son nombres de tool separados por coma, que
    pasan a su variante de fallo en el `TOOLS` de su proceso, con un aviso en stderr ("MOCK_FAIL:
    create_ticket responderán con un fallo."). Un nombre desconocido sale con código 1 y
    "MOCK_FAIL nombra herramientas que no existen: …", sin traza. Solo fallo, no timeout: el
    estado incierto se prueba en los tests.
  - *Log (decisión G; precisa D50):* `results=` pasa a `tool:ID:outcome` por paso intentado (`ID`
    es `-` si no hay); después va `idem=` con la clave de cada paso, en el mismo orden, y
    `tool_error=<Tipo>` si una tool lanzó. `tool_error` es una clave propia, porque `error=` ya es
    el fallo del modelo (D21); solo el último paso intentado puede llevarla. La clave de
    idempotencia es un hash, no un valor del usuario.
  - *`route`:* sigue en `confirmed` también con `failed` y `uncertain`; el resultado de cada paso
    va en `results=`.
  - *`TurnOutcome.results`:* lista de `StepRun` (`tool`, `values`, `key`, `outcome`, `result`,
    `result_id`, `error`), que usan la respuesta, `history` y el log.
  - *CLI:* arma su propio `App(name=APP_NAME, root_agent=…)` con el registro de Redis; el `app` del
    módulo sigue siendo el que encuentra `adk web`.
- **Lo que ve el usuario (caso H, TASK.md líneas 441-451):** con la operación pendiente, repetir la
  solicitud no cambia nada y repite la pregunta o la confirmación (D9, D40, D47). Ya ejecutada, la
  misma solicitud abre una confirmación nueva, con otro `operation_id`, y su "sí" responde "Ya
  creé el ticket … con estos mismos datos; no lo repetí." con el mismo ID, sin ejecutar. Un "sí"
  reenviado tras un turno que no se guardó encuentra `done` y tampoco ejecuta, aun desde otro
  proceso. Con la clave `started`, una excepción o un resultado ilegible, la respuesta dice que no
  se pudo confirmar y que no se reintentará; con `success=false`, que falló y que pedirlo de nuevo
  lo intenta otra vez.
- **Motivo:** TASK.md pide evitar duplicados ante reintentos o mensajes repetidos con un mecanismo
  de idempotencia (líneas 286 y 443-449), manejar errores y ejecuciones duplicadas (línea 25), dar
  feedback del resultado (líneas 24 y 246) y no ejecutar la segunda tool si falla la primera
  (línea 270). El log suma lo que se ejecutó y su clave (línea 471). Las reglas viven en el núcleo
  y se prueban sin Redis; el nodo solo pasa el registro (D46; líneas 568-570).
- **Resuelve:** el *Pendiente* de D46 (idempotencia, corte en un fallo y excepción de una tool),
  los de D48, D49 y D50 (`success`, la excepción, el ID leído dos veces y la idempotencia; con
  D60 también el `result` serializable) y el *Reloj del nodo* de D58
  (`test_node_passes_session_and_clock`).
- **Lo prueban:** `test_case_h_repeated_while_pending`, `test_case_h_same_request_after_running`,
  `test_resent_yes_after_unsaved_turn`, `test_steps_after_one_that_does_not_complete_never_run`
  (fallo y timeout), `test_simulated_failure_and_timeout`, `test_case_h_through_the_node`
  (sesiones `s-h` y `s-h2`; la línea de log del cuarto turno con `:reused` e `idem=`),
  `test_node_passes_session_and_clock` (`session_id` y el reloj parcheado en `assistant.agent`),
  `test_log_line_of_a_failed_and_an_uncertain_step`, `test_case_g_through_the_node` y
  `test_case_b_through_the_node` (con `results=…:ran idem=…`),
  `test_cli_uses_the_redis_registry_and_mock_fail` y
  `test_cli_exits_clearly_with_an_unknown_mock_fail`; `grep -rn "\.execute(" core assistant` da
  solo `_run_confirmed`, y `core/` no importa `redis` ni lee `MOCK_FAIL`.
- **Pendiente:**
  - *Explicación escrita (tarea 19):* qué hace el sistema cuando no puede saber si una ejecución
    terminó (TASK.md línea 451) y las preguntas 6 y 7 de §9 (líneas 558 y 559), con este
    mecanismo y los límites de D60 y D62; que el duplicado se detecta al ejecutar, después del
    "sí" (D9, *Limitaciones*).
  - *Plan (14b):* el reporte del fallo parcial, el `ticket_id` en el `message` y la clave del paso
    2 calculada antes de añadirlo; la costura ya corta en el primer paso no hecho y marca cada
    paso (D59).
  - *`MOCK_FAIL` con el modelo real:* solo se probó por `_run` con el modelo falso; `adk web` con
    el registro en memoria no se lanzó.
  - *Tamaño:* `core/turn.py` tiene 532 líneas (tarea 15).

**D64 · Cambio de intención: una línea fija dice que se descartó lo pendiente** (2026-11-17) ·
decidida por el autor

- **Decisión:** en un cambio de intención (*Otra tool*, D3.1), la respuesta antepone a la del
  flujo nuevo la línea fija del descarte (D66) con las acciones de lo abandonado: "No voy a
  {acciones}; descarté lo que tenía pendiente.". Si el filtro del registro descartó algo, la línea
  de D10 va primero (D41). Respuesta del autor a la brief 12, decisión B, opción (a).
- **Lo que ve el usuario:** en el caso F, con la reunión con Juan pendiente, "Mejor crea un ticket
  para revisar el servidor" responde "No voy a programar una reunión; descarté lo que tenía
  pendiente.", "Anoté la descripción: revisar el servidor." y la pregunta del ticket (el título,
  la prioridad y a quién se asigna). Nada se ejecuta.
- **Motivo:** el caso F pide detectar el cambio de intención (TASK.md línea 411). Sin la línea, el
  usuario no sabe si la reunión sigue en pie, y un "sí" posterior ya no la ejecutaría sin que
  nadie se lo haya dicho (líneas 282-284). Es la misma línea del descarte por `cancel` o `reject`,
  así que el usuario lee lo mismo cuando algo pendiente se deja de lado.
- **Precisa D3.1:** D3.1 fija qué pasa con el registro, no qué ve el usuario. Su texto no se
  edita.
- **Descartada:** (b) sin línea: la respuesta del flujo nuevo como si nada estuviera pendiente, y
  el abandono solo en `history` y en el log (`route=other_tool:R2`).
- **Lo prueban:** `test_case_f_other_tool_abandons_and_starts_the_new_flow` (`request_action` y
  `provide_info`), `test_case_f_through_the_node` y `test_d10_line_before_every_new_pending_branch`
  (la línea de D10, la del descarte y el flujo nuevo, en ese orden).
- **Pendiente:** la línea también precede a R3 y R4 tras un cambio de intención, aunque el turno
  vuelva a pedir la tool abandonada (D71, AW 1).

**D65 · Un cierre sin ejecutar guarda los pasos como estaban en `pending`** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** un registro que se cierra sin ejecutar (`cancelled` o `abandoned`) va a
  `history` como `{operation_id, status, steps}` con los `steps` del registro tal como estaban
  en `pending`, `{tool, args}`: las palabras del usuario tras la fusión, sin normalizar ni
  validar. No lleva `outcome`, `values` ni `result`, porque ninguna tool recibió nada. Lo arma
  `_closed_without_running` (`core/turn.py`) con `closed_entry` y `appended` (D49); las
  constantes `CANCELLED` y `ABANDONED` viven en `core/history.py`. Decisión A de la brief 12,
  opción (a).
- **Lo que se guarda:** desde la 12, un "no", un "cancela" o un cambio de intención vacían
  `pending` y agregan la entrada a `history` (las últimas 10). Ninguna regla la lee (D6): ni la
  fusión (D4), ni la compuerta (D3), ni `pending_summary` (D14). El usuario no la ve.
- **Motivo:** D6 manda guardar el cierre "con su estado final, sus valores"; RF-07 pide mantener
  la intención y el estado de ejecución como estado estructurado, separado de la conversación
  (TASK.md líneas 210-222 y 229). En el caso F deja a la vista que la reunión con Juan quedó
  archivada y que el ticket nuevo no la hereda (línea 413).
- **Precisa D59:** "cada paso … es `{tool, values, outcome, result}`" vale para los cierres tras
  un "sí". El texto de D59 no se edita.
- **Descartadas:** (b) `steps: []` (pierde "sus valores" de D6); (c) la forma de D59 con
  `outcome: "not_run"`, `values` normalizados y `result: null` (más código y unos `values` que
  nadie ejecutó).
- **Lo prueban:** `test_case_f_other_tool_abandons_and_starts_the_new_flow`,
  `test_cancel_or_plain_reject_discards_and_archives`,
  `test_closing_without_running_keeps_the_last_ten` (un `history` de 10 sigue en 10, con el
  cierre al final) y `test_case_f_through_the_node` (la reunión `abandoned` con
  `attendees == ["Juan"]` en la sesión).

**D66 · Redacción del descarte: "No voy a {acciones}; descarté lo que tenía pendiente."**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** `cancel`, y `reject` sin argumentos (D69), responden solo con una línea fija:
  "No voy a {acciones}; descarté lo que tenía pendiente.", con las `action_phrase` de
  `pending["tools"]` unidas con "ni" (`discarded_line`, `core/replies.py`). Es la misma línea que
  D64 antepone a un flujo nuevo. Decisión C de la brief 12.
- **Lo que ve el usuario:** con un ticket pendiente, "Mejor cancela" responde "No voy a crear un
  ticket; descarté lo que tenía pendiente.", y un "sí" posterior, "No hay ninguna operación
  pendiente." (TASK.md línea 284). Solo cambia la redacción; el contenido lo fija D7.3.
- **Motivo:** las frases salen del registro y no de un nombre de tool (TASK.md línea 562). Sin
  "Listo", que es la palabra de un resultado (D48), y sin valores.
- **Descartada:** la frase de la regla 1 de la aclaración ("No inicié ninguna acción…"), que no
  es cierta cuando la operación ya estaba iniciada y esperando el "sí".
- **Lo prueban:** `test_cancel_or_plain_reject_discards_and_archives`,
  `test_discarded_line_joins_actions_with_ni` y `test_cancel_through_the_node`.

**D67 · `question` y `other` con algo pendiente: las capacidades sin pregunta y lo pendiente**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** con un registro en `collecting` o `awaiting_confirmation`, `question` y `other`
  responden la frase de capacidades de D20 sin su "¿Qué necesitas?" (`capabilities(ask=False)`)
  y después lo pendiente repetido (la pregunta, o la confirmación con sus valores) por
  `_repeat_pending`. Nada del mensaje entra al registro, aunque traiga operaciones de la tool
  pendiente o de otra tool `explicit`. Decisión D de la brief 12, opción (a).
- **Lo que ve el usuario:** con un ticket pendiente, "¿Puedes enviar notificaciones por SMS?"
  responde "Puedo ayudarte a crear un ticket, programar una reunión o enviar una notificación." y
  la pregunta del ticket. No invita a una solicitud nueva que abandonaría lo pendiente.
- **Lo que se guarda:** solo lo que D57 permite: el registro se escribe si su validez cambió con
  el tiempo; si no, el delta es vacío.
- **Motivo:** D3 (*Resto*) manda "una plantilla" sin tocar el estado; una intención informativa
  no inicia ni cambia nada (TASK.md línea 554), y el resumen nunca sale sin la línea de una hora
  que ya pasó (D57).
- **Precisa D3 y D20:** "no tocan el estado" se lee como en D57 ("nada de lo que trae el
  mensaje"). La plantilla de D20 sin nada pendiente no cambia. El texto de D3 y D20 no se edita.
- **Descartadas:** (b) la plantilla de D20 tal cual (termina en "¿Qué necesitas?" y el usuario
  pierde de vista lo pendiente); (c) solo lo pendiente repetido, como D40 (no contesta la
  pregunta).
- **Lo prueban:** `test_question_or_other_with_pending_keeps_state` (`Q1` y `K3` con `A1` y con
  `H1`), `test_question_or_other_with_operations_keeps_state` (un dato de la tool pendiente y
  otra tool `explicit`) y `test_repeating_branches_reassess_against_now`.

**D68 · Solo `plausible` de otra tool: nada del mensaje entra al registro** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** la rama *Solo `plausible` de otra tool* de D3 es: `act` `request_action` o
  `provide_info`, ninguna operación `explicit` de una tool no pendiente y alguna operación de una
  tool no pendiente (por eliminación, `plausible`). Nada del mensaje entra al registro, tampoco
  las operaciones de la tool pendiente de ese turno; lo pendiente se repite con
  `_repeat_pending` (D57), como D45 trata un "sí" que mezcla otra tool. Ruta
  `plausible_other_tool`. Decisión E de la brief 12.
- **Lo que ve el usuario:** la última pregunta o la confirmación pendiente, sin cambios. Una
  `plausible` nunca descarta un flujo ni inicia otro (TASK.md líneas 128-134).
- **Motivo:** lectura literal de D3 ("el registro pendiente no se toca y se repite su última
  pregunta"). Fusionar los datos de la tool pendiente convertiría una lectura dudosa del modelo
  en un cambio guardado.
- **Descartada:** fusionar los datos de la tool pendiente e ignorar la `plausible`.
- **Lo prueban:** `test_only_plausible_of_another_tool_repeats_the_question` (una `plausible`
  sola y junto a `priority="alta"` del ticket pendiente: delta vacío y `steps` sin la prioridad)
  y `test_repeating_branches_reassess_against_now`.
- **Pendiente:** *limitación para la tarea 20:* "prioridad alta, y quizá avísale a Carlos", si
  la segunda parte llega `plausible`, no anota la prioridad; el usuario la tiene que repetir.

**D69 · "`reject` con argumentos" (D7.3): un argumento presente de una tool pendiente**
(2026-11-17) · autodecidida por el orquestador; revisable

- **Decisión:** un `reject` tiene argumentos si alguna operación de una tool pendiente trae algún
  argumento presente tras el filtro del registro y sin ausentes (`drop_absent`, D4 regla 2).
  Entonces es una corrección: pasa por `_merge_steps` y `_after_merge` (ruta `reject_merged`), y
  un cambio vuelve a mostrar la confirmación con hash nuevo o vuelve a `collecting` (D47); sin
  cambios se repite lo pendiente (D40, D57); un nombre no escrito se rechaza (D38). Nunca ejecuta.
  Una operación de la tool pendiente sin argumentos, una `plausible` de otra tool o nada es "sin
  argumentos" y descarta (`cancelled`, D66). Decisión F de la brief 12.
- **Lo que ve el usuario:** con un ticket en confirmación asignado a Carlos, "No, asígnalo a Ana"
  responde "Cambié a quién se asigna: Carlos → Ana." y la confirmación con los valores nuevos; el
  "sí" siguiente crea el ticket asignado a Ana. "No, el ticket no" descarta.
- **Motivo:** D7.3 manda incorporar la corrección y volver a pedir confirmación, que la línea 145
  de TASK.md obliga a no perder y RF-05 a validar otra vez (líneas 179-182); "contradicciones
  entre argumentos" es la pregunta de defensa 5 (línea 557).
- **Lo prueban:** `test_reject_with_args_is_a_correction` (la corrección y su "sí", el mismo valor
  sin cambios y una corrección en `collecting`) y
  `test_cancel_or_plain_reject_discards_and_archives` (`S2`, `reject` con una operación sin
  argumentos y con una `plausible` de otra tool).
- **Pendiente:**
  - *Limitación para la tarea 20:* un "no" al que el modelo añada un nombre que el usuario no
    escribió no descarta: responde la línea de nombre rechazado y repite lo pendiente; un segundo
    "no" o "cancela" sí descarta (revisión de la tarea 12, ronda 3, nota 4).
  - *Sin test (tarea 15):* la parte "sin ausentes": un `reject` con `priority: ""` descarta (sonda
    de la revisión de la tarea 12, ronda 3), pero cambiar `drop_absent(op["args"])` por
    `op["args"]` no rompe ningún test (should-fix 1 de esa ronda, abierto).

**D70 · `reject` o `cancel` con solo una `plausible` de otra tool descarta** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** la rama *Solo `plausible` de otra tool* (D68) solo toma `request_action` y
  `provide_info`. Un "no" o un "cancela" acompañado solo de una `plausible` de otra tool archiva
  lo pendiente como `cancelled` (D7.3, D66) y no inicia nada.
- **Lo que ve el usuario:** "No, quizá avísale a Carlos" con un ticket pendiente responde "No voy
  a crear un ticket; descarté lo que tenía pendiente.". Nada `plausible` se aplica ni se ejecuta.
- **Motivo:** el acto del usuario es descartar; la `plausible` no descarta, lo hace el "no". Nota 2
  de la revisión de la tarea 12, ronda 2.
- **Precisa D3:** su rama *Solo `plausible`* no nombra `act`, y está antes de *Resto*; leída al
  pie de la letra, ese "no" no tocaría el registro. Aquí `reject` y `cancel` van a D7.3. El texto
  de D3 no se edita.
- **Lo prueba:** `test_cancel_or_plain_reject_discards_and_archives` (la entrada de `_PLAIN_NO`
  con `op("send_notification", "plausible", recipient="Carlos")`, con `A1` y con `H1`).

**D71 · Tarea 12: cambio de intención, rechazo y cancelación en el turno** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** con un registro en `collecting` o `awaiting_confirmation`, `_pending_gate`
  (`core/turn.py`) resuelve todo lo que no es `confirm`, en este orden: *Otra tool* (D3.1, D64,
  D65), *Misma tool*, *Solo `plausible`* (D68), `reject` con argumentos (D69), `reject` sin
  argumentos o `cancel` (D66, D70) y `question`/`other` (D67). Decide por `act`, `certainty` y el
  nombre de las tools pendientes, nunca por el texto. Los detalles no cambian lo que ve el usuario,
  cuándo se ejecuta una tool ni qué se guarda más allá de D64–D70, así que van en una línea cada
  uno (regla del autor desde la tarea 07):
  - *Otra tool:* `_rules_table` corre sobre todas las operaciones del turno, contra nada guardado;
    R2 escribe el registro nuevo con otro `operation_id` y R3–R5 no crean nada, así que `pending`
    queda vacío. Un `reject` o `cancel` que trae otra tool `explicit` es *Otra tool* y cierra
    `abandoned` (D3.1); `cancelled` es solo el descarte sin flujo nuevo.
  - *Rutas (decisión G):* `other_tool:<fila>` (`other_tool:R2` … `:R5`), `plausible_other_tool`,
    `reject_merged`, `cancelled` y `pending_template`; `pending_provisional` queda solo para un
    registro que no está en `collecting` ni `awaiting_confirmation` (`awaiting_clarification`,
    tarea 13), con `PENDING_NOT_SUPPORTED_YET`.
  - *Log:* sin claves nuevas. `op=` da el registro nuevo en *Otra tool* (o el abandonado si la
    tabla no creó ninguno) y el cancelado en `cancelled`; el `operation_id` del abandonado queda
    en `history`.
  - *`confirm_incomplete` (precisa D57):* un "sí" sobre un registro `collecting` pasa por
    `_repeat_pending`: reevalúa contra `now` y escribe el registro solo si su validez cambió.
  - *Línea de D10 (precisa D41):* precede a la respuesta en todas las ramas nuevas, como ya hacía
    `handle_turn`. La redacción "Parte de tu solicitud" no cambia.
  - *`confirm` con otra tool:* sigue en D45 (`_decide` lo resuelve antes de `_pending_gate`),
    nunca en *Otra tool*.
  - *D11:* va antes que todo; un `reject` o `cancel` con dos operaciones de una tool responde D11.
  - *AW 1, R3 y R4 tras un cambio de intención:* la línea del descarte se antepone aunque el turno
    vuelva a pedir la tool abandonada ("No voy a crear un ticket; …" y "Por ahora puedo atender
    una operación a la vez; ¿cuál necesitas primero?"); lectura literal de D64 y D3.1.
  - *AW 2:* `reject` con argumentos de la tool pendiente ignora sin línea una operación
    `plausible` de otra tool, como D68.
  - *AW 3:* `reject` con argumentos más una tool que el filtro descartó fusiona tras la línea de
    D10, como *Misma tool* (D41); D45 solo veta el `confirm`, porque un "sí" ejecuta y una
    corrección no.
  - *Tests reescritos:* `test_anything_with_pending_is_provisional_and_keeps_state` pasa a
    `test_pending_branches_are_no_longer_provisional` (`A1`→`Q1` es D67 y `F1`→`F2`, *Otra tool*),
    y `test_only_a_confirmed_yes_writes_history` (D49) a `test_only_a_closing_writes_history`
    (también `cancelled` y `other_tool:*` escriben `history`).
- **Lo que ve el usuario (casos F y G):** el caso F de punta a punta (TASK.md líneas 395-417): se
  dice el cambio, el ticket nace solo con su descripción, la reunión queda archivada y nada se
  ejecuta. Tras un cambio de intención, el "sí" corre solo la tool nueva (líneas 282-284 y
  436); tras un "no" o un "cancela", encuentra "No hay ninguna operación pendiente." (línea
  284).
- **Motivo:** RF-02 pide no invocar herramientas que no corresponden a la intención (TASK.md
  líneas 128-134); RF-07, mantener la intención actual y lo abandonado como estado estructurado
  (líneas 210-222 y 229); RF-10, que un "sí" nunca autorice otra tool (líneas 276-284); el
  objetivo 7 (línea 22) y las preguntas 2, 4 y 5 de §9 (líneas 554, 556 y 557).
- **Resuelve:** el *Pendiente* de D57 (`confirm_incomplete`), la parte de la 12 del *Alcance real*
  de D41 y el *Pendiente* de D47 (`reject` y `cancel`).
- **Lo prueban:** `test_case_f_other_tool_abandons_and_starts_the_new_flow`,
  `test_other_tool_while_awaiting_confirmation_runs_only_the_new_tool`,
  `test_reject_or_cancel_with_another_tool_is_a_change_of_intention` (`other_tool:R2`, `:R3` y
  `:R4`, también con la tool pendiente en el turno),
  `test_confirm_incomplete_reassesses_against_now`,
  `test_d10_line_before_every_new_pending_branch`, `test_clarification_stays_provisional` (`F2` y
  `cancel` sobre `awaiting_clarification`, delta vacío),
  `test_pending_branches_are_no_longer_provisional`, `test_only_a_closing_writes_history`,
  `test_case_f_through_the_node` y
  `test_cancel_through_the_node`; `test_no_turn_executes_a_tool` y
  `test_same_tool_needs_a_collecting_record` siguen sin cambios.
- **Pendiente:**
  - *Limitación para las tareas 19 y 20 (AW 1):* un cambio de intención que cae en R3 o R4 archiva
    lo pendiente y responde la línea del descarte y la plantilla provisional, sin crear nada: el
    usuario pierde lo recopilado hasta que la 13 y la 14a reemplacen esa plantilla (revisión de la
    tarea 12, ronda 1, nota 7, y ronda 3, nota 4).
  - *Herencia en Otra tool (tarea 14a):* "ninguna tool conserva argumentos guardados" (D3.1)
    no es observable hoy, porque R2 exige una sola `explicit` y R3/R4 no crean registro; la brief
    de la 14a debe pedir el test (un ticket pendiente con título y un turno con `create_ticket` y
    `send_notification` `explicit` → el paso del ticket del plan sin el título guardado; ronda 1,
    nota 4).
  - *Sin test:* AW 2 solo se vio en sondas de la revisión.
  - *Tamaño:* `core/turn.py` tiene 599 líneas, a una del límite de la brief (tarea 15).

**D72 · Numeración de `TASK.md`: una línea en blanco en lugar de la línea 4** (2026-11-17) ·
autodecidida por el orquestador; revisable

- **Decisión:** el commit del autor `681031a` quitó la línea 4 de `docs/TASK.md` (el enlace a
  `CLAUDE.md`, que no se versiona), y toda cita "TASK.md línea N" con N ≥ 4 quedó corrida en uno.
  El conciliador de la 12 pone una línea en blanco en su lugar: el archivo vuelve a tener 596
  líneas y desde la 5 cada una coincide con su número anterior. El enlace sigue fuera y nada más
  cambia.
- **Motivo:** el Markdown se ve igual y todas las citas de `docs/decisiones.md`, el código, los
  tests, el backlog y las briefs vuelven a valer sin tocar cientos de líneas. No cambia lo que ve
  el usuario, ni cuándo se ejecuta una tool, ni qué se guarda.
- **Descartada:** renumerar todas las citas.
