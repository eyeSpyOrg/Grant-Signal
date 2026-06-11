"""Background indexer: pulls a funder's 990 XML filings and loads their grant
lists into the local database so they become searchable.

Runs in a daemon thread inside the Flask process. Status is polled by the UI.
"""
import threading
import time
import traceback

import db
import propublica
import xml990

MAX_FILINGS_PER_ORG = 6   # most recent filings to index per funder
REQUEST_PAUSE = 0.6       # seconds between network calls, to be polite

_lock = threading.Lock()
_state = {
    "running": False,
    "queue": [],        # [{ein, name}]
    "current": None,    # {ein, name, step}
    "done": [],         # [{ein, name, filings, grants}]
    "errors": [],       # [{ein, name, error}]
}
_thread = None


def status():
    with _lock:
        return {
            "running": _state["running"],
            "queue": list(_state["queue"]),
            "current": dict(_state["current"]) if _state["current"] else None,
            "done": list(_state["done"][-25:]),
            "errors": list(_state["errors"][-25:]),
        }


def enqueue(ein, name=""):
    ein = str(ein).replace("-", "")
    with _lock:
        in_queue = any(item["ein"] == ein for item in _state["queue"])
        is_current = _state["current"] and _state["current"]["ein"] == ein
        if in_queue or is_current:
            return False
        _state["queue"].append({"ein": ein, "name": name})
    _ensure_thread()
    return True


def _ensure_thread():
    global _thread
    with _lock:
        if _state["running"]:
            return
        _state["running"] = True
    _thread = threading.Thread(target=_worker, daemon=True)
    _thread.start()


def _set_step(step):
    with _lock:
        if _state["current"]:
            _state["current"]["step"] = step


def _worker():
    try:
        while True:
            with _lock:
                if not _state["queue"]:
                    _state["running"] = False
                    _state["current"] = None
                    return
                item = _state["queue"].pop(0)
                _state["current"] = {"ein": item["ein"], "name": item["name"], "step": "starting"}
            try:
                filings, grants = _index_org(item["ein"], item["name"])
                with _lock:
                    _state["done"].append({"ein": item["ein"], "name": item["name"],
                                           "filings": filings, "grants": grants})
            except Exception as e:
                traceback.print_exc()
                with _lock:
                    _state["errors"].append({"ein": item["ein"], "name": item["name"], "error": str(e)})
    finally:
        with _lock:
            _state["running"] = False
            _state["current"] = None


def _index_org(ein, name):
    _set_step("looking up filings")
    oids = propublica.discover_object_ids(ein)
    time.sleep(REQUEST_PAUSE)
    if not oids:
        raise RuntimeError("No e-file XML filings found for this organization")

    filings_done = 0
    grants_total = 0
    for oid in oids[:MAX_FILINGS_PER_ORG]:
        if db.is_filing_indexed(oid):
            filings_done += 1
            continue
        _set_step(f"downloading filing {oid}")
        xml_bytes = xml990.fetch_xml(oid)
        time.sleep(REQUEST_PAUSE)
        if not xml_bytes:
            continue
        _set_step(f"parsing filing {oid}")
        parsed = xml990.parse_filing(xml_bytes)
        h = parsed["header"]
        funder_name = name or h.get("org_name") or ""
        db.save_filing(oid, ein, h.get("form_type"), h.get("tax_year"),
                       parsed["grants"], parsed["people"], funder_name)
        filings_done += 1
        grants_total += len(parsed["grants"])
    return filings_done, grants_total
