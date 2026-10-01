"""SQLite storage for watch sessions (one row per relevant watching session)."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import config

MIN_WATCHED_SEC = 60  # shorter sessions are noise and get deleted
FINISHED_RATIO = 0.95

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id             INTEGER PRIMARY KEY,
  slug           TEXT NOT NULL,
  episode        INTEGER NOT NULL,
  started_at     TEXT NOT NULL,
  ended_at       TEXT NOT NULL,
  start_pos_sec  INTEGER NOT NULL DEFAULT 0,
  end_pos_sec    INTEGER NOT NULL DEFAULT 0,
  watched_sec    INTEGER NOT NULL DEFAULT 0,
  length_sec     INTEGER,
  finished       INTEGER NOT NULL DEFAULT 0,
  source         TEXT NOT NULL DEFAULT 'play'
);
CREATE INDEX IF NOT EXISTS sessions_slug_ep ON sessions (slug, episode);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions (started_at);
"""


def db_path() -> Path:
    env = os.environ.get("ANIME_DB")
    return Path(env) if env else config.home() / "data" / "anime.db"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect(path: Path | None = None):
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def start_session(con, slug: str, episode: int, start_pos_sec: int = 0) -> int:
    t = now()
    cur = con.execute(
        "INSERT INTO sessions (slug, episode, started_at, ended_at, start_pos_sec, end_pos_sec) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (slug, episode, t, t, start_pos_sec, start_pos_sec),
    )
    con.commit()
    return cur.lastrowid


def update_session(con, session_id: int, position_sec: int, length_sec: int | None, add_watched_sec: int) -> None:
    con.execute(
        "UPDATE sessions SET ended_at = ?, end_pos_sec = ?, length_sec = COALESCE(?, length_sec), "
        "watched_sec = watched_sec + ? WHERE id = ?",
        (now(), position_sec, length_sec, add_watched_sec, session_id),
    )
    con.commit()


def end_session(con, session_id: int) -> bool:
    """Closes a session: deletes it if too short, else flags it finished. Returns whether it was kept."""
    row = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return False
    if row["watched_sec"] < MIN_WATCHED_SEC:
        con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        con.commit()
        return False
    finished = int(bool(row["length_sec"]) and row["end_pos_sec"] >= FINISHED_RATIO * row["length_sec"])
    con.execute("UPDATE sessions SET finished = ?, ended_at = ? WHERE id = ?", (finished, now(), session_id))
    con.commit()
    return True


def last_played(con, slug: str):
    """Most recent session, finished or not (what 'continue' should offer)."""
    return con.execute(
        "SELECT * FROM sessions WHERE slug = ? ORDER BY ended_at DESC, id DESC LIMIT 1", (slug,)
    ).fetchone()


def highest_finished(con, slug: str) -> int:
    row = con.execute("SELECT MAX(episode) AS m FROM sessions WHERE slug = ? AND finished = 1", (slug,)).fetchone()
    return row["m"] or 0


def resume_position(con, slug: str, episode: int) -> int:
    """Where the latest session of this episode ended, 0 if it was finished or never played."""
    row = con.execute(
        "SELECT end_pos_sec, finished FROM sessions WHERE slug = ? AND episode = ? ORDER BY ended_at DESC, id DESC LIMIT 1",
        (slug, episode),
    ).fetchone()
    return 0 if row is None or row["finished"] else row["end_pos_sec"]


def episode_status(con, slug: str) -> dict[int, tuple[bool, int]]:
    """episode -> (finished at least once, resume position of its latest session; 0 if that one finished)."""
    status: dict[int, tuple[bool, int]] = {}
    for row in con.execute("SELECT episode, finished, end_pos_sec FROM sessions WHERE slug = ? ORDER BY ended_at, id", (slug,)):
        ever = status.get(row["episode"], (False, 0))[0] or bool(row["finished"])
        status[row["episode"]] = (ever, 0 if row["finished"] else row["end_pos_sec"])
    return status


def import_progress(con, slug: str, episode: int, when: str | None = None) -> None:
    """Counts an episode as watched without a play: for last/highest, never for stats."""
    if episode <= 0:
        return
    t = when or now()
    con.execute(
        "INSERT INTO sessions (slug, episode, started_at, ended_at, finished, source) VALUES (?, ?, ?, ?, 1, 'import')",
        (slug, episode, t, t),
    )
    con.commit()
