"""SQLite storage for Grant Scout: caches, the local grants database, and the prospect pipeline."""
import sqlite3
import json
import os
import threading
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grantscout.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_cache (
    ein TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS object_ids (
    object_id TEXT PRIMARY KEY,
    ein TEXT NOT NULL,
    discovered_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_object_ids_ein ON object_ids(ein);
CREATE TABLE IF NOT EXISTS filings_indexed (
    object_id TEXT PRIMARY KEY,
    ein TEXT NOT NULL,
    form_type TEXT,
    tax_year INTEGER,
    grants_count INTEGER DEFAULT 0,
    indexed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filings_ein ON filings_indexed(ein);
CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    funder_ein TEXT NOT NULL,
    funder_name TEXT,
    object_id TEXT NOT NULL,
    tax_year INTEGER,
    recipient_name TEXT,
    recipient_ein TEXT,
    city TEXT,
    state TEXT,
    purpose TEXT,
    amount INTEGER,
    is_future INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_grants_funder ON grants(funder_ein);
CREATE INDEX IF NOT EXISTS idx_grants_state ON grants(state);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT NOT NULL,
    object_id TEXT NOT NULL,
    name TEXT,
    title TEXT,
    compensation INTEGER
);
CREATE INDEX IF NOT EXISTS idx_people_ein ON people(ein);
CREATE TABLE IF NOT EXISTS pipeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ein TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Researching',
    ask_amount TEXT,
    deadline TEXT,
    contact TEXT,
    notes TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

PIPELINE_STATUSES = ["Researching", "Good Fit", "Contacted", "LOI Sent", "Applied", "Awarded", "Declined"]


def get_db():
    if not hasattr(_local, "conn"):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.commit()
        _local.conn = conn
    return _local.conn


# ---------- org cache (ProPublica JSON, 24h TTL) ----------

def cache_org(ein, payload):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO org_cache (ein, payload, fetched_at) VALUES (?,?,?)",
               (str(ein), json.dumps(payload), time.time()))
    db.commit()


def get_cached_org(ein, max_age=86400):
    row = get_db().execute("SELECT payload, fetched_at FROM org_cache WHERE ein=?", (str(ein),)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age:
        return json.loads(row["payload"])
    return None


# ---------- object ids ----------

def save_object_ids(ein, object_ids):
    db = get_db()
    for oid in object_ids:
        db.execute("INSERT OR IGNORE INTO object_ids (object_id, ein, discovered_at) VALUES (?,?,?)",
                   (str(oid), str(ein), time.time()))
    db.commit()


def get_object_ids(ein):
    rows = get_db().execute(
        "SELECT object_id FROM object_ids WHERE ein=? ORDER BY object_id DESC", (str(ein),)).fetchall()
    return [r["object_id"] for r in rows]


# ---------- indexed filings & grants ----------

def is_filing_indexed(object_id):
    return get_db().execute("SELECT 1 FROM filings_indexed WHERE object_id=?", (str(object_id),)).fetchone() is not None


def save_filing(object_id, ein, form_type, tax_year, grants, people, funder_name):
    db = get_db()
    db.execute("DELETE FROM grants WHERE object_id=?", (str(object_id),))
    db.execute("DELETE FROM people WHERE object_id=?", (str(object_id),))
    for g in grants:
        db.execute(
            "INSERT INTO grants (funder_ein, funder_name, object_id, tax_year, recipient_name, recipient_ein,"
            " city, state, purpose, amount, is_future) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(ein), funder_name, str(object_id), tax_year, g.get("recipient_name"), g.get("recipient_ein"),
             g.get("city"), g.get("state"), g.get("purpose"), g.get("amount"), 1 if g.get("is_future") else 0))
    for p in people:
        db.execute("INSERT INTO people (ein, object_id, name, title, compensation) VALUES (?,?,?,?,?)",
                   (str(ein), str(object_id), p.get("name"), p.get("title"), p.get("compensation")))
    db.execute("INSERT OR REPLACE INTO filings_indexed (object_id, ein, form_type, tax_year, grants_count, indexed_at)"
               " VALUES (?,?,?,?,?,?)",
               (str(object_id), str(ein), form_type, tax_year, len(grants), time.time()))
    db.commit()


def indexed_filings_for(ein):
    return get_db().execute(
        "SELECT * FROM filings_indexed WHERE ein=? ORDER BY tax_year DESC", (str(ein),)).fetchall()


def search_grants(q=None, state=None, min_amount=None, max_amount=None, year=None,
                  funder_ein=None, limit=200):
    sql = "SELECT * FROM grants WHERE 1=1"
    params = []
    if q:
        sql += " AND (recipient_name LIKE ? OR purpose LIKE ? OR funder_name LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if state:
        sql += " AND state = ?"
        params.append(state.upper())
    if min_amount is not None:
        sql += " AND amount >= ?"
        params.append(min_amount)
    if max_amount is not None:
        sql += " AND amount <= ?"
        params.append(max_amount)
    if year:
        sql += " AND tax_year = ?"
        params.append(year)
    if funder_ein:
        sql += " AND funder_ein = ?"
        params.append(str(funder_ein))
    sql += " ORDER BY amount DESC LIMIT ?"
    params.append(limit)
    return get_db().execute(sql, params).fetchall()


def grants_stats():
    db = get_db()
    row = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total FROM grants").fetchone()
    funders = db.execute("SELECT COUNT(DISTINCT ein) AS n FROM filings_indexed").fetchone()
    return {"grants": row["n"], "total": row["total"], "funders": funders["n"]}


def grants_for_funder(ein, limit=500):
    return get_db().execute(
        "SELECT * FROM grants WHERE funder_ein=? ORDER BY tax_year DESC, amount DESC LIMIT ?",
        (str(ein), limit)).fetchall()


def people_for(ein):
    # newest filing's roster only
    row = get_db().execute(
        "SELECT object_id FROM people WHERE ein=? ORDER BY object_id DESC LIMIT 1", (str(ein),)).fetchone()
    if not row:
        return []
    return get_db().execute(
        "SELECT * FROM people WHERE ein=? AND object_id=? ORDER BY compensation DESC NULLS LAST",
        (str(ein), row["object_id"])).fetchall()


# ---------- pipeline ----------

def pipeline_all():
    return get_db().execute(
        "SELECT * FROM pipeline ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END,"
        " deadline ASC, updated_at DESC").fetchall()


def pipeline_get(pid):
    return get_db().execute("SELECT * FROM pipeline WHERE id=?", (pid,)).fetchone()


def pipeline_has_ein(ein):
    return get_db().execute("SELECT id FROM pipeline WHERE ein=?", (str(ein),)).fetchone()


def pipeline_add(ein, name, status="Researching", ask_amount="", deadline="", contact="", notes=""):
    now = time.time()
    db = get_db()
    cur = db.execute(
        "INSERT INTO pipeline (ein, name, status, ask_amount, deadline, contact, notes, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (str(ein) if ein else None, name, status, ask_amount, deadline, contact, notes, now, now))
    db.commit()
    return cur.lastrowid


def pipeline_update(pid, **fields):
    allowed = {"status", "ask_amount", "deadline", "contact", "notes", "name"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    params.append(time.time())
    params.append(pid)
    db = get_db()
    db.execute(f"UPDATE pipeline SET {', '.join(sets)} WHERE id=?", params)
    db.commit()


def pipeline_delete(pid):
    db = get_db()
    db.execute("DELETE FROM pipeline WHERE id=?", (pid,))
    db.commit()


def pipeline_counts():
    rows = get_db().execute("SELECT status, COUNT(*) AS n FROM pipeline GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
