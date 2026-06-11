# Security

## Reporting a vulnerability

If you find a security issue, please open a private security advisory on
GitHub or contact the repository owner directly. Do not file a public issue
for vulnerabilities.

## Secrets posture

This project loads every credential from environment variables (a local
`.env` file in development; the deployment environment in production). No
secret is ever hardcoded.

`.env` is in `.gitignore` and must never be committed. If you accidentally
commit a secret:

1. Rotate the credential immediately at the issuing service.
2. Force-push a rewrite that removes the secret from history (or, easier and
   safer, accept that the secret is now public and only rely on rotation).
3. Audit access logs at the issuing service for the period the secret was
   exposed.

## What this project never sends anywhere

- The user's raw Telegram messages are sent only to the configured LLM
  provider (Anthropic in this codebase) for parsing, and only when the user
  explicitly forwards content to the bot.
- No telemetry, no analytics, no third-party trackers. The only outbound
  network traffic is to the APIs documented in the README.

## Single-user posture

These projects are designed for single-user, self-hosted use. The
authorisation model is "the configured Telegram user ID is the only allowed
caller" (or, for the webapp, "Tailscale ACL is the only gate"). Public
deployment without re-engineering the auth layer is not supported and is
explicitly out of scope.
