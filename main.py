import os
import sqlite3
import datetime
import contextlib
import asyncio
import logging

from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv
from github import Github

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "lahari-gangineni/superset")
DEVIN_API_KEY = os.getenv("DEVIN_API_KEY", "")
DEVIN_API_BASE_URL = os.getenv("DEVIN_API_BASE_URL", "https://api.devin.ai/v1")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
DB_PATH = os.getenv("DB_PATH", "tasks.db")

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_number INTEGER UNIQUE NOT NULL,
                issue_title TEXT NOT NULL,
                issue_url TEXT NOT NULL,
                devin_session_id TEXT,
                devin_session_url TEXT,
                pr_url TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_message TEXT
            )
            """
        )
        conn.commit()


# ---------------------------------------------------------------------------
# GitHub polling helpers
# ---------------------------------------------------------------------------
def fetch_open_vulnerability_issues():
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(GITHUB_REPO)
    issues = repo.get_issues(state="open", labels=["vulnerability"])
    result = []
    for issue in issues:
        result.append(
            {
                "number": issue.number,
                "title": issue.title,
                "html_url": issue.html_url,
                "body": issue.body or "",
            }
        )
    logger.info("Fetched %d open vulnerability issues from %s", len(result), GITHUB_REPO)
    return result


def sync_issues_to_db(issues):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_count = 0
    with get_db() as conn:
        for issue in issues:
            cursor = conn.execute(
                "SELECT 1 FROM tasks WHERE issue_number = ?", (issue["number"],)
            )
            if cursor.fetchone() is None:
                conn.execute(
                    """
                    INSERT INTO tasks (issue_number, issue_title, issue_url, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', ?, ?)
                    """,
                    (issue["number"], issue["title"], issue["html_url"], now, now),
                )
                new_count += 1
        conn.commit()
    logger.info("Synced issues: %d new, %d already known", new_count, len(issues) - new_count)
    return new_count


async def polling_loop():
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            issues = fetch_open_vulnerability_issues()
            new_count = sync_issues_to_db(issues)
            logger.info("Polling cycle complete: %d new tasks", new_count)
        except Exception:
            logger.exception("Error during polling cycle")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(polling_loop())
    yield
    task.cancel()


app = FastAPI(title="Vulnerability Remediation Orchestrator", lifespan=lifespan)


@app.get("/health")
def health():
    db_status = "ok"
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
    except Exception:
        db_status = "error"
    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "db": db_status,
    }


@app.post("/poll")
def poll():
    issues = fetch_open_vulnerability_issues()
    new_count = sync_issues_to_db(issues)
    return {"new_tasks": new_count, "total_open_issues": len(issues)}


@app.get("/tasks")
def list_tasks():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
