"""SQLite persistence for audit reports."""
import json, os, sqlite3, time

DB_PATH = os.environ.get("AUDITOR_DB", os.path.join(os.path.dirname(__file__), "..", "data", "auditor.db"))

def _conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS reports(
            id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, source TEXT NOT NULL,
            title TEXT, score INTEGER NOT NULL, issue_count INTEGER NOT NULL, data TEXT NOT NULL)""")

def save(source, report):
    with _conn() as c:
        cur = c.execute("INSERT INTO reports(created,source,title,score,issue_count,data) VALUES(?,?,?,?,?,?)",
                        (time.time(), source, report.get("title", ""), report["score"]["total"],
                         len(report["issues"]), json.dumps(report)))
        return cur.lastrowid

def list_reports(limit=50):
    with _conn() as c:
        rows = c.execute("SELECT id,created,source,title,score,issue_count FROM reports ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

def get(rid):
    with _conn() as c:
        r = c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = json.loads(r["data"])
    d.update(id=r["id"], created=r["created"], source=r["source"])
    return d

def delete(rid):
    with _conn() as c:
        return c.execute("DELETE FROM reports WHERE id=?", (rid,)).rowcount > 0
