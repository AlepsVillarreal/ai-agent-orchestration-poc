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

## Tests

```bash
uv run pytest
```
