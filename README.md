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

## Tests

```bash
uv run pytest
```
