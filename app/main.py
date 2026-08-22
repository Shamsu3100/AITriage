import os
import sqlite3
from contextlib import contextmanager

from dotenv import load_dotenv
load_dotenv()  # must run before app.ai reads os.getenv

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import ai, budget

DB = os.getenv("DB_PATH", "readings.db")
app = FastAPI(title="Nexus AI Triage")


@contextmanager
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT CURRENT_TIMESTAMP,
                sensor TEXT, value REAL, unit TEXT, normal_range TEXT,
                severity TEXT,              -- from code: instant, always correct
                reason TEXT, action TEXT,   -- from the model: arrives later
                ai_status TEXT              -- pending | done
            )
        """)


init_db()


class Reading(BaseModel):
    sensor: str
    value: float
    unit: str = ""
    normal_range: str = "unspecified"


def _fill_in_wording(reading_id: int, sensor, value, unit, low, high, severity):
    """Runs AFTER the HTTP response has already been sent.
    Nobody is waiting on this. If it takes 26 seconds, nobody notices."""
    reason, action = ai.explain(sensor, value, unit, low, high, severity)
    with db() as conn:
        conn.execute(
            "UPDATE readings SET reason=?, action=?, ai_status='done' WHERE id=?",
            (reason, action, reading_id),
        )


@app.post("/ingest")
def ingest(r: Reading, background: BackgroundTasks):
    """Returns in ~2ms. The AI wording is filled in afterwards."""
    severity, low, high = ai.severity_of(r.value, r.normal_range)
    if severity is None:
        raise HTTPException(400, "normal_range must look like '20-60 C'")

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO readings (sensor, value, unit, normal_range, severity, ai_status)"
            " VALUES (?,?,?,?,?, 'pending')",
            (r.sensor, r.value, r.unit, r.normal_range, severity),
        )
        reading_id = cur.lastrowid

    # queued, not awaited
    background.add_task(_fill_in_wording, reading_id, r.sensor, r.value,
                        r.unit, low, high, severity)

    return {"id": reading_id, "severity": severity, "ai_status": "pending"}


@app.get("/readings/{reading_id}")
def one_reading(reading_id: int):
    """The browser polls this until ai_status flips to 'done'."""
    with db() as conn:
        row = conn.execute("SELECT * FROM readings WHERE id=?", (reading_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "no such reading")
    return dict(row)


@app.get("/readings")
def readings(limit: int = 20):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/health")
def health():
    """Hosting platforms ping this to know the container is alive.

    The web app always splits the work: severity from code, wording from a
    model. So the only thing that varies is which model writes the wording.
    """
    return {
        "status": "ok",
        "severity_source": "code",
        "wording_source": os.getenv("HYBRID_BACKEND", "ollama"),
    }


@app.delete("/readings")
def clear_readings():
    """Wipe the table. Handy between demos."""
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM readings").fetchone()["c"]
        conn.execute("DELETE FROM readings")
    return {"deleted": n}


@app.get("/queue")
def queue():
    """How many readings are still waiting on the model?"""
    with db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM readings WHERE ai_status='pending'").fetchone()["c"]
    return {"pending": n}


@app.get("/usage")
def usage():
    """What have I spent today? Check this before demo day."""
    return budget.status()


app.mount("/", StaticFiles(directory="static", html=True), name="static")
