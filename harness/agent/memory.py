"""Agent-managed memory layer (Phase G).

A scenario-scoped SQLite store the *agent* curates through three tools. The
harness never writes rows itself and never injects facts into the agent's
context — it only provides a blank store. The agent decides what to remember and
what to retrieve. Memory usage is not scored; it only reduces signal loss for
models that take disciplined notes.

`run_id` is stamped automatically on every write (metadata, not curation) so the
store can later bridge multiple cold runs of one scenario without redesign.
"""
import os
import sqlite3
import time

NS_KEY_MAX_CHARS = 256
CONTENT_MAX_BYTES = 64 * 1024
MAX_ROWS = 50


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class MemoryStore:
    """SQLite-backed freeform key/value memory with FTS5 search (LIKE fallback)."""

    def __init__(self, db_path: str, run_id: str, enable_fts: bool = True):
        self.db_path = db_path
        self.run_id = run_id
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memory (
                   id        INTEGER PRIMARY KEY AUTOINCREMENT,
                   namespace TEXT NOT NULL,
                   key       TEXT NOT NULL,
                   content   TEXT NOT NULL,
                   run_id    TEXT NOT NULL,
                   ts        TEXT NOT NULL,
                   UNIQUE(namespace, key)
               )"""
        )
        self.fts_enabled = self._init_fts() if enable_fts else False
        self._conn.commit()

    def _init_fts(self) -> bool:
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                "USING fts5(namespace, key, content)"
            )
            return True
        except sqlite3.OperationalError:
            return False

    # ── writes ────────────────────────────────────────────────────────────────

    def write(self, namespace: str, key: str, content: str) -> str:
        if not isinstance(namespace, str) or not namespace.strip():
            return "Error: namespace must be a non-empty string."
        if not isinstance(key, str) or not key.strip():
            return "Error: key must be a non-empty string."
        if len(namespace) > NS_KEY_MAX_CHARS or len(key) > NS_KEY_MAX_CHARS:
            return f"Error: namespace and key must each be <= {NS_KEY_MAX_CHARS} chars."
        if not isinstance(content, str):
            return "Error: content must be a string."
        size = len(content.encode("utf-8"))
        if size > CONTENT_MAX_BYTES:
            return f"Error: content is too large ({size} bytes; limit {CONTENT_MAX_BYTES})."

        ts = _now()
        cur = self._conn.execute(
            """INSERT INTO memory (namespace, key, content, run_id, ts)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(namespace, key)
               DO UPDATE SET content=excluded.content,
                             run_id=excluded.run_id,
                             ts=excluded.ts""",
            (namespace, key, content, self.run_id, ts),
        )
        row_id = cur.lastrowid
        if row_id == 0:  # upsert path: fetch the existing id
            row = self._conn.execute(
                "SELECT id FROM memory WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
            row_id = row["id"]
        if self.fts_enabled:
            self._conn.execute("DELETE FROM memory_fts WHERE rowid=?", (row_id,))
            self._conn.execute(
                "INSERT INTO memory_fts(rowid, namespace, key, content) VALUES (?, ?, ?, ?)",
                (row_id, namespace, key, content),
            )
        self._conn.commit()
        return f"Saved {namespace}/{key} ({len(content)} chars)."

    # ── reads ─────────────────────────────────────────────────────────────────

    def read(self, namespace: str | None = None) -> list[dict]:
        if namespace is None:
            rows = self._conn.execute(
                "SELECT namespace, COUNT(*) AS count FROM memory "
                "GROUP BY namespace ORDER BY namespace"
            ).fetchall()
            return [{"namespace": r["namespace"], "count": r["count"]} for r in rows]
        rows = self._conn.execute(
            "SELECT namespace, key, content, run_id, ts FROM memory "
            "WHERE namespace=? ORDER BY id DESC LIMIT ?",
            (namespace, MAX_ROWS),
        ).fetchall()
        return [dict(r) for r in rows]

    def search(self, query: str, namespace: str | None = None) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            return []
        if self.fts_enabled:
            try:
                return self._search_fts(query, namespace)
            except sqlite3.OperationalError:
                pass  # malformed FTS query → fall through to LIKE
        return self._search_like(query, namespace)

    def _search_fts(self, query: str, namespace: str | None) -> list[dict]:
        sql = (
            "SELECT m.namespace, m.key, m.content, m.run_id, m.ts "
            "FROM memory_fts f JOIN memory m ON m.id = f.rowid "
            "WHERE memory_fts MATCH ?"
        )
        params: list = [query]
        if namespace is not None:
            sql += " AND m.namespace = ?"
            params.append(namespace)
        sql += " ORDER BY rank LIMIT ?"
        params.append(MAX_ROWS)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _search_like(self, query: str, namespace: str | None) -> list[dict]:
        like = f"%{query}%"
        sql = (
            "SELECT namespace, key, content, run_id, ts FROM memory "
            "WHERE (content LIKE ? OR key LIKE ?)"
        )
        params: list = [like, like]
        if namespace is not None:
            sql += " AND namespace = ?"
            params.append(namespace)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(MAX_ROWS)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass


def teardown_memory(db_path: str) -> None:
    """Remove the memory DB and its WAL/SHM sidecars. Idempotent."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except FileNotFoundError:
            pass
        except OSError:
            pass
