# ai-agent-orchestration-poc

Agente conversacional construido con **Google ADK** que interpreta solicitudes en lenguaje natural, valida argumentos de forma determinística y orquesta la ejecución de herramientas (`create_ticket`, `schedule_meeting`, `send_notification`).

Arquitectura (componentes, estados de una operación, ciclo de un turno y qué decide el LLM frente al código): [`docs/arquitectura.md`](docs/arquitectura.md).
Documentación técnica (uso de ADK, memoria, idempotencia, observabilidad, preguntas de defensa, límites conocidos y decisiones clave): [`docs/documentacion-tecnica.md`](docs/documentacion-tecnica.md).

## Qué hace

El modelo (Gemini, por ADK) solo interpreta cada mensaje; el código valida los datos, pide lo que falta, confirma y ejecuta las herramientas simuladas ([arquitectura](docs/arquitectura.md)). Nada se ejecuta sin mostrar los valores y recibir un "sí" (D7.1). Los tests de `tests/test_cases.py` usan un modelo falso con interpretaciones fijas; las transcripciones de `examples/transcripts/`, el modelo real.

| Caso (TASK.md) | Qué hace el agente | Tests | Transcripción |
|---|---|---|---|
| A (líneas 292-308) | Anota la descripción, pide el título, la prioridad y a quién se asigna, y no ejecuta. | `test_case_a_end_to_end_through_the_node` | [`caso_a.txt`](examples/transcripts/caso_a.txt) |
| B (líneas 311-335) | Conserva lo dicho en cada mensaje, valida otra vez tras cada uno y crea el ticket solo con todo completo, después de mostrar los valores y recibir un "sí". | `test_case_b_through_the_node` | [`caso_b.txt`](examples/transcripts/caso_b.txt) |
| C (líneas 338-355) | Con las dos lecturas, pregunta "¿Quieres enviar una notificación, programar una reunión, o ambas?" y no ejecuta nada hasta que se elige una y se confirma. | `test_case_c_through_the_node` | [`caso_c.txt`](examples/transcripts/caso_c.txt) (difiere: ver [Diferencias](#diferencias-con-el-enunciado)) |
| D (líneas 357-371) | Responde "La fecha indicada no existe." y "La hora está fuera del formato válido.", pide corregirlas junto con lo que falta, valida otra vez la corrección y no ejecuta. | `test_case_d_through_the_node`, `test_case_d_correction_is_validated_again` | [`caso_d.txt`](examples/transcripts/caso_d.txt) |
| E (líneas 374-392) | Pide en una sola respuesta lo que falta de las dos herramientas, confirma una vez, crea el ticket y envía la notificación con el número del ticket al final del mensaje; si el ticket falla, no intenta la notificación (solo en los tests). | `test_case_e_through_the_node`, `test_case_e_first_step_fails_through_the_node` | [`caso_e.txt`](examples/transcripts/caso_e.txt) |
| F (líneas 395-417) | Al pedir el ticket con la reunión pendiente, responde "No voy a programar una reunión; descarté lo que tenía pendiente." y empieza el ticket sin heredar nada de la reunión. | `test_case_f_through_the_node` | [`caso_f.txt`](examples/transcripts/caso_f.txt) |
| G (líneas 420-438) | Muestra los valores y pide confirmación sin volver a pedir datos; con el "sí", envía solo la notificación pendiente, una vez. Un "sí" que nombra otra herramienta no ejecuta nada (solo en los tests). | `test_case_g_through_the_node`, `test_case_g_yes_naming_another_tool_runs_nothing` | [`caso_g.txt`](examples/transcripts/caso_g.txt) |
| H (líneas 441-451) | La misma solicitud repetida mientras espera el "sí" no cambia nada; repetida después de ejecutarse, el "sí" responde "Ya creé el ticket … con estos mismos datos; no lo repetí." sin ejecutar otra vez. Si no sabe si una ejecución terminó, no la reintenta (solo en los tests). | `test_case_h_repeated_while_pending`, `test_case_h_through_the_node`, `test_case_h_uncertain_execution_never_runs` | [`caso_h.txt`](examples/transcripts/caso_h.txt) |

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
- La prueba de humo está en `phase0/runs/`, con una nota por corrida: 0/23 fallos de forma, así que se mantiene la opción B (D32).
- La medición del caso C (dos rondas de 5 llamadas, con nota) también está ahí; su resultado, en [Diferencias con el enunciado](#diferencias-con-el-enunciado) (D52).
- Los umbrales de D2 se miden solo con esta prueba de humo: la corrida completa de 207 llamadas no se hace (F0.2).

## Tests

```bash
docker compose up -d redis   # algunos tests usan Redis
uv run pytest
```

Los tests no llaman al modelo real: usan un modelo falso con respuestas fijas. `tests/test_cases.py` tiene un grupo de tests por caso de TASK.md §4 (`test_case_a_…` a `test_case_h_…`), que pasan por el agente de ADK con ese modelo falso. Si Redis no responde, los tests que lo necesitan fallan con un mensaje que indica cómo levantarlo.

Los tests no necesitan `GOOGLE_API_KEY`: la quitan del entorno, así que ninguno puede llamar al modelo real. Los tests de la CLI borran, al terminar, toda sesión de la CLI que se creó en Redis mientras corrían, así que no conviene usar la CLI contra el mismo Redis mientras corre `uv run pytest`.

## Diferencias con el enunciado

Dónde lo que hace el agente difiere del texto de los casos de TASK.md §4. Los límites del diseño están en [Límites conocidos](docs/documentacion-tecnica.md#límites-conocidos), y cada transcripción, turno por turno, en [`examples/transcripts/README.md`](examples/transcripts/README.md).

- **B** (líneas 317-319 y 327-335; D17, D7.1): la primera pregunta pide también el título ("el título, la prioridad y a quién se asigna"), porque el agente no lo inventa (línea 184), y el ticket se crea tras mostrar los valores y recibir un "sí" que el enunciado no muestra; `test_case_b_through_the_node`, [`caso_b.txt`](examples/transcripts/caso_b.txt).
- **F** (línea 403; D17): tras "Agenda una reunión con Juan" la pregunta nombra el título, la fecha, la hora y la duración en minutos, no solo "¿Qué día y a qué hora?", porque el agente no inventa el título ni la duración (línea 184); `test_case_f_through_the_node`, [`caso_f.txt`](examples/transcripts/caso_f.txt).
- **D** (línea 198; D56): responde "La fecha indicada no existe." y "La hora está fuera del formato válido." sin la cabecera "Hay un problema con la fecha y la hora." ni la pregunta "¿Qué fecha y hora deseas utilizar?"; su pregunta nombra la fecha y la hora junto con los otros faltantes; `test_case_d_through_the_node`, [`caso_d.txt`](examples/transcripts/caso_d.txt).
- **C** (líneas 338-355; D52): con `gemini-3.5-flash-lite`, "Avísale a Carlos que mañana revisamos el servidor" llega solo como notificación, así que el agente pide el canal en vez de preguntar y nunca ofrece la reunión ([`caso_c.txt`](examples/transcripts/caso_c.txt)); en ese archivo, "Solo avísale" llegó además como canal y el validador lo rechazó. Con las dos lecturas el agente pregunta, como muestra `test_case_c_through_the_node` con interpretaciones fijas.
