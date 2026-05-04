# Vulnerability Remediation Orchestrator

Event-driven vulnerability remediation using the Devin API.

## Status

Full pipeline implemented: polling -> dispatch -> status sync. Dashboard pending.

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

| Method | Path              | Description                                              |
|--------|-------------------|----------------------------------------------------------|
| GET    | `/health`         | Returns service and database status                      |
| POST   | `/poll`           | Manually trigger GitHub polling and return counts        |
| GET    | `/tasks`          | Returns all tasks as a JSON array                        |
| POST   | `/dispatch`       | Dispatch all pending tasks to Devin API sessions         |
| POST   | `/sync-statuses`  | Poll Devin API for running task statuses and update DB   |

## End-to-end demo

```bash
# 1. Configure environment
cp .env.example .env
# Set GITHUB_TOKEN, GITHUB_REPO, DEVIN_API_KEY, DEVIN_API_BASE_URL in .env

# 2. Start the orchestrator
docker compose up --build

# 3. Poll GitHub for vulnerability issues
curl -X POST http://localhost:8000/poll

# 4. Dispatch pending tasks to Devin
curl -X POST http://localhost:8000/dispatch

# 5. Wait a few minutes for Devin sessions to progress

# 6. Sync session statuses
curl -X POST http://localhost:8000/sync-statuses

# 7. View all tasks
curl http://localhost:8000/tasks | jq
```
