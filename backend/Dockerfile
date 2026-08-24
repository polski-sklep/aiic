# Multi-stage. Stage order matters: `runtime` is LAST, so a bare
# `docker build` / `docker compose build backend` produces the production
# image and never the dev one. CI asks for the dev image explicitly with
# `--target dev`.

FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]


# --- dev: adds lint / type / test / audit tooling. CI and `make` only. ---
FROM base AS dev
RUN pip install --no-cache-dir -r requirements-dev.txt


# --- runtime: the production image. Keep this stage last. ---
FROM base AS runtime
