# Multi-stage. Stage order matters: `runtime` is LAST, so a bare
# `docker build` / `docker compose build backend` produces the production
# image and never the dev one. CI asks for the dev image explicitly with
# `--target dev`, and docker-compose.yml names `target: runtime` so a future
# reorder cannot ship dev tooling to production by accident.

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
# Stays root: CI mounts the repo at an arbitrary uid and only ever reads it.
FROM base AS dev
RUN pip install --no-cache-dir -r requirements-dev.txt


# --- runtime: the production image. Keep this stage last. ---
FROM base AS runtime

# SEC-06 (Low). The container ran as root with `./backend/app` bind-mounted
# read-write, so an in-container RCE could rewrite application source, which
# WatchFiles would then hot-reload straight into the running process. A
# non-root uid cannot write those host-owned files.
#
# uid 10001 is arbitrary but fixed: it must not collide with a real host user.
# The app writes nothing under /app (verified — no open(...,"w"), write_text,
# mkdir or shutil call exists in backend/app), so read-only access is enough.
# Python simply skips writing __pycache__ when it cannot.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser
USER 10001
