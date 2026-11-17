# ai-agent-orchestration-poc

Agente conversacional construido con **Google ADK** que interpreta solicitudes en lenguaje natural, valida argumentos de forma determinística y orquesta la ejecución de herramientas (`create_ticket`, `schedule_meeting`, `send_notification`).

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
```

- Sin `--session`, cada ejecución abre una sesión nueva; su id y el comando para continuarla se imprimen en stderr.
- stdout lleva solo la conversación. Con stdin por tubería, cada línea del usuario se repite como `> …` antes de la respuesta.
- stderr lleva el aviso de sesión, una línea de log por turno (`assistant.turn`, en formato `clave=valor`) y las trazas de ADK cuando falla el modelo.
- Al arrancar se comprueba Redis (`REDIS_URL`, por defecto `redis://localhost:6379/0`) y después `GOOGLE_API_KEY`; si algo falla, la CLI sale con código 1 y un mensaje que dice qué hacer.
- Si el modelo falla (cuota, timeout, red) o su respuesta no se puede leer, el agente responde con una plantilla y no cambia el estado de la conversación.

> **Estado actual:** el agente reconoce la solicitud y pide los datos que faltan (caso A). Todavía no valida, no confirma ni ejecuta herramientas: una solicitud con todos los datos, la continuación de una operación pendiente o una solicitud con varias operaciones o ambigua responden con mensajes provisionales.

## Prueba de humo del intérprete (fase 0)

Manda al intérprete, solo, los 23 mensajes de la fase 0 con el estado pendiente que vería y cuenta las salidas que no cumplen el esquema (`docs/decisiones.md`, D2, F0 y F0.1). No pasa por el núcleo ni ejecuta herramientas. Se corre desde la raíz del repositorio.

```bash
# Lista los 23 escenarios; no llama al modelo, no lee .env y no crea archivos
uv run python -m phase0.run

# 23 llamadas al modelo real, una por escenario, sin reintentos
uv run python -m phase0.run --run
```

- `--run` necesita `GOOGLE_API_KEY` en `.env` (no necesita Redis) y consume 23 llamadas de la cuota gratuita, con 6 s de espera entre ellas.
- La salida cruda queda en `phase0/runs/<fecha>-<modelo>.jsonl`, una línea por escenario. El informe en stdout da los fallos de forma y el veredicto de F0.1.
- Si el modelo falla (cuota, timeout, falta la clave), la corrida se detiene en ese escenario, sale con código 1 y no da veredicto.
- Los umbrales de D2 se miden solo con esta prueba de humo: la corrida completa de 207 llamadas no se hace (F0.2).

## Tests

```bash
docker compose up -d redis   # algunos tests usan Redis
uv run pytest
```

Los tests no llaman al modelo real: usan un modelo falso con respuestas fijas. Si Redis no responde, los tests que lo necesitan fallan con un mensaje que indica cómo levantarlo.
