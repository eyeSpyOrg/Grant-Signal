"""SQLite storage for Grant Scout: caches, the local grants database, and the prospect pipeline."""
import sqlite3
import json
import os
import threading
import time
import hashlib
import secrets

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grantscout.db")

_local = threading.local()

# SCHEMA = """
# CREATE TABLE IF NOT EXISTS org_cache (
#     ein TEXT PRIMARY KEY,
#     payload TEXT NOT NULL,
#     fetched_at REAL NOT NULL
# );
# CREATE TABLE IF NOT EXISTS object_ids (
#     object_id TEXT PRIMARY KEY,
#     ein TEXT NOT NULL,
#     discovered_at REAL NOT NULL
# );
# CREATE INDEX IF NOT EXISTS idx_object_ids_ein ON object_ids(ein);
# CREATE TABLE IF NOT EXISTS filings_indexed (
#     object_id TEXT PRIMARY KEY,
#     ein TEXT NOT NULL,
#     form_type TEXT,
#     tax_year INTEGER,
#     grants_count INTEGER DEFAULT 0,
#     indexed_at REAL NOT NULL
# );
# CREATE INDEX IF NOT EXISTS idx_filings_ein ON filings_indexed(ein);
# CREATE TABLE IF NOT EXISTS grants (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     funder_ein TEXT NOT NULL,
#     funder_name TEXT,
#     object_id TEXT NOT NULL,
#     tax_year INTEGER,
#     recipient_name TEXT,
#     recipient_ein TEXT,
#     city TEXT,
#     state TEXT,
#     purpose TEXT,
#     amount INTEGER,
#     is_future INTEGER DEFAULT 0
# );
# CREATE INDEX IF NOT EXISTS idx_grants_funder ON grants(funder_ein);
# CREATE INDEX IF NOT EXISTS idx_grants_state ON grants(state);
# CREATE TABLE IF NOT EXISTS people (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     ein TEXT NOT NULL,
#     object_id TEXT NOT NULL,
#     name TEXT,
#     title TEXT,
#     compensation INTEGER
# );
# CREATE INDEX IF NOT EXISTS idx_people_ein ON people(ein);
# CREATE TABLE IF NOT EXISTS pipeline (
#     id INTEGER PRIMARY KEY AUTOINCREMENT,
#     ein TEXT,
#     name TEXT NOT NULL,
#     status TEXT NOT NULL DEFAULT 'Researching',
#     ask_amount TEXT,
#     deadline TEXT,
#     contact TEXT,
#     notes TEXT,
#     created_at REAL NOT NULL,
#     updated_at REAL NOT NULL
# );
# CREATE TABLE IF NOT EXISTS settings (
#     key TEXT PRIMARY KEY,
#     value TEXT
# );
# """
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token);
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
    is_future INTEGER DEFAULT 0,
    visibility TEXT DEFAULT 'team',
    added_by_user_id INTEGER,
    FOREIGN KEY(added_by_user_id) REFERENCES users(id) ON DELETE SET NULL
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
    created_by_user_id INTEGER,
    ein TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Researching',
    ask_amount TEXT,
    deadline TEXT,
    contact TEXT,
    notes TEXT,
    visibility TEXT DEFAULT 'personal',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_created_by ON pipeline(created_by_user_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

PIPELINE_STATUSES = ["Researching", "Good Fit", "Contacted", "LOI Sent", "Applied", "Awarded", "Declined"]


def pipeline_all_team():
    """Get team pipeline (visible to all)."""
    return get_db().execute(
        "SELECT * FROM pipeline WHERE visibility='team' ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END,"
        " deadline ASC, updated_at DESC").fetchall()

def pipeline_all_personal(user_id):
    """Get user's personal pipeline."""
    return get_db().execute(
        "SELECT * FROM pipeline WHERE visibility='personal' AND created_by_user_id=? ORDER BY CASE WHEN deadline IS NULL OR deadline='' THEN 1 ELSE 0 END,"
        " deadline ASC, updated_at DESC", (user_id,)).fetchall()

def search_grants_team(q=None, state=None, min_amount=None, max_amount=None, year=None, funder_ein=None, limit=200):
    """Search team grants (indexed 990s visible to all)."""
    sql = "SELECT * FROM grants WHERE visibility='team' AND 1=1"
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

def search_grants_personal(user_id, q=None, state=None, min_amount=None, max_amount=None, year=None, funder_ein=None, limit=200):
    """Search user's personal grants research."""
    sql = "SELECT * FROM grants WHERE visibility='personal' AND added_by_user_id=? AND 1=1"
    params = [user_id]
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

def pipeline_share_to_team(pid):
    """Move a prospect from personal to team."""
    db = get_db()
    db.execute("UPDATE pipeline SET visibility='team' WHERE id=?", (pid,))
    db.commit()

def get_username_by_id(user_id):
    """Get username for a user."""
    if not user_id:
        return None
    row = get_db().execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    return row["username"] if row else None

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


def pipeline_add(ein, name, status="Researching", ask_amount="", deadline="", contact="", notes="", created_by_user_id=None):
    now = time.time()
    db = get_db()
    cur = db.execute(
        "INSERT INTO pipeline (created_by_user_id, ein, name, status, ask_amount, deadline, contact, notes, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (created_by_user_id, str(ein) if ein else None, name, status, ask_amount, deadline, contact, notes, now, now))
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


import hashlib
import secrets

# --- User Authentication ---

def create_user(username, email, password):
    """Create a new user with hashed password."""
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    try:
        db = get_db()
        cur = db.execute(
            "INSERT INTO users (username, email, password_hash, created_at, updated_at) VALUES (?,?,?,?,?)",
            (username, email, password_hash, time.time(), time.time()))
        db.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None

def get_user_by_username(username):
    """Get user by username."""
    return get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

def verify_password(stored_hash, password):
    """Verify password against hash."""
    return stored_hash == hashlib.sha256(password.encode()).hexdigest()

def create_api_token(user_id, expires_in_days=365):
    """Generate API token for user."""
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + (expires_in_days * 86400)
    db = get_db()
    db.execute(
        "INSERT INTO api_tokens (user_id, token, created_at, expires_at) VALUES (?,?,?,?)",
        (user_id, token, time.time(), expires_at))
    db.commit()
    return token

def get_user_by_token(token):
    """Verify token and return user."""
    row = get_db().execute(
        "SELECT u.* FROM users u JOIN api_tokens t ON u.id=t.user_id WHERE t.token=? AND (t.expires_at IS NULL OR t.expires_at > ?)",
        (token, time.time())).fetchone()
    return row

# --- Deadline Tracking ---

def deadlines_upcoming(user_id=None, days_ahead=30):  # Ignore user_id param
    """Get all prospects with deadlines in next N days."""
    sql = """
    SELECT * FROM pipeline 
    WHERE deadline IS NOT NULL AND deadline != ''
    AND date(deadline) BETWEEN date('now') AND date('now', '+' || ? || ' days')
    ORDER BY deadline ASC
    """
    return get_db().execute(sql, [days_ahead]).fetchall()

def deadlines_overdue(user_id=None):  # Ignore user_id param
    """Get all prospects with passed deadlines."""
    sql = """
    SELECT * FROM pipeline 
    WHERE deadline IS NOT NULL AND deadline != ''
    AND date(deadline) < date('now')
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
    
    sql = """
    SELECT * FROM pipeline 
    WHERE deadline IS NOT NULL AND deadline != ''
    AND strftime('%Y', deadline) = ?
    AND strftime('%m', deadline) = ?
    ORDER BY deadline ASC
    """
    params = [str(year).zfill(4), str(month).zfill(2)]
    return get_db().execute(sql, params).fetchall()