# Deploying Reel Fact-Checker

One container serves the website and the API on one port. Nothing here requires a particular host;
any service that runs a Docker image with a persistent volume and HTTPS works (for example a small
VPS behind Caddy or nginx, or a managed container platform). Choosing and paying for a host is a
separate decision.

## Build

```sh
docker build -t reel-fact-checker .                               # statements and article links
docker build -t reel-fact-checker --build-arg WITH_VIDEO=true .   # adds ffmpeg and local Whisper
```

The build never includes `.env` files, local data, traces or `node_modules` (see `.dockerignore`).
Tag each release (for example `reel-fact-checker:2026-09-30`) so you can roll back.

## Run

```sh
docker run -d --name reel-fact-checker -p 8000:8000 \
  -v reel-data:/data \
  --env-file production.env \
  reel-fact-checker
```

`production.env` lives only on the server, never in Git:

| Setting | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY`, `TAVILY_API_KEY` | yes | Provider keys. Also set spending limits in both provider accounts. |
| `OPENAI_MODEL` | yes | `gpt-4.1-mini` (the only model with built-in pricing for the spending cap). |
| `ENABLE_LIVE_RESEARCH` | yes | `true` to allow paid research. |
| `APP_PASSWORD` | yes | Access password. Production refuses live research without it. |
| `SESSION_SECRET` | yes | Random value of 32+ characters, e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it signs everyone out. |
| `DAILY_BUDGET_USD`, `DAILY_SEARCH_LIMIT` | no | Daily caps (defaults 0.50 and 40). |
| `REPORTS_PER_HOUR` | no | Per-person report limit (default 10). |
| `TRUST_PROXY_HEADERS` | behind a proxy | `true` only when a trusted reverse proxy sets `X-Forwarded-For`; otherwise every visitor shares the proxy's address. |
| `WHISPER_MODEL` | video only | Whisper size (default `small`); downloads once into `/data/models`. |

Set by the image, normally unchanged: `ENVIRONMENT=production`, `DATA_DIR=/data`, `FRONTEND_DIST=/app/frontend`,
`COOKIE_SECURE=true`, `PORT=8000`.

## Railway

1. railway.com → New Project → Deploy from GitHub repo → `blessy-kambham/reel-fact-checker`, branch `main`. Railway finds the `Dockerfile`.
2. Service → Settings → Networking → Generate Domain (HTTPS is automatic). When asked for the port, use the one in the deploy log line `Uvicorn running on http://0.0.0.0:<port>`; Railway sets it to 8080.
3. Right-click the service → Attach Volume, mount path `/data` (1 GB is plenty without video).
4. Service → Variables: the required settings from the table above, plus
   - `RAILWAY_RUN_UID=0` (Railway volumes are root-owned; without it saving reports fails),
   - `TRUST_PROXY_HEADERS=true` (Railway's proxy sets `X-Forwarded-For`),
   - optional `WITH_VIDEO=true` to build with video (needs about 2 GB memory; roughly doubles cost).
5. Deploy, open the domain, sign in with `APP_PASSWORD`, and open `/config`: it should show `live_ready: true`. `setting_states` lists each setting as set, empty or absent (names only) and `started_at` shows when the running copy started, so you can tell whether a new deployment is live. Add variables on the service's own Variables tab; project-level Shared Variables do nothing until attached to the service.
6. Set a usage limit under Workspace → Usage, and spending limits in the OpenAI and Tavily accounts.

## HTTPS

Put the container behind a reverse proxy or platform that terminates HTTPS. Sign-in cookies are
`Secure`, so sign-in does not work over plain HTTP in production.

## Health and monitoring

- `GET /health` returns `{"status": "ok"}`; the image's `HEALTHCHECK` uses it.
- `GET /config` shows whether live research is ready, today's estimated spending (numbers only) and
  what is missing. It never returns keys or the password.
- Watch the provider dashboards for spend; the in-app cap is an estimate, not a billing guarantee.

## Data, backups and rollback

- Everything persistent is in the `/data` volume: `history.sqlite3` (saved reports), `usage-*.json`
  (daily spending ledgers) and, for video, `models/`.
- Back up: stop the container (or run a SQLite backup) and copy `history.sqlite3` somewhere safe.
- Roll back: run the previous image tag with the same volume and `production.env`.
- Delete a user's saved report from the page, or with `DELETE /history/{id}`.

## Limits of this setup

One process with in-memory rate limits and one report at a time: fine for a demo or a small group.
Several replicas would need shared rate limiting and a shared database. Uploaded videos are processed
in a temporary folder and deleted; they are never stored.
