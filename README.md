# Vulnerability Remediation Orchestrator

Event-driven vulnerability remediation using the Devin API.

## Status

Skeleton — health endpoint only. Polling, Devin dispatch, and dashboard added in subsequent commits.

## Quick start

```bash
cp .env.example .env
# edit .env with real values
docker compose up --build
curl http://localhost:8000/health
```

## Endpoints

| Method | Path      | Description                          |
|--------|-----------|--------------------------------------|
| GET    | `/health` | Returns service and database status  |
