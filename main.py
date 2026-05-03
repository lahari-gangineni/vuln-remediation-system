import os
import sqlite3
import datetime
import contextlib

from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv

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
# FastAPI application
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


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


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
