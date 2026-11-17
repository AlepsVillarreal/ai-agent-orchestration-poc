# Decisiones de diseño

Registro de las decisiones de arquitectura del PoC, en el orden en que se tomaron. Las
referencias a `TASK.md` y sus números de línea apuntan al enunciado de la prueba técnica,
copiado literal en [`TASK.md`](TASK.md).

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
  - *Sin `retry_config`* en el nodo del turno ni en el intérprete. Un `RetryConfig()` con los
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
  valores normalizados que muestra la confirmación (D7).
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

- **Decisión:** si tras el filtro del registro un turno trae dos operaciones de la misma tool
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
  ejecuta ni cambia nada, y vuelve a mostrar la confirmación pendiente. En un plan,
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
  `awaiting_confirmation`.
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
    tarea 08 suma los cambios de fusión con sus valores (D4 regla 8).

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
