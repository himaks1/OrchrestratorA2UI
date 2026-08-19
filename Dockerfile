FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.4.15 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1
ENV LITELLM_MODEL="vertex_ai/gemini-2.5-flash"
ENV VERTEX_PROJECT="ssrg-agents"
ENV VERTEX_LOCATION="us-central1"

WORKDIR /app
COPY . /app

RUN uv sync --no-cache

CMD ["./start.sh"]
