# Vulnerability Remediation Orchestrator

Event-driven vulnerability remediation using the Devin API.

## Status

Polling implemented. Devin dispatch and dashboard pending.

## Quick start

```bash
cp .env.example .env
# edit .env with real values
docker compose up --build
curl http://localhost:8000/health
```

## Local development

1. Set `GITHUB_TOKEN` in `.env`
2. `docker compose up --build`
3. `curl -X POST http://localhost:8000/poll`
4. `curl http://localhost:8000/tasks | jq`

## Endpoints

| Method | Path      | Description                                      |
|--------|-----------|--------------------------------------------------|
| GET    | `/health` | Returns service and database status              |
| POST   | `/poll`   | Manually trigger GitHub polling and return counts |
| GET    | `/tasks`  | Returns all tasks as a JSON array                |
