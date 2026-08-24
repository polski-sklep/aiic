# Dependency audit

**Date:** 24 August 2026 · **Branch:** `agent/devops` · **Path claimed by this
branch** (it was unowned in `CONTRACTS.md` §1).

Reproduce with `make audit-all`. Enumerated with
`gh api /repos/polski-sklep/aiic/dependabot/alerts` (which worked; no fallback
was needed) plus `pip-audit` inside the container.

---

## Summary

| | Advisories |
|---|---|
| Reported by Dependabot | 9 (1 critical, 3 high, 2 moderate, 3 low) |
| Additionally found by `pip-audit` | 12 — all in **transitive** dependencies |
| **Total before this work** | **21** |
| Cleared | **14** |
| Accepted with reasoning | 7 |
| **Reachable from application code, before or after** | **0** |

Dependabot only parses `requirements.txt`, so it sees direct pins and nothing
else. There is no lockfile, so the 12 advisories in `starlette`, `pyasn1` and
`ecdsa` were invisible to it. That gap is the main finding of this audit: **the
9 alerts on the dashboard were not the 9 that mattered most.** The critical one
was in a package nothing imports, while `starlette` — the ASGI core underneath
every single request — carried seven unreported advisories.

---

## The nine Dependabot alerts

| # | Package | Severity | Advisory | Reachable? | Action |
|---|---|---|---|---|---|
| 2 | python-jose | **Critical** | CVE-2024-33663 — algorithm confusion with OpenSSH ECDSA keys | **No** | **Removed the package** |
| 1 | python-jose | Moderate | CVE-2024-33664 — DoS via compressed JWE content | **No** | **Removed the package** |
| 9 | python-multipart | **High** | CVE-2026-53539 — quadratic querystring parsing, CPU DoS | **No** | Bumped 0.0.20 → 0.0.31 |
| 5 | python-multipart | **High** | CVE-2026-42561 — DoS via unbounded multipart part headers | **No** | Bumped 0.0.20 → 0.0.31 |
| 3 | python-multipart | **High** | CVE-2026-24486 — arbitrary file write (non-default config) | **No** | Bumped 0.0.20 → 0.0.31 |
| 4 | python-multipart | Moderate | CVE-2026-40347 — DoS via large multipart preamble/epilogue | **No** | Bumped 0.0.20 → 0.0.31 |
| 7 | python-multipart | Low | CVE-2026-53538 — semicolon parameter smuggling | **No** | Bumped 0.0.20 → 0.0.31 |
| 6 | python-multipart | Low | CVE-2026-53537 — Content-Disposition RFC 2231 smuggling | **No** | Bumped 0.0.20 → 0.0.31 |
| 8 | python-multipart | Low | CVE-2026-53540 — negative Content-Length buffers whole body | **No** | Bumped 0.0.20 → 0.0.31 |

**All nine cleared.**

### Why `python-jose` was removed rather than bumped

Removing it is *more* conservative than upgrading it, because it changes no
behaviour at all:

- `grep -rn "jose"` over the entire repo returns two hits: the pin itself, and
  a line in an old blueprint document. **Nothing imports it.**
- `settings.jwt_secret`, `settings.google_client_id` and
  `settings.google_client_secret` exist in `app/config.py` and are read by
  **zero** call sites. There is no auth layer — security review SEC-03 confirms
  no endpoint is authenticated.
- It was the sole reason `ecdsa` and `pyasn1` were installed, which is five
  further advisories.

Bumping to 3.4.0 would have cleared 2 advisories; deleting the line cleared 7.
If auth is ever built, re-add at `>=3.4.0`, or prefer `pyjwt`.

### Why `python-multipart` was bumped despite being unreachable

FastAPI imports it lazily, only for `Form`/`File`/`UploadFile` parameters. There
are none — `grep` for `multipart|UploadFile|File(|Form(|request.form` across
`backend/app/` returns nothing. But it is a leaf dependency with no API surface
this project touches, so the upgrade is close to free. Bumped.

---

## The twelve `pip-audit` found

| Package | Severity | Advisory | Reachable? | Action |
|---|---|---|---|---|
| pyasn1 0.4.8 | High (A:H) | CVE-2026-30922 — recursion DoS on nested SEQUENCE/SET | **No** | Gone with python-jose |
| pyasn1 0.4.8 | High (A:H) | CVE-2026-59886 — `univ.Real` exponent CPU blowup | **No** | Gone with python-jose |
| pyasn1 0.4.8 | High (A:H) | CVE-2026-59885 — quadratic OID decoding | **No** | Gone with python-jose |
| pyasn1 0.4.8 | High (A:H) | CVE-2026-59884 — unbounded long-form tag ids | **No** | Gone with python-jose |
| ecdsa 0.19.2 | High (C:H/I:H) | CVE-2024-23342 — Minerva timing attack on P-256 | **No** | Gone with python-jose. **No fix exists** — upstream considers side-channel resistance out of scope. Would have been unfixable while the package remained. |
| starlette 0.41.3 | — | CVE-2026-48710 — Host header not validated, poisons `request.url` | **No** — see below | **Accepted** |
| starlette 0.41.3 | A:H | CVE-2026-54283 — `request.form()` ignores limits for urlencoded | **No** | **Accepted** |
| starlette 0.41.3 | I:L | CVE-2026-54282 — unvalidated path moves the authority boundary in `request.url` | **No** | **Accepted** |
| starlette 0.41.3 | A:H | CVE-2025-62727 — O(n²) Range-header merging in `FileResponse` | **No, but see the warning** | **Accepted** |
| starlette 0.41.3 | A:L | CVE-2025-54121 — blocking spool-to-disk on large multipart uploads | **No** | **Accepted** |
| starlette 0.41.3 | C:H | CVE-2026-48818 — `StaticFiles` UNC SSRF **on Windows** | **No** | **Accepted** |
| starlette 0.41.3 | I:L | CVE-2026-48817 — `HTTPEndpoint` dispatches via unrestricted `getattr` | **No** | **Accepted** |

---

## The seven accepted `starlette` advisories

### Why they cannot simply be patched

`fastapi==0.115.6` pins `starlette<0.42.0`, so 0.41.3 is the newest resolvable
version. Clearing them requires bumping FastAPI, and the fix versions are
spread out:

| Needed starlette | Minimum FastAPI that allows it | Clears |
|---|---|---|
| 0.47.2 | 0.116.2 (`<0.49.0`) | 1 of 7 |
| 0.49.1 | 0.120.4 (`<0.50.0`) | 2 of 7 |
| 1.1.0 | 0.128.8 (`<1.0.0`) — still not enough | 4 of 7 |
| **1.3.1** | **0.133.1** (`starlette>=0.40.0`, unbounded) | **7 of 7** |

FastAPI is currently at 0.141.1. Going from 0.115.6 to 0.133.1 is a **20-minor
jump** on the runtime that produced six weeks of stable behaviour, and 0.128
onward also raises the `pydantic` floor and reorganises the `standard` extras.
Buying one unreachable advisory per two minors is a bad trade; buying all seven
in one jump is a real change that deserves its own commit, its own smoke test
and a rollback plan. **Deferred, not ignored** — see the recommendation below.

### Reachability, per advisory

Verified by grepping `backend/app/` for every trigger:

```
StaticFiles      0 hits
FileResponse     0 hits
request.url      0 hits
request.form     0 hits
HTTPEndpoint     0 hits
mount(           0 hits
```

- **CVE-2026-54283, CVE-2025-54121** — require `request.form()`. Never called.
  No endpoint takes `Form`, `File` or `UploadFile`.
- **CVE-2025-62727, CVE-2026-48818** — require `FileResponse` or `StaticFiles`.
  Neither is mounted. 48818 additionally only affects Windows; production is
  Ubuntu in a Linux container.
- **CVE-2026-48817** — requires an `HTTPEndpoint` subclass on a `Route` with no
  explicit `methods=`. The app uses FastAPI decorators exclusively.
- **CVE-2026-54282, CVE-2026-48710** — poison `request.url` / `request.url.path`.
  Nothing reads `request.url`, and the impact both describe is bypassing
  path-based security checks, of which there are none (SEC-03: no endpoint is
  authenticated). Latent, not exploitable for gain today.

⚠️ **This analysis expires the moment `agent/ui-report` mounts `StaticFiles`.**
That branch owns `backend/app/static/**`. Serving any file through
`StaticFiles` or `FileResponse` makes **CVE-2025-62727** (unauthenticated
CPU-exhaustion via a crafted `Range` header) immediately reachable. The
`fastapi` bump must land before or with that change.

### How the acceptance is enforced

The seven ids are an explicit ledger in the `Makefile` (`AUDIT_IGNORE`), which
CI's blocking `pip-audit` job uses. **Anything new fails the build.** The
advisory CI job runs `pip-audit` with no exemptions so the accepted set stays
visible rather than forgotten.

---

## Not changed, with reasons

| Package | Pinned | Latest | Why not bumped |
|---|---|---|---|
| `anthropic` | 0.42.0 | — | No advisory. This is the SDK every agent call goes through, and there is deliberately no provider fallback (CONTRACTS.md §4.3), so a regression here is total. `temperature` is already excluded from Claude kwargs (§4.4) and model ids resolve from settings; a bump risks re-opening both. Bump only with a live smoke evaluation. |
| `sqlalchemy` | 2.0.36 | — | No advisory. `agent/persistence` is actively changing the models and the migration runner on top of this exact version. |
| `fastapi` | 0.115.6 | 0.141.1 | See above. |
| `openai` | 1.58.1 | — | No advisory. Note this path is **no longer dormant** — `semantic_search_notes` is registered and in every agent's tool list, so the embeddings call is live. That raises the *value* of keeping it current, but there is nothing to fix today. |
| `alembic` | 1.14.1 | — | No advisory. Pinned but never configured; `agent/persistence` evaluated and rejected it in favour of a forward-only SQL runner (`backend/migrations/README.md`). **Recommend removing the pin** — it is dead weight — but that is their call, not mine. |

---

## Recommendations, in priority order

1. **Bump `fastapi` to ≥0.133 and let `starlette` resolve to ≥1.3.1**, as a
   single dedicated change with its own smoke test. Clears the last 7. **Do
   this before `agent/ui-report` mounts `StaticFiles`**, which turns
   CVE-2025-62727 from unreachable into an unauthenticated DoS.
2. **Add a lockfile** (`pip-compile` / `uv lock`). The entire transitive layer
   is currently unpinned and invisible to Dependabot; that is why 12 of 21
   advisories were undetected. This is the structural fix.
3. **Enable Dependabot for GitHub Actions** as well as pip, now that
   `.github/workflows/` exists.
4. **Remove the `alembic` pin** (owner: `agent/persistence`).
5. Rotate `ANTHROPIC` / `OPENAI` / `BRAVE` / `NOTION` keys — handoff §8.1,
   still open, unrelated to this audit but adjacent.

---

## Verification

```
$ docker run --rm -v "$PWD":/src -w /src aiic-dev \
    pip-audit -r backend/requirements.txt --progress-spinner off
Found 7 known vulnerabilities in 1 package
Name      Version ID              Fix Versions
--------- ------- --------------- ------------
starlette 0.41.3  PYSEC-2026-161  1.0.1
starlette 0.41.3  PYSEC-2026-249  1.3.1
starlette 0.41.3  PYSEC-2026-248  1.3.0
starlette 0.41.3  PYSEC-2026-1942 0.49.1
starlette 0.41.3  PYSEC-2026-1941 0.47.2
starlette 0.41.3  PYSEC-2026-2281 1.1.0
starlette 0.41.3  PYSEC-2026-2280 1.1.0

$ make audit
No known vulnerabilities found, 7 ignored

$ docker run --rm aiic-backend python3 -c "import app.main; print('IMPORT OK')"
IMPORT OK

$ docker run --rm -v "$PWD/backend":/app -w /app aiic-backend \
    python3 -m unittest discover -s tests -t .
Ran 74 tests in 0.180s
OK
```

Before this work `pip-audit` reported **21 vulnerabilities in 4 packages**.
