"""Postgres storage for Grant Scout: caches, the local grants database, and the prospect pipeline."""
import json
import os
import threading
import time
import hashlib
import secrets

import psycopg
from psycopg.rows import dict_row
from psycopg.errors import UniqueViolation

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at a Postgres connection string "
        "(Render provides one automatically for the web service)."
    )

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE TABLE IF NOT EXISTS api_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT UNIQUE NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token);
CREATE TABLE IF NOT EXISTS org_cache (
    ein TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS object_ids (
    object_id TEXT PRIMARY KEY,
    ein TEXT NOT NULL,
    discovered_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_object_ids_ein ON object_ids(ein);
CREATE TABLE IF NOT EXISTS filings_indexed (
    object_id TEXT PRIMARY KEY,
    ein TEXT NOT NULL,
    form_type TEXT,
    tax_year INTEGER,
    grants_count INTEGER DEFAULT 0,
    indexed_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filings_ein ON filings_indexed(ein);
CREATE TABLE IF NOT EXISTS grants (
    id SERIAL PRIMARY KEY,
    funder_ein TEXT NOT NULL,
    funder_name TEXT,
    object_id TEXT NOT NULL,
    tax_year INTEGER,
    recipient_name TEXT,
    recipient_ein TEXT,
    city TEXT,
    state TEXT,
    purpose TEXT,
    amount BIGINT,
    is_future INTEGER DEFAULT 0,
    visibility TEXT DEFAULT 'team',
    added_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_grants_funder ON grants(funder_ein);
CREATE INDEX IF NOT EXISTS idx_grants_state ON grants(state);
CREATE TABLE IF NOT EXISTS people (
    id SERIAL PRIMARY KEY,
    ein TEXT NOT NULL,
    object_id TEXT NOT NULL,
    name TEXT,
    title TEXT,
    compensation BIGINT
);
CREATE INDEX IF NOT EXISTS idx_people_ein ON people(ein);
CREATE TABLE IF NOT EXISTS pipeline (
    id SERIAL PRIMARY KEY,
    created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ein TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Researching',
    ask_amount TEXT,
    deadline TEXT,
    contact TEXT,
    notes TEXT,
    visibility TEXT DEFAULT 'personal',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_created_by ON pipeline(created_by_user_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

PIPELINE_STATUSES = ["Researching", "Good Fit", "Contacted", "LOI Sent", "Applied", "Awarded", "Declined"]

# Only deadlines shaped like YYYY-MM-DD are safe to cast to ::date; guard every
# cast against stray text so a bad value can't abort the whole query.
_DATE_GUARD = r"deadline ~ '^\d{4}-\d{2}-\d{2}'"


class _PGConn:
    """sqlite3.Connection-style wrapper so call sites can do conn.execute(...).fetchall()."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def cursor(self):
        return self._conn.cursor()


def get_db():
    conn = getattr(_local, "conn", None)
    if conn is None or conn._conn.closed:
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        with raw.cursor() as cur:
            cur.execute(SCHEMA)
        raw.commit()
        conn = _PGConn(raw)
        _local.conn = conn
    return conn


def pipeline_all_team():
    """Get team pipeline (visible to all)."""
    return get_db().execute(
        "SELECT * FROM pipeline WHERE visibility='team' ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END,"
        " deadline ASC, updated_at DESC").fetchall()

def pipeline_all_personal(user_id):
    """Get user's personal pipeline."""
    return get_db().execute(
        "SELECT * FROM pipeline WHERE visibility='personal' AND created_by_user_id=%s ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END,"
        " deadline ASC, updated_at DESC", (user_id,)).fetchall()

def search_grants_team(q=None, state=None, min_amount=None, max_amount=None, year=None, funder_ein=None, limit=200):
    """Search team grants (indexed 990s visible to all)."""
    sql = "SELECT * FROM grants WHERE visibility='team' AND 1=1"
    params = []
    if q:
        sql += " AND (recipient_name ILIKE %s OR purpose ILIKE %s OR funder_name ILIKE %s)"
        like = f"%{q}%"
        params += [like, like, like]
    if state:
        sql += " AND state = %s"
        params.append(state.upper())
    if min_amount is not None:
        sql += " AND amount >= %s"
        params.append(min_amount)
    if max_amount is not None:
        sql += " AND amount <= %s"
        params.append(max_amount)
    if year:
        sql += " AND tax_year = %s"
        params.append(year)
    if funder_ein:
        sql += " AND funder_ein = %s"
        params.append(str(funder_ein))
    sql += " ORDER BY amount DESC LIMIT %s"
    params.append(limit)
    return get_db().execute(sql, params).fetchall()

def search_grants_personal(user_id, q=None, state=None, min_amount=None, max_amount=None, year=None, funder_ein=None, limit=200):
    """Search user's personal grants research."""
    sql = "SELECT * FROM grants WHERE visibility='personal' AND added_by_user_id=%s AND 1=1"
    params = [user_id]
    if q:
        sql += " AND (recipient_name ILIKE %s OR purpose ILIKE %s OR funder_name ILIKE %s)"
        like = f"%{q}%"
        params += [like, like, like]
    if state:
        sql += " AND state = %s"
        params.append(state.upper())
    if min_amount is not None:
        sql += " AND amount >= %s"
        params.append(min_amount)
    if max_amount is not None:
        sql += " AND amount <= %s"
        params.append(max_amount)
    if year:
        sql += " AND tax_year = %s"
        params.append(year)
    if funder_ein:
        sql += " AND funder_ein = %s"
        params.append(str(funder_ein))
    sql += " ORDER BY amount DESC LIMIT %s"
    params.append(limit)
    return get_db().execute(sql, params).fetchall()

def pipeline_share_to_team(pid):
    """Move a prospect from personal to team."""
    db = get_db()
    db.execute("UPDATE pipeline SET visibility='team' WHERE id=%s", (pid,))
    db.commit()

def get_username_by_id(user_id):
    """Get username for a user."""
    if not user_id:
        return None
    row = get_db().execute("SELECT username FROM users WHERE id=%s", (user_id,)).fetchone()
    return row["username"] if row else None


# ---------- org cache (ProPublica JSON, 24h TTL) ----------

def cache_org(ein, payload):
    db = get_db()
    db.execute(
        "INSERT INTO org_cache (ein, payload, fetched_at) VALUES (%s,%s,%s)"
        " ON CONFLICT (ein) DO UPDATE SET payload=EXCLUDED.payload, fetched_at=EXCLUDED.fetched_at",
        (str(ein), json.dumps(payload), time.time()))
    db.commit()


def get_cached_org(ein, max_age=86400):
    row = get_db().execute("SELECT payload, fetched_at FROM org_cache WHERE ein=%s", (str(ein),)).fetchone()
    if row and time.time() - row["fetched_at"] < max_age:
        return json.loads(row["payload"])
    return None


# ---------- object ids ----------

def save_object_ids(ein, object_ids):
    db = get_db()
    for oid in object_ids:
        db.execute(
            "INSERT INTO object_ids (object_id, ein, discovered_at) VALUES (%s,%s,%s)"
            " ON CONFLICT (object_id) DO NOTHING",
            (str(oid), str(ein), time.time()))
    db.commit()


def get_object_ids(ein):
    rows = get_db().execute(
        "SELECT object_id FROM object_ids WHERE ein=%s ORDER BY object_id DESC", (str(ein),)).fetchall()
    return [r["object_id"] for r in rows]


# ---------- indexed filings & grants ----------

def is_filing_indexed(object_id):
    return get_db().execute("SELECT 1 FROM filings_indexed WHERE object_id=%s", (str(object_id),)).fetchone() is not None


def save_filing(object_id, ein, form_type, tax_year, grants, people, funder_name):
    db = get_db()
    db.execute("DELETE FROM grants WHERE object_id=%s", (str(object_id),))
    db.execute("DELETE FROM people WHERE object_id=%s", (str(object_id),))
    for g in grants:
        db.execute(
            "INSERT INTO grants (funder_ein, funder_name, object_id, tax_year, recipient_name, recipient_ein,"
            " city, state, purpose, amount, is_future) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ein), funder_name, str(object_id), tax_year, g.get("recipient_name"), g.get("recipient_ein"),
             g.get("city"), g.get("state"), g.get("purpose"), g.get("amount"), 1 if g.get("is_future") else 0))
    for p in people:
        db.execute("INSERT INTO people (ein, object_id, name, title, compensation) VALUES (%s,%s,%s,%s,%s)",
                   (str(ein), str(object_id), p.get("name"), p.get("title"), p.get("compensation")))
    db.execute(
        "INSERT INTO filings_indexed (object_id, ein, form_type, tax_year, grants_count, indexed_at)"
        " VALUES (%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (object_id) DO UPDATE SET ein=EXCLUDED.ein, form_type=EXCLUDED.form_type,"
        " tax_year=EXCLUDED.tax_year, grants_count=EXCLUDED.grants_count, indexed_at=EXCLUDED.indexed_at",
        (str(object_id), str(ein), form_type, tax_year, len(grants), time.time()))
    db.commit()


def indexed_filings_for(ein):
    return get_db().execute(
        "SELECT * FROM filings_indexed WHERE ein=%s ORDER BY tax_year DESC", (str(ein),)).fetchall()


GRANT_SORTS = {
    "amount_desc": "amount DESC",
    "amount_asc": "amount ASC",
    "year_desc": "tax_year DESC, amount DESC",
    "recent": "id DESC",
}


def search_grants(q=None, state=None, min_amount=None, max_amount=None, year=None,
                  funder_ein=None, sort="amount_desc", limit=200):
    sql = "SELECT * FROM grants WHERE 1=1"
    params = []
    if q:
        sql += " AND (recipient_name ILIKE %s OR purpose ILIKE %s OR funder_name ILIKE %s)"
        like = f"%{q}%"
        params += [like, like, like]
    if state:
        sql += " AND state = %s"
        params.append(state.upper())
    if min_amount is not None:
        sql += " AND amount >= %s"
        params.append(min_amount)
    if max_amount is not None:
        sql += " AND amount <= %s"
        params.append(max_amount)
    if year:
        sql += " AND tax_year = %s"
        params.append(year)
    if funder_ein:
        sql += " AND funder_ein = %s"
        params.append(str(funder_ein))
    sql += " ORDER BY " + GRANT_SORTS.get(sort, GRANT_SORTS["amount_desc"]) + " LIMIT %s"
    params.append(limit)
    return get_db().execute(sql, params).fetchall()


def funders_list():
    return get_db().execute(
        "SELECT DISTINCT funder_ein, funder_name FROM grants WHERE funder_name IS NOT NULL"
        " ORDER BY funder_name").fetchall()


def grants_by_year():
    rows = get_db().execute(
        "SELECT tax_year, COUNT(*) AS n FROM grants WHERE tax_year IS NOT NULL"
        " GROUP BY tax_year ORDER BY tax_year").fetchall()
    return [{"year": r["tax_year"], "n": r["n"]} for r in rows]


def grants_stats():
    db = get_db()
    row = db.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS total FROM grants").fetchone()
    funders = db.execute("SELECT COUNT(DISTINCT ein) AS n FROM filings_indexed").fetchone()
    return {"grants": row["n"], "total": row["total"], "funders": funders["n"]}


def grants_for_funder(ein, limit=500):
    return get_db().execute(
        "SELECT * FROM grants WHERE funder_ein=%s ORDER BY tax_year DESC, amount DESC LIMIT %s",
        (str(ein), limit)).fetchall()


def people_for(ein):
    # newest filing's roster only
    row = get_db().execute(
        "SELECT object_id FROM people WHERE ein=%s ORDER BY object_id DESC LIMIT 1", (str(ein),)).fetchone()
    if not row:
        return []
    return get_db().execute(
        "SELECT * FROM people WHERE ein=%s AND object_id=%s ORDER BY compensation DESC NULLS LAST",
        (str(ein), row["object_id"])).fetchall()


# ---------- pipeline ----------

def pipeline_all():
    return get_db().execute(
        "SELECT * FROM pipeline ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END,"
        " deadline ASC, updated_at DESC").fetchall()


def pipeline_get(pid):
    return get_db().execute("SELECT * FROM pipeline WHERE id=%s", (pid,)).fetchone()


def pipeline_has_ein(ein):
    return get_db().execute("SELECT id FROM pipeline WHERE ein=%s", (str(ein),)).fetchone()


def pipeline_add(ein, name, status="Researching", ask_amount="", deadline="", contact="", notes="", created_by_user_id=None):
    now = time.time()
    db = get_db()
    cur = db.execute(
        "INSERT INTO pipeline (created_by_user_id, ein, name, status, ask_amount, deadline, contact, notes, created_at, updated_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (created_by_user_id, str(ein) if ein else None, name, status, ask_amount, deadline, contact, notes, now, now))
    new_id = cur.fetchone()["id"]
    db.commit()
    return new_id

def pipeline_update(pid, **fields):
    allowed = {"status", "ask_amount", "deadline", "contact", "notes", "name"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=%s")
            params.append(v)
    if not sets:
        return
    sets.append("updated_at=%s")
    params.append(time.time())
    params.append(pid)
    db = get_db()
    db.execute(f"UPDATE pipeline SET {', '.join(sets)} WHERE id=%s", params)
    db.commit()


def pipeline_delete(pid):
    db = get_db()
    db.execute("DELETE FROM pipeline WHERE id=%s", (pid,))
    db.commit()


def pipeline_counts():
    rows = get_db().execute("SELECT status, COUNT(*) AS n FROM pipeline GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def pipeline_funnel_stats():
    """Dashboard funnel summary: drafts -> requested (LOI) -> submitted (Applied)."""
    counts = pipeline_counts()
    drafts = counts.get("Researching", 0) + counts.get("Good Fit", 0) + counts.get("Contacted", 0)
    requested = counts.get("LOI Sent", 0)
    submitted = counts.get("Applied", 0)
    next_row = get_db().execute(
        "SELECT name, deadline FROM pipeline WHERE deadline IS NOT NULL AND deadline != ''"
        f" AND {_DATE_GUARD} AND deadline::date >= CURRENT_DATE AND status NOT IN ('Awarded','Declined')"
        " ORDER BY deadline ASC LIMIT 1").fetchone()
    return {
        "drafts": drafts,
        "requested": requested,
        "submitted": submitted,
        "in_pipeline": drafts + requested + submitted,
        "next_deadline": dict(next_row) if next_row else None,
    }


# --- User Authentication ---

def create_user(username, email, password):
    """Create a new user with hashed password."""
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (username, email, password_hash, created_at, updated_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (username, email, password_hash, time.time(), time.time()))
        new_id = cur.fetchone()["id"]
        db.commit()
        return new_id
    except UniqueViolation:
        db.rollback()
        return None

def get_user_by_username(username):
    """Get user by username."""
    return get_db().execute("SELECT * FROM users WHERE username=%s", (username,)).fetchone()

def verify_password(stored_hash, password):
    """Verify password against hash."""
    return stored_hash == hashlib.sha256(password.encode()).hexdigest()

def create_api_token(user_id, expires_in_days=365):
    """Generate API token for user."""
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + (expires_in_days * 86400)
    db = get_db()
    db.execute(
        "INSERT INTO api_tokens (user_id, token, created_at, expires_at) VALUES (%s,%s,%s,%s)",
        (user_id, token, time.time(), expires_at))
    db.commit()
    return token

def get_user_by_token(token):
    """Verify token and return user."""
    row = get_db().execute(
        "SELECT u.* FROM users u JOIN api_tokens t ON u.id=t.user_id WHERE t.token=%s AND (t.expires_at IS NULL OR t.expires_at > %s)",
        (token, time.time())).fetchone()
    return row

# --- Deadline Tracking ---

def deadlines_upcoming(user_id=None, days_ahead=30):  # Ignore user_id param
    """Get all prospects with deadlines in next N days."""
    sql = f"""
    SELECT * FROM pipeline
    WHERE deadline IS NOT NULL AND deadline != ''
    AND {_DATE_GUARD}
    AND deadline::date BETWEEN CURRENT_DATE AND CURRENT_DATE + (%s * INTERVAL '1 day')
    ORDER BY deadline ASC
    """
    return get_db().execute(sql, [days_ahead]).fetchall()

def deadlines_overdue(user_id=None):  # Ignore user_id param
    """Get all prospects with passed deadlines."""
    sql = f"""
    SELECT * FROM pipeline
    WHERE deadline IS NOT NULL AND deadline != ''
    AND {_DATE_GUARD}
    AND deadline::date < CURRENT_DATE
    AND status != 'Awarded' AND status != 'Declined'
    ORDER BY deadline ASC
    """
    return get_db().execute(sql, []).fetchall()

def deadlines_by_month(user_id=None, year=None, month=None):  # Ignore user_id param
    """Get deadlines grouped by month for calendar view."""
    import datetime
    if not year or not month:
        today = datetime.date.today()
        year = year or today.year
        month = month or today.month

    sql = f"""
    SELECT * FROM pipeline
    WHERE deadline IS NOT NULL AND deadline != ''
    AND {_DATE_GUARD}
    AND to_char(deadline::date, 'YYYY') = %s
    AND to_char(deadline::date, 'MM') = %s
    ORDER BY deadline ASC
    """
    params = [str(year).zfill(4), str(month).zfill(2)]
    return get_db().execute(sql, params).fetchall()
