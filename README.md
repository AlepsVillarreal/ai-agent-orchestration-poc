# ai-agent-orchestration-poc

Agente conversacional construido con **Google ADK** que interpreta solicitudes en lenguaje natural, valida argumentos de forma determinística y orquesta la ejecución de herramientas (`create_ticket`, `schedule_meeting`, `send_notification`).

Arquitectura (componentes, estados de una operación, ciclo de un turno y qué decide el LLM frente al código): [`docs/arquitectura.md`](docs/arquitectura.md).

> 🚧 En construcción. Este README se ampliará con la arquitectura, decisiones técnicas y ejemplos de interacción.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestiona Python 3.12 y las dependencias)
- Docker (para Redis)
- Una API key de Gemini ([Google AI Studio](https://aistudio.google.com/apikey))

## Configuración

```bash
# 1. Instalar dependencias (crea .venv con Python 3.12)
uv sync

# 2. Variables de entorno
cp .env.example .env
# editar .env y colocar GOOGLE_API_KEY

# 3. Levantar Redis
docker compose up -d redis
```

`uv sync` lee `.python-version` y `uv.lock`: descarga Python 3.12 si no está instalado y crea el entorno virtual en `.venv/` (ignorado por git). No es necesario activarlo; usa `uv run <comando>` para ejecutar dentro del entorno.

### Verificar el entorno

```bash
# Redis debe aparecer como "healthy"
docker compose ps

# Debe responder PONG
docker compose exec redis redis-cli ping

# Debe imprimir la versión de google-adk
uv run python -c "import google.adk; print(google.adk.__version__)"
```

Para detener Redis: `docker compose down` (los datos se conservan en el volumen `redis-data`; usa `docker compose down -v` para borrarlos).

## Ejecutar el agente (CLI)

Requiere Redis levantado (`docker compose up -d redis`) y `GOOGLE_API_KEY` en `.env`. Cada mensaje llama al modelo de Gemini y consume cuota de la API key.

```bash
# Conversación interactiva; cada línea es un mensaje. Ctrl+D para salir.
uv run python -m assistant.cli

# Continuar una conversación guardada en Redis
uv run python -m assistant.cli --session <ID>

# Mensajes por tubería: cada línea es un mensaje y la salida se lee como transcripción
printf 'Necesito crear un ticket\n' | uv run python -m assistant.cli

# Los mensajes de un caso de prueba (examples/inputs/caso_a.txt … caso_h.txt)
uv run python -m assistant.cli < examples/inputs/caso_b.txt
```

- `examples/inputs/` tiene un archivo por caso de TASK.md §4, con un mensaje por línea (sin comentarios ni líneas en blanco: la CLI manda toda línea no vacía). Son los mismos mensajes que `tests/test_cases.py` pasa con el modelo falso; con el modelo real y la fecha del día, las respuestas pueden diferir de las de los tests. `examples/transcripts/` tiene lo que respondió el agente a cada archivo con `gemini-3.5-flash-lite` (stdout sin editar, una sesión nueva por archivo), y su `README.md` dice cuántas llamadas se hicieron y en qué difiere cada transcripción de los tests. Se grabaron con una línea cada 13 s; por tubería las líneas salen seguidas y la capa gratuita puede responder un 429 a mitad de un archivo.
- Sin `--session`, cada ejecución abre una sesión nueva; su id y el comando para continuarla se imprimen en stderr.
- stdout lleva solo la conversación. Con stdin por tubería, cada línea del usuario se repite como `> …` antes de la respuesta.
- stderr lleva el aviso de sesión, el aviso de `MOCK_FAIL` si está activo, una línea de log por turno (`assistant.turn`, en formato `clave=valor`) y las trazas de ADK cuando falla el modelo. Un turno que ejecuta una herramienta suma `results=herramienta:ID:resultado` (`ran`, `reused`, `failed` o `uncertain`; en un plan, uno por paso intentado, en orden), `idem=` con la clave de idempotencia de cada paso y, si la herramienta lanzó una excepción, `tool_error=` con su tipo. Un turno que arma un plan de varias herramientas suma, tras `route=`, `depends=` con sus dependencias (`send_notification<create_ticket`, con `:inferred` si la dedujo el registro y `:ignored` si se descartó), vacío si no tiene ninguna.
- Al arrancar se comprueba Redis (`REDIS_URL`, por defecto `redis://localhost:6379/0`), después `GOOGLE_API_KEY` y después `MOCK_FAIL`; si algo falla, la CLI sale con código 1 y un mensaje que dice qué hacer.
- Si el modelo falla (cuota, timeout, red) o su respuesta no se puede leer, el agente responde con una plantilla y no cambia el estado de la conversación.
- Las claves de idempotencia viven en el mismo Redis que las sesiones (`idem:<hash>`, 24 h); `docker compose down -v` las borra junto con las sesiones.

### Simular un fallo de herramienta (`MOCK_FAIL`)

```bash
# create_ticket responde con un fallo (success: false) en esta ejecución; varios nombres van separados por coma
MOCK_FAIL=create_ticket uv run python -m assistant.cli
```

- Las herramientas nombradas responden `success: false` en ese proceso; las demás funcionan igual. La CLI lo avisa en stderr.
- Tras el "sí", el agente responde "No pude crear un ticket: la herramienta respondió con un error. Si me lo pides de nuevo, lo intento otra vez." y no guarda la clave de idempotencia, así que repetir la solicitud (sin `MOCK_FAIL`) la ejecuta de nuevo.
- Con `MOCK_FAIL=send_notification`, el "sí" al plan del caso E crea el ticket y responde "Listo: creé el ticket TCK-…." y "No pude enviar una notificación: la herramienta respondió con un error. …". Para reintentar, sal y vuelve a abrir la misma sesión sin `MOCK_FAIL` (`--session <ID>`) y repite la solicitud: el agente reutiliza el ticket ("Ya creé el ticket TCK-… con estos mismos datos; no lo repetí.") y envía la notificación con ese número. Con `MOCK_FAIL=create_ticket`, la notificación no se intenta ("No intenté enviar una notificación porque un paso anterior no se completó.").
- Un nombre que no es una herramienta (`MOCK_FAIL=pizza`) sale con código 1 y la lista de nombres válidos.
- Solo simula un fallo, no un timeout: el estado incierto (una ejecución que no se sabe si terminó) se prueba en los tests.

> **Estado actual:** el agente reconoce la solicitud, pide los datos que faltan (caso A) y conserva y completa los que llegan en mensajes siguientes (caso B), mostrando qué anotó o cambió. Valida cada dato en código tras cada mensaje (presencia, tipo, valores permitidos, la duración de 1 a 480 minutos, y fechas y horas: que existan, que no hayan pasado y que sus partes coincidan) y, si uno es inválido, dice cuál y pide corregirlo (caso D); una fecha sin mes o una hora sin la hora se vuelven a pedir, nunca se completan. "Hoy" es la fecha y hora local de la máquina. Con todos los datos válidos muestra los valores que va a usar y pide confirmación; solo un "sí" que no cambia ni agrega nada ejecuta la herramienta simulada, una vez, y responde qué hizo con el ID que devolvió (por ejemplo, "Listo: envié la notificación NTF-…"). Así los casos B y G terminan de punta a punta; el caso B termina con un "sí" que TASK.md no muestra (D7). Cada operación cerrada queda en el estado de la sesión (`history`, las últimas 10, con sus valores, su resultado y el desenlace de cada paso; una cancelada o abandonada, con los datos que tenía), que ninguna regla ni el modelo leen. Cada ejecución lleva una clave de idempotencia (sesión + herramienta + valores) guardada en Redis antes de llamar a la herramienta (caso H): repetir una solicitud mientras está pendiente no cambia nada y repite la pregunta o la confirmación; repetirla después de ejecutarse vuelve a pedir confirmación, y el "sí" responde "Ya creé el ticket … con estos mismos datos; no lo repetí." sin ejecutar otra vez; un "sí" reenviado tras un turno que no se guardó tampoco ejecuta. Si no se puede saber si una ejecución terminó (una excepción, un timeout o una respuesta que no se puede leer), responde "No pude confirmar si llegué a …; no lo voy a reintentar para no duplicarlo." y esa misma ejecución no se repite durante 24 h. Si la herramienta responde con un fallo, lo dice y repetir la solicitud la vuelve a intentar (ver `MOCK_FAIL`). Con algo pendiente, pedir otra herramienta (caso F) descarta lo pendiente sin ejecutarlo, lo dice ("No voy a programar una reunión; descarté lo que tenía pendiente.") e inicia el flujo nuevo sin mezclar datos, así que un "sí" posterior solo ejecuta la herramienta nueva; un "no" o "cancela" descarta lo pendiente con esa misma línea, y un "sí" posterior responde "No hay ninguna operación pendiente."; si además pide otra herramienta ("Cancela la reunión y crea un ticket para revisar el servidor"), solo inicia la nueva; un "no" con datos de lo pendiente ("No, asígnalo a Ana") es una corrección que vuelve a pedir el "sí"; una pregunta responde qué puede hacer y repite lo pendiente sin cambiarlo. Si el modelo reporta una lectura segura y otra posible (o solo posibles), el agente no inicia nada y pregunta cuál quiere ("¿Quieres enviar una notificación, programar una reunión, o ambas?", caso C), guardando lo ya dicho para cada opción; si elige una, inicia ese flujo con esos datos (los muestra) y pide lo que falte o la confirmación, sin ejecutar en ese turno; un "no" o "cancela" descarta la pregunta ("No inicié ninguna acción. Dime qué necesitas cuando quieras."), pedir otra herramienta la descarta como en el caso F, y cualquier otra respuesta repite la pregunta. Con el modelo real, el mensaje del caso C llega solo como notificación, así que el agente pide el canal en vez de preguntar (D52). Una solicitud con varias operaciones (caso E) forma un plan: anota lo dicho para cada herramienta, pide en una sola respuesta lo que falta de todas, ordena los pasos por la dependencia que reportó el modelo o, si no reportó ninguna, por la que declara el registro (la notificación va después del ticket), y muestra una sola confirmación que dice "El mensaje llevará al final el número de ticket."; elegir "ambas" en el caso C forma el plan del mismo modo. Con el "sí", el plan se ejecuta paso a paso en ese orden: crea el ticket, envía la notificación con el número del ticket al final de su mensaje ("ya se registró la falla (ticket TCK-…)") y responde una línea por operación ("Listo: creé el ticket TCK-…." y "Listo: envié la notificación NTF-…."). Cada paso lleva su propia clave de idempotencia; la de la notificación incluye el número de su ticket, así que otro ticket con el mismo aviso envía otro aviso. Si un paso no se completa, los siguientes no se ejecutan y la respuesta lo dice ("No intenté enviar una notificación porque un paso anterior no se completó."); si falla la notificación, el ticket queda creado, y repetir la solicitud reutiliza el ticket ("Ya creé el ticket … con estos mismos datos; no lo repetí.") y vuelve a intentar solo la notificación (ver `MOCK_FAIL`). Un plan ya ejecutado y pedido otra vez no ejecuta nada.

## Prueba de humo del intérprete (fase 0)

Manda al intérprete, solo, los 23 mensajes de la fase 0 con el estado pendiente que vería y cuenta las salidas que no cumplen el esquema (`docs/decisiones.md`, D2, F0 y F0.1). No pasa por el núcleo ni ejecuta herramientas. Se corre desde la raíz del repositorio.

```bash
# Lista los 23 escenarios; no llama al modelo, no lee .env y no crea archivos
uv run python -m phase0.run

# 23 llamadas al modelo real, una por escenario; el cliente reintenta un 429 o un 503
uv run python -m phase0.run --run

# Reanuda una corrida detenida: copia lo que ese JSONL ya guardó y llama solo a lo que falta
uv run python -m phase0.run --run --resume phase0/runs/<fecha>-<modelo>.jsonl

# Solo esos escenarios (aquí, 5 llamadas); sin --run, solo los lista
uv run python -m phase0.run --run --only C1,N1,S3,A1,E1

# Juzga un JSONL guardado con las reglas del núcleo (C1, N1, S3, A1, E1); no llama al modelo
uv run python -m tests.case_c_judge phase0/runs/<fecha>-<modelo>.jsonl
```

- `--run` necesita `GOOGLE_API_KEY` en `.env` (no necesita Redis) y consume hasta 23 llamadas de la cuota gratuita, con 13 s de espera entre llamadas reales (la página de límites no publica los de la capa gratuita para el modelo; 13 s cabe en 5 por minuto).
- El arnés reintenta con su propia configuración (D28 y D30): ante un 429 o un 503, el cliente de google-genai repite la llamada hasta 3 intentos, con espera exponencial de 2 a 30 s; también reintenta sus errores de red transitorios. Cada reintento deja una línea en stderr (logger `google_genai._api_client`). El intérprete del agente (CLI) reintenta un 503 una sola vez, con 1–2 s de espera (y, como el arnés, un fallo de conexión); un 429 no se reintenta y el nodo del turno no reintenta (D1.2).
- La salida cruda queda en `phase0/runs/<fecha>-<modelo>.jsonl`, una línea por escenario. El informe en stdout da los fallos de forma y el veredicto de F0.1.
- Si el modelo sigue fallando tras los reintentos (o hay un timeout o falta la clave), la corrida se detiene en ese escenario, sale con código 1 y no da veredicto.
- `--resume` (solo junto con `--run`) escribe un JSONL nuevo de 23 líneas: copia sin cambios las líneas que ya tenían salida y sin error, y llama solo a los escenarios que faltan; el archivo anterior no se modifica (D29). Si ese archivo no es de este arnés o no coincide con los escenarios actuales, sale con código 2 sin llamar al modelo ni crear archivos.
- `--only ID,…` corre o lista solo esos escenarios, en el orden de siempre; no se combina con `--resume`, y un id desconocido o repetido sale con código 2 sin llamar al modelo. Con menos de 23 escenarios el informe da los fallos de forma, pero no el veredicto de F0.1 (D53).
- La prueba de humo de la tarea 06 está en `phase0/runs/`, con una nota por corrida: 0/23 fallos de forma, así que se mantiene la opción B (D32).
- La medición del caso C de la tarea 10c (dos rondas de 5 llamadas, con nota) también está ahí: con `gemini-3.5-flash-lite`, "Avísale a Carlos que mañana revisamos el servidor" se lee solo como notificación, así que el agente pide el canal en vez de preguntar si también se quiere una reunión; es una limitación documentada (D52).
- Los umbrales de D2 se miden solo con esta prueba de humo: la corrida completa de 207 llamadas no se hace (F0.2).

## Tests

```bash
docker compose up -d redis   # algunos tests usan Redis
uv run pytest
```

Los tests no llaman al modelo real: usan un modelo falso con respuestas fijas. `tests/test_cases.py` tiene un grupo de tests por caso de TASK.md §4 (`test_case_a_…` a `test_case_h_…`), que pasan por el agente de ADK con ese modelo falso. Si Redis no responde, los tests que lo necesitan fallan con un mensaje que indica cómo levantarlo.
