import os
import sqlite3
import datetime
import contextlib
import asyncio
import logging
import json

from fastapi import FastAPI
import uvicorn
import requests as http_requests
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
DEVIN_API_BASE_URL = os.getenv("DEVIN_API_BASE_URL", "https://api.devinenterprise.com/v1")
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


# ---------------------------------------------------------------------------
# Devin API helpers
# ---------------------------------------------------------------------------
def build_devin_prompt(task: dict) -> str:
    return (
        f"You are working on the repository {GITHUB_REPO}. A security scanner "
        f"has flagged a vulnerability that needs remediation.\n"
        f"\n"
        f"## Finding (from GitHub issue #{task['issue_number']})\n"
        f"Title: {task['issue_title']}\n"
        f"URL: {task['issue_url']}\n"
        f"\n"
        f"Read the full issue body at the URL above. Investigate the flagged "
        f"code, determine the appropriate fix, and open a pull request against "
        f"the main branch of this repository.\n"
        f"\n"
        f"Requirements:\n"
        f"1. Make the minimal, focused change addressing the root cause.\n"
        f"2. Run any relevant tests; ensure no regressions.\n"
        f"3. PR title must reference the rule ID and issue #{task['issue_number']}.\n"
        f"4. PR description must explain the root cause, your fix, and tradeoffs. "
        f"Reference issue #{task['issue_number']}.\n"
        f"5. If the finding is a false positive or requires architectural changes "
        f"beyond a single PR, document that in the PR and propose an "
        f"incremental approach instead of attempting a partial fix.\n"
        f"\n"
        f"Keep changes minimal and focused on this single finding."
    )


def create_devin_session(task: dict) -> bool:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    prompt = build_devin_prompt(task)
    headers = {
        "Authorization": f"Bearer {DEVIN_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = http_requests.post(
            f"{DEVIN_API_BASE_URL}/sessions",
            headers=headers,
            json={"prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        session_id = data.get("session_id", "")
        session_url = data.get("url", "")
        logger.info(
            "Created Devin session %s for issue #%s: %s",
            session_id, task["issue_number"], session_url,
        )
        with get_db() as conn:
            conn.execute(
                """UPDATE tasks
                   SET status='running', devin_session_id=?, devin_session_url=?, updated_at=?
                   WHERE id=?""",
                (session_id, session_url, now, task["id"]),
            )
            conn.commit()
        return True
    except http_requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        body = (exc.response.text[:500] if exc.response is not None else str(exc))
        logger.error(
            "HTTP error creating Devin session for issue #%s: %s %s",
            task["issue_number"], status_code, body,
        )
        error_msg = f"auth error" if status_code in (401, 403) else body[:500]
        with get_db() as conn:
            conn.execute(
                """UPDATE tasks SET status='failed', error_message=?, updated_at=? WHERE id=?""",
                (error_msg, now, task["id"]),
            )
            conn.commit()
        return False
    except Exception as exc:
        logger.exception("Unexpected error creating Devin session for issue #%s", task["issue_number"])
        with get_db() as conn:
            conn.execute(
                """UPDATE tasks SET status='failed', error_message=?, updated_at=? WHERE id=?""",
                (str(exc)[:500], now, task["id"]),
            )
            conn.commit()
        return False


def poll_devin_session(task: dict) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    headers = {"Authorization": f"Bearer {DEVIN_API_KEY}"}
    try:
        resp = http_requests.get(
            f"{DEVIN_API_BASE_URL}/sessions/{task['devin_session_id']}",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except http_requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        body = (exc.response.text[:500] if exc.response is not None else str(exc))
        logger.error(
            "HTTP error polling session %s: %s %s",
            task["devin_session_id"], status_code, body,
        )
        if status_code in (401, 403):
            with get_db() as conn:
                conn.execute(
                    """UPDATE tasks SET status='failed', error_message='auth error', updated_at=? WHERE id=?""",
                    (now, task["id"]),
                )
                conn.commit()
            return "failed"
        return task["status"]
    except Exception:
        logger.exception("Unexpected error polling session %s", task["devin_session_id"])
        return task["status"]

    status_enum = data.get("status_enum", "")
    pull_request = data.get("pull_request")

    if status_enum in ("running", "working"):
        new_status = "running"
    elif status_enum == "blocked":
        new_status = "completed" if pull_request else "needs_review"
    elif status_enum in ("finished", "completed"):
        new_status = "completed"
    elif status_enum in ("expired", "failed"):
        new_status = "failed"
    else:
        logger.warning("Unknown status_enum '%s' for session %s", status_enum, task["devin_session_id"])
        new_status = "running"

    pr_url = None
    if pull_request:
        logger.info(
            "Pull request detected for session %s: %s",
            task["devin_session_id"], json.dumps(pull_request)[:500],
        )
        pr_url = pull_request.get("url") or pull_request.get("html_url") or pull_request.get("pr_url")

    # Validate response shape
    if "status_enum" not in data:
        logger.warning(
            "Unexpected response shape for session %s: fields=%s, raw=%s",
            task["devin_session_id"], list(data.keys()), json.dumps(data)[:500],
        )
        error_msg = f"unexpected response shape: {list(data.keys())}"
        with get_db() as conn:
            conn.execute(
                """UPDATE tasks SET error_message=?, updated_at=? WHERE id=?""",
                (error_msg[:500], now, task["id"]),
            )
            conn.commit()
        return task["status"]

    error_message = None if new_status != "failed" else task.get("error_message")
    with get_db() as conn:
        conn.execute(
            """UPDATE tasks
               SET status=?, pr_url=COALESCE(?, pr_url), error_message=?, updated_at=?
               WHERE id=?""",
            (new_status, pr_url, error_message, now, task["id"]),
        )
        conn.commit()

    logger.info(
        "Session %s: status_enum=%s -> %s (pr_url=%s)",
        task["devin_session_id"], status_enum, new_status, pr_url,
    )
    return new_status


async def polling_loop():
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            issues = fetch_open_vulnerability_issues()
            new_count = sync_issues_to_db(issues)
            logger.info("Polling cycle complete: %d new tasks", new_count)
        except Exception:
            logger.exception("Error during GitHub polling phase")

        # Auto-dispatch pending tasks
        try:
            with get_db() as conn:
                rows = conn.execute("SELECT * FROM tasks WHERE status='pending'").fetchall()
            pending = [dict(r) for r in rows]
            for t in pending:
                create_devin_session(t)
            if pending:
                logger.info("Auto-dispatched %d pending tasks", len(pending))
        except Exception:
            logger.exception("Error during auto-dispatch phase")

        # Sync statuses for running tasks
        try:
            with get_db() as conn:
                rows = conn.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
            running = [dict(r) for r in rows]
            for t in running:
                poll_devin_session(t)
            if running:
                logger.info("Synced statuses for %d running tasks", len(running))
        except Exception:
            logger.exception("Error during status sync phase")


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


@app.post("/dispatch")
def dispatch():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status='pending'").fetchall()
    pending = [dict(r) for r in rows]
    dispatched = 0
    errors = 0
    for t in pending:
        if create_devin_session(t):
            dispatched += 1
        else:
            errors += 1
    return {"dispatched": dispatched, "errors": errors}


@app.post("/sync-statuses")
def sync_statuses():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
    running_tasks = [dict(r) for r in rows]
    updated = 0
    still_running = 0
    completed = 0
    failed = 0
    needs_review = 0
    for t in running_tasks:
        new_status = poll_devin_session(t)
        if new_status == "running":
            still_running += 1
        elif new_status == "completed":
            completed += 1
            updated += 1
        elif new_status == "failed":
            failed += 1
            updated += 1
        elif new_status == "needs_review":
            needs_review += 1
            updated += 1
        else:
            still_running += 1
    return {
        "updated": updated,
        "still_running": still_running,
        "completed": completed,
        "failed": failed,
        "needs_review": needs_review,
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
