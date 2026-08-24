# Operations guide

Everything needed to run, develop, test and deploy the AIIC committee
orchestrator. Owner: `agent/devops`.

The README is owned by the orchestrator; this file is the source it draws from.

---

## 1. Prerequisites

| | Why |
|---|---|
| **Docker Desktop** (or Docker Engine) with Compose v2 | The only supported runtime. Every command below runs in a container. |
| **make** | Entry points. Preinstalled on macOS and Ubuntu. |
| **git** | Deploy is `git pull`. |
| `gitleaks` (optional, local) | `make secrets`. `brew install gitleaks`. |

**Do not create a host virtualenv.** The maintainer's Mac runs Python
**3.14.5**, which is too new for the pinned dependencies — they will not install,
and if they did, validating against them would prove nothing about production.
The container runs **3.12**, and per `CONTRACTS.md` §5 it is the only
trustworthy runtime for import validation. There is deliberately no `make venv`.

On macOS the Docker CLI may not be on `PATH`:

```bash
export PATH="$PATH:/Applications/Docker.app/Contents/Resources/bin"
```

---

## 2. Install and first run

```bash
git clone git@github.com:polski-sklep/aiic.git
cd aiic
cp .env.example .env
$EDITOR .env            # fill in ANTHROPIC_API_KEY and POSTGRES_PASSWORD
make up
```

`make up` builds the image, starts Postgres and Redis, waits for both to report
healthy, then starts the backend. Check it:

```bash
curl -s http://localhost:8100/health
make logs
```

`make` on its own lists every target.

---

## 3. Environment variables

`.env.example` is the reference and is annotated per variable — which are
required, which gate a specific capability, and where to obtain each key. Names
are fixed by `backend/app/config.py::Settings` (`CONTRACTS.md` §3.5). Adding a
setting means adding it to **both**. Never read `os.environ` directly; go
through `get_settings()`.

Minimum to boot: `ANTHROPIC_API_KEY` and `POSTGRES_PASSWORD`.

`.env` is gitignored and must never be committed. Verify with the definitive
tool, not `git status` (handoff §14.5):

```bash
git check-ignore -v .env
git diff --cached --name-only     # before every commit
```

### ⚠️ Trap 1 — `.env` changes need `--force-recreate`

```bash
docker compose up -d --force-recreate backend      # or: make recreate
```

`docker compose restart backend` **reuses the environment baked into the
container at creation time.** Your edit will appear to have had no effect, and
you will look for the bug in the wrong place. Handoff §9.3: this cost two full
sessions before it was understood.

### ⚠️ Trap 2 — `POSTGRES_PASSWORD` cannot be rotated in `.env` alone

The value is baked into the Postgres data directory when the volume is **first
initialised** (`CONTRACTS.md` §4.5). Changing `.env` on an existing volume does
not rotate anything — Postgres simply rejects the new password and the backend
fails to connect. To rotate for real:

```bash
docker compose exec postgres psql -U committee -d committee \
  -c "ALTER USER committee WITH PASSWORD 'new-value-here';"
# then update .env, then:
docker compose up -d --force-recreate backend
```

In that order. Take a backup first (§6).

---

## 4. Local development

Python changes hot-reload: `./backend/app` is bind-mounted and uvicorn runs
with WatchFiles. **Compose changes do not** — they need `docker compose up -d`.
Changes to `backend/requirements.txt` or the `Dockerfile` need a rebuild
(`make up` rebuilds).

| Target | Does |
|---|---|
| `make up` | Build and start the stack |
| `make down` | Stop containers; **volumes and data survive** |
| `make logs` | Tail the backend (`make logs SERVICE=postgres` for another) |
| `make shell` | Shell inside the running backend |
| `make recreate` | The `--force-recreate` that `.env` changes require |
| `make ps` | Container status |
| `make clean` | Stop **and destroy the volumes** |

### Running several stacks at once

Container names and published ports are `${VAR:-default}` expressions whose
defaults reproduce production exactly. To run a second stack — or to work on a
machine that already has Postgres on 5432 — set them in `.env`:

```bash
CONTAINER_PREFIX=aiic-mybranch
POSTGRES_HOST_PORT=55436
REDIS_HOST_PORT=56383
BACKEND_HOST_PORT=58104
```

and pass a distinct project name: `docker compose -p aiic-mybranch up -d`, or
`make up PROJECT=aiic-mybranch`.

This replaces the private `docker-compose.override.yml` that everyone was using
to dodge collisions. That file stays gitignored and is still supported for
anything the variables do not cover — **never commit one.**

### ⚠️ Trap 3 — the backend container is deliberately minimal

`committee-backend` has **no `ps`, no `pkill`, no `gh`, no `gitleaks`**, and
since the SEC-06 fix it runs as **uid 10001, not root**.

```bash
docker compose restart backend                       # to kill an in-container process
docker compose exec backend ls /proc                 # to enumerate processes
docker compose exec backend cat /proc/1/cmdline      # what pid 1 is
```

Run `gitleaks`, `gh` and anything else from the **host**, not the container.

---

## 5. Database

Postgres is `pgvector/pgvector:pg16`. Redis is `redis:7-alpine`.

### ⚠️ Trap 4 — the volumes are named and live outside the repo

```
committee-orchestrator_pgdata      committee-orchestrator_redisdata
```

They are **named Docker volumes, external to the repository directory**. That
property is why re-cloning the repo did not destroy the database (handoff §7.2).
**Preserve it.** Never convert `pgdata` to a bind mount, and never assume
`rm -rf` on the repo is destructive to data — or that it is safe.

`docker compose down` keeps them. `docker compose down -v` destroys them.

### ⚠️ Trap 5 — `init.sql` only runs on an empty data directory

`backend/init.sql` is mounted into `/docker-entrypoint-initdb.d/`. Postgres runs
that directory **only when initialising an empty volume**. On the live volume it
has never run and never will (handoff §9.4). `calibration_records` had to be
created by hand for exactly this reason.

So: **`init.sql` is a fresh-volume fast path, not the schema's source of truth.**
Every schema change must ship as *both* an `init.sql` edit *and* a numbered
migration, in the same commit (`CONTRACTS.md` §3.3).

### Migrations

Forward-only, ordered, idempotent SQL in `backend/migrations/`, applied by a
runner in `backend/app/database.py`. Owned by `agent/persistence`; full design
notes in `backend/migrations/README.md`.

```bash
make migrate          # docker compose exec backend python -m app.database
```

Output looks like `applied=['0002'] skipped=['0001'] errors=[]`; exit status 0
when OK. Re-running is a no-op. Editing an already-applied migration is
detected by checksum and refused — ship a new one instead.

`backend/migrations` is bind-mounted read-only into the container, so a
migration added by `git pull` is delivered without a rebuild. **This was a real
deploy bug**: only `backend/app` used to be mounted, so a new migration was
baked in at image-build time and a plain `git pull` — which `CONTRACTS.md` §4.7
says is the entire deploy — silently failed to deliver it.

### Other database targets

```bash
make db-shell     # psql into the running database
make db-backup    # gzipped pg_dump into the working directory
make db-reset     # DESTROY the local volume and re-init from init.sql (prompts)
```

---

## 6. Backups

Take one before any schema or credential work.

```bash
make db-backup
# VPS equivalent:
# ssh root@100.95.239.105 "docker compose -f /opt/committee-orchestrator/docker-compose.yml \
#   exec -T postgres pg_dump -U committee committee | gzip > /root/aiic-db-backup-$(date +%Y%m%d).sql.gz"
```

A pre-existing dump is at `~/aiic-backups/aiic-db-backup-20260824.sql.gz` (Mac)
and `/root/aiic-db-backup-20260824.sql.gz` (VPS).

---

## 7. Tests

74 tests, written against **stdlib `unittest`**. They run in the *production*
image with nothing from `requirements-dev.txt` installed:

```bash
make test        # stdlib unittest in the production image — the canonical runner
make test-v      # same, names every test
make test-pytest # optional richer runner; pytest collects the same cases
```

That is deliberate. A CI runner nobody can reproduce locally is worse than no
CI, so the blocking suite needs only Docker.

No test may touch CoinGecko or Postgres; HTTP is patched and the backfill uses
an in-memory repository double.

---

## 8. Lint, types and security

```bash
make lint       # ruff
make fmt        # ruff autofix + format
make typecheck  # mypy
make audit      # pip-audit against the accepted-vulnerability ledger
make audit-all  # ... with no exemptions
make secrets    # gitleaks over history and the working tree
make check      # everything CI runs
```

`ruff` and `mypy` are configured in `pyproject.toml` as a **ratchet**: the
current state is pinned as the floor with an explicit, itemised debt ledger, so
the build is green today and the first red build is a real regression. Each
ledger entry names the finding and its owning branch. Delete entries as they are
fixed; never add one back.

Accepted dependency advisories are listed in the `Makefile` (`AUDIT_IGNORE`) and
justified per-CVE in [`docs/reviews/dependency-audit.md`](reviews/dependency-audit.md).

---

## 9. CI

`.github/workflows/ci.yml` and `.github/workflows/security.yml`. Six jobs:
`compose`, `image`, `static`, `advisory`, `secrets`, `dependencies`.

Every step runs a `make` target or a command the Makefile also runs, so CI is
reproducible locally by typing the same thing.

### ⚠️ Trap 6 — validate with the instrument that consumes the artifact

`docker-compose.yml` was broken in two places for weeks because it was
"validated" with `compileall`, which never reads YAML (handoff §14.1). Two
commits, `bdad5cd` and `8432cf4`, fixed indentation on `POSTGRES_PASSWORD` and
`DATABASE_URL`.

```bash
make compose-check        # docker compose config -q --no-interpolate
make compose-check-full   # with interpolation, against your real .env
make import-check         # import app.main in the container's 3.12
```

`--no-interpolate` is the right default: it validates YAML and schema without
needing a `POSTGRES_PASSWORD` or an `.env` to exist, so CI never invents a fake
secret. It still catches the historical defect — reintroducing the `bdad5cd`
indentation makes it print `yaml: line 8: mapping values are not allowed in
this context`.

And remember §14.4: **compile ≠ import ≠ runs.** A module can compile and still
`ImportError`. That is what `make import-check` is for, and it must run in the
container's 3.12.

---

## 10. Production build and deployment

**Deploy is `git pull --ff-only` on the VPS. There is no other path**
(`CONTRACTS.md` §4.7).

```
edit on the Mac (~/Projects/aiic) → commit → push to GitHub → git pull on the VPS
```

```bash
ssh root@100.95.239.105
cd /opt/committee-orchestrator
git pull --ff-only
docker compose up -d                # only if docker-compose.yml changed
docker compose up -d --build backend  # only if requirements.txt or Dockerfile changed
docker compose exec backend python -m app.database   # if a migration was added
```

`./backend/app` and `./backend/migrations` are bind-mounted, so Python and
migration files land with the pull. Everything else needs a rebuild.

The image is multi-stage; `runtime` is the last stage and `docker-compose.yml`
names `target: runtime` explicitly, so the dev image (which carries ruff, mypy,
pytest and pip-audit) can never be shipped to production by accident.

### Persona-only fast path

```bash
sync-committee   # rsync, MARKDOWN ONLY, into backend/app/memory/
```

**Never sync Python this way.** An earlier unscoped rsync overwrote
`agent_personas.py` and `__init__.py` on the live box. It was harmless that
time (handoff §7.3).

### Preflight for any VPS session

```bash
tailscale ping 100.95.239.105
ssh root@100.95.239.105 "echo REACHABLE && docker compose -f /opt/committee-orchestrator/docker-compose.yml ps"
```

Known access failures (handoff §9.1):

- **NordVPN over Tailscale blackholes SSH.** `tailscale ping` returns pong (it
  rides DERP relays) but `ssh` times out. A full-tunnel commercial VPN hijacks
  the default route. **Quit NordVPN.** This recurs.
- **The Ubuntu MOTD swallows commands chained onto a fresh `ssh`.** Run one
  block at a time, or wrap in `ssh host "..."`.
- **Wrong-machine commands are the single largest time sink.**
  `/opt/committee-orchestrator` exists only on the VPS; `~/Projects/aiic` only
  on the Mac. `zsh: no matches found` means you are on the Mac (zsh) when you
  meant to be on Ubuntu (bash). An empty grep result can look exactly like a
  passing check.

---

## 11. Network posture — ACTION REQUIRED FROM JACOB

Security review **SEC-02 (High)**. The brief assumed a host firewall. There is
none:

- `ufw status` → **`inactive`**; `/etc/ufw/ufw.conf` → `ENABLED=no`.
- `iptables -S INPUT` → policy **ACCEPT**, containing only a jump to `ts-input`.
- `nft list ruleset` → INPUT chain **policy accept**.
- **No host rule drops 5432, 6379 or 8100.**

Public ports are currently filtered, so this is not an active breach — but the
filtering happens *upstream of the VM*, i.e. an external Hetzner Cloud Firewall.
That single external control is the **entire** defence, with no host-level
fallback. If it is deleted, edited, or the server is rebuilt without it, an
unauthenticated Postgres, a passwordless Redis and a no-auth API become
internet-reachable immediately.

**Already fixed in this repo:** `docker-compose.yml` now binds Postgres and
Redis to `127.0.0.1`. Nothing outside the compose network needs them — the
backend reaches them by service name — so they leave the exposed set entirely.
This takes effect on the next `docker compose up -d`.

**Still to do, on the host — these are Jacob's to run. They are listed here,
not executed, because they change the live server.** Review each before running;
`ufw enable` can drop your SSH session if rule 1 is skipped.

```bash
# 1. FIRST — never lock yourself out. Allow SSH over Tailscale.
ufw allow in on tailscale0
ufw allow 22/tcp

# 2. Default deny inbound.
ufw default deny incoming
ufw default allow outgoing

# 3. Docker publishes ports by writing to the DOCKER chain, which BYPASSES ufw
#    entirely. ufw rules alone will NOT protect a published container port.
#    Add an explicit DOCKER-USER rule, which Docker consults first:
iptables -I DOCKER-USER -i eth0 -p tcp --dport 5432 -j DROP
iptables -I DOCKER-USER -i eth0 -p tcp --dport 6379 -j DROP
#    Persist it (otherwise it is lost on reboot):
apt-get install -y iptables-persistent && netfilter-persistent save

# 4. Enable. Confirm you still have a second SSH session open first.
ufw enable
ufw status verbose
```

Optionally, narrow the API to the Tailscale address instead of `0.0.0.0` by
setting this in the VPS `.env`, then `docker compose up -d`:

```
BACKEND_BIND_ADDR=100.95.239.105
```

Also outstanding on the host (handoff §9.6, SEC-09): 29 pending apt updates, 3
ESM security updates, `*** System restart required ***`, and a dead Tailscale
node `tailscale-vps @ 100.85.27.99` offline 111+ days that should be deleted
from the admin console.

---

## 12. Secrets

`.env` lives only on the VPS at `/opt/committee-orchestrator/.env`, with a
backup at `~/aiic-vps-env.backup` on the Mac.

`gitleaks` runs in CI on every push and is configured in `.gitleaks.toml`.
**Verified against gitleaks 8.30.1: the stock ruleset does not detect Anthropic
`sk-ant-…`, Brave `BSA…` or CoinGecko `CG-…` keys** — precisely the credentials
this project holds. Explicit rules were added. Do not delete them assuming
`useDefault` covers them; it was tested and it does not.

CI's blocking scans are the **pushed commit range** and the **working tree**.
The full-history scan is advisory, because history contains findings that were
fixed and can never become clean (`.env.example` shipped a weak default password
from the first commit until it was corrected).

Handoff §8.1: `.env` was once tracked in the VPS repo's *local* git history at
commit `52d6fc1`. That history had no remote and was destroyed with
`/opt/committee-orchestrator.old`; GitHub was verified clean. But keys have
appeared in pasted terminal output and screenshots across sessions, so
**rotation of `ANTHROPIC`, `OPENAI`, `BRAVE` and `NOTION` remains a standing,
never-done item.**

---

## 13. Quick reference

| Situation | Command |
|---|---|
| First run | `cp .env.example .env` → edit → `make up` |
| Changed `.env` | `make recreate` — **not** `restart` |
| Changed Python under `app/` | nothing; it hot-reloads |
| Changed `docker-compose.yml` | `docker compose up -d` |
| Changed `requirements.txt` or `Dockerfile` | `make up` (rebuilds) |
| Added a migration | commit it, `git pull` on the VPS, `make migrate` |
| Added a schema change | edit `init.sql` **and** add a migration, same commit |
| Port 5432 already in use | set `POSTGRES_HOST_PORT` in `.env` |
| Something is wrong in the container | `make logs`, `make shell` |
| Before every commit | `git diff --cached --name-only` |
