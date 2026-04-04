"""Camada de acesso ao banco SQLite com WAL."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from .errors import DatabaseError
from .models import RepositoryRecord

_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS repositories (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_name_canonical TEXT    UNIQUE NOT NULL,
    display_name              TEXT    NOT NULL,
    first_seen_at             TEXT    NOT NULL,
    last_seen_at              TEXT    NOT NULL,
    status                    TEXT    NOT NULL,
    is_source_present         INTEGER NOT NULL DEFAULT 1,
    last_source_root_path     TEXT,
    created_at                TEXT    NOT NULL,
    updated_at                TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path               TEXT    NOT NULL,
    repository_name_canonical TEXT    NOT NULL,
    rel_input_path            TEXT    NOT NULL,
    extension                 TEXT    NOT NULL,
    media_kind                TEXT    NOT NULL,
    size_bytes                INTEGER NOT NULL,
    mtime_epoch               REAL    NOT NULL,
    ctime_epoch               REAL    NOT NULL,
    hash_sha256               TEXT,
    capture_dt                TEXT,
    capture_dt_source         TEXT,
    capture_dt_confidence     TEXT,
    status                    TEXT    NOT NULL,
    reason                    TEXT,
    destination_path          TEXT,
    duplicate_of_hash         TEXT,
    metadata_json             TEXT,
    first_seen_at             TEXT    NOT NULL,
    last_seen_at              TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_source_path ON files(source_path);
CREATE INDEX IF NOT EXISTS idx_files_repo ON files(repository_name_canonical);
CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash_sha256);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);

CREATE TABLE IF NOT EXISTS kept_files (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_name_canonical TEXT    NOT NULL,
    hash_sha256               TEXT    NOT NULL,
    canonical_destination_path TEXT   NOT NULL,
    media_kind                TEXT    NOT NULL,
    extension                 TEXT    NOT NULL,
    size_bytes                INTEGER NOT NULL,
    capture_dt                TEXT,
    capture_dt_source         TEXT,
    metadata_json             TEXT,
    created_at                TEXT    NOT NULL,
    updated_at                TEXT    NOT NULL,
    UNIQUE(repository_name_canonical, hash_sha256)
);

CREATE TABLE IF NOT EXISTS source_state (
    source_path               TEXT    PRIMARY KEY,
    repository_name_canonical TEXT    NOT NULL,
    size_bytes                INTEGER NOT NULL,
    mtime_epoch               REAL    NOT NULL,
    last_hash_sha256          TEXT,
    last_processed_at         TEXT    NOT NULL,
    last_status               TEXT    NOT NULL,
    retry_count               INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS processing_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT    NOT NULL,
    finished_at      TEXT,
    mode             TEXT    NOT NULL,
    pipeline_version TEXT    NOT NULL,
    policy_version   TEXT    NOT NULL,
    files_seen       INTEGER DEFAULT 0,
    files_supported  INTEGER DEFAULT 0,
    files_kept       INTEGER DEFAULT 0,
    files_duplicate  INTEGER DEFAULT 0,
    files_review     INTEGER DEFAULT 0,
    files_corrupted  INTEGER DEFAULT 0,
    files_skipped    INTEGER DEFAULT 0,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS inconsistencies (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    inconsistency_type        TEXT    NOT NULL,
    repository_name_canonical TEXT,
    filesystem_path           TEXT,
    db_reference              TEXT,
    detected_at               TEXT    NOT NULL,
    resolved_at               TEXT,
    resolution_notes          TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Wrapper para o banco SQLite do pipeline."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=60,           # mais margem para contenção multi-thread
            check_same_thread=False,  # cada thread abre sua própria conexão
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError("Banco não está aberto.")
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ── Repositories ──────────────────────────────────────────────────

    @staticmethod
    def _row_to_repository(row) -> RepositoryRecord:
        d = dict(row)
        d["is_source_present"] = bool(d.get("is_source_present", 1))
        return RepositoryRecord(**d)

    def get_repository(self, canonical_name: str) -> RepositoryRecord | None:
        row = self.conn.execute(
            "SELECT * FROM repositories WHERE repository_name_canonical = ?",
            (canonical_name,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_repository(row)

    def get_all_repositories(self) -> list[RepositoryRecord]:
        rows = self.conn.execute("SELECT * FROM repositories").fetchall()
        return [self._row_to_repository(r) for r in rows]

    def upsert_repository(self, repo: RepositoryRecord) -> int:
        now = _now_iso()
        with self.transaction() as cur:
            existing = self.get_repository(repo.repository_name_canonical)
            if existing:
                cur.execute(
                    """UPDATE repositories
                       SET display_name = ?, last_seen_at = ?, status = ?,
                           is_source_present = ?, last_source_root_path = ?, updated_at = ?
                     WHERE repository_name_canonical = ?""",
                    (
                        repo.display_name,
                        now,
                        repo.status,
                        int(repo.is_source_present),
                        repo.last_source_root_path,
                        now,
                        repo.repository_name_canonical,
                    ),
                )
                return existing.id  # type: ignore[return-value]
            else:
                cur.execute(
                    """INSERT INTO repositories
                       (repository_name_canonical, display_name, first_seen_at, last_seen_at,
                        status, is_source_present, last_source_root_path, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        repo.repository_name_canonical,
                        repo.display_name,
                        now, now,
                        repo.status,
                        int(repo.is_source_present),
                        repo.last_source_root_path,
                        now, now,
                    ),
                )
                return cur.lastrowid  # type: ignore[return-value]

    def mark_repository_inactive(self, canonical_name: str) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """UPDATE repositories
                   SET status = 'inactive_source_missing', is_source_present = 0, updated_at = ?
                 WHERE repository_name_canonical = ?""",
                (now, canonical_name),
            )

    # ── Kept files ────────────────────────────────────────────────────

    def find_kept_file(self, canonical_name: str, hash_sha256: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM kept_files WHERE repository_name_canonical = ? AND hash_sha256 = ?",
            (canonical_name, hash_sha256),
        ).fetchone()
        return dict(row) if row else None

    def insert_kept_file(self, data: dict[str, Any]) -> int:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO kept_files
                   (repository_name_canonical, hash_sha256, canonical_destination_path,
                    media_kind, extension, size_bytes, capture_dt, capture_dt_source,
                    metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["repository_name_canonical"],
                    data["hash_sha256"],
                    data["canonical_destination_path"],
                    data["media_kind"],
                    data["extension"],
                    data["size_bytes"],
                    data.get("capture_dt"),
                    data.get("capture_dt_source"),
                    data.get("metadata_json"),
                    now, now,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    # ── Source state ──────────────────────────────────────────────────

    def get_source_state(self, source_path: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM source_state WHERE source_path = ?", (source_path,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_source_state(self, data: dict[str, Any]) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO source_state
                   (source_path, repository_name_canonical, size_bytes, mtime_epoch,
                    last_hash_sha256, last_processed_at, last_status, retry_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_path) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    mtime_epoch = excluded.mtime_epoch,
                    last_hash_sha256 = excluded.last_hash_sha256,
                    last_processed_at = excluded.last_processed_at,
                    last_status = excluded.last_status,
                    retry_count = excluded.retry_count""",
                (
                    data["source_path"],
                    data["repository_name_canonical"],
                    data["size_bytes"],
                    data["mtime_epoch"],
                    data.get("last_hash_sha256"),
                    now,
                    data["last_status"],
                    data.get("retry_count", 0),
                ),
            )

    # ── Files ─────────────────────────────────────────────────────────

    def insert_file_record(self, data: dict[str, Any]) -> int:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO files
                   (source_path, repository_name_canonical, rel_input_path, extension,
                    media_kind, size_bytes, mtime_epoch, ctime_epoch, hash_sha256,
                    capture_dt, capture_dt_source, capture_dt_confidence, status, reason,
                    destination_path, duplicate_of_hash, metadata_json, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["source_path"],
                    data["repository_name_canonical"],
                    data["rel_input_path"],
                    data["extension"],
                    data["media_kind"],
                    data["size_bytes"],
                    data["mtime_epoch"],
                    data["ctime_epoch"],
                    data.get("hash_sha256"),
                    data.get("capture_dt"),
                    data.get("capture_dt_source"),
                    data.get("capture_dt_confidence"),
                    data["status"],
                    data.get("reason"),
                    data.get("destination_path"),
                    data.get("duplicate_of_hash"),
                    data.get("metadata_json"),
                    now, now,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def record_file_transaction(
        self,
        file_data: dict[str, Any],
        kept_data: dict[str, Any] | None,
        state_data: dict[str, Any],
    ) -> int:
        """Grava file_record + kept_file (opcional) + source_state em UMA única
        transação atômica, reduzindo 3x a contenção de lock em execuções
        multi-thread (eliminando os erros 'database is locked').

        Retorna o id inserido na tabela files.
        """
        now = _now_iso()
        with self.transaction() as cur:
            # 1. files
            cur.execute(
                """INSERT INTO files
                   (source_path, repository_name_canonical, rel_input_path, extension,
                    media_kind, size_bytes, mtime_epoch, ctime_epoch, hash_sha256,
                    capture_dt, capture_dt_source, capture_dt_confidence, status, reason,
                    destination_path, duplicate_of_hash, metadata_json, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    file_data["source_path"],
                    file_data["repository_name_canonical"],
                    file_data["rel_input_path"],
                    file_data["extension"],
                    file_data["media_kind"],
                    file_data["size_bytes"],
                    file_data["mtime_epoch"],
                    file_data["ctime_epoch"],
                    file_data.get("hash_sha256"),
                    file_data.get("capture_dt"),
                    file_data.get("capture_dt_source"),
                    file_data.get("capture_dt_confidence"),
                    file_data["status"],
                    file_data.get("reason"),
                    file_data.get("destination_path"),
                    file_data.get("duplicate_of_hash"),
                    file_data.get("metadata_json"),
                    now, now,
                ),
            )
            file_id: int = cur.lastrowid  # type: ignore[assignment]

            # 2. kept_files (apenas se kept)
            if kept_data is not None:
                cur.execute(
                    """INSERT INTO kept_files
                       (repository_name_canonical, hash_sha256, canonical_destination_path,
                        media_kind, extension, size_bytes, capture_dt, capture_dt_source,
                        metadata_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        kept_data["repository_name_canonical"],
                        kept_data["hash_sha256"],
                        kept_data["canonical_destination_path"],
                        kept_data["media_kind"],
                        kept_data["extension"],
                        kept_data["size_bytes"],
                        kept_data.get("capture_dt"),
                        kept_data.get("capture_dt_source"),
                        kept_data.get("metadata_json"),
                        now, now,
                    ),
                )

            # 3. source_state
            cur.execute(
                """INSERT INTO source_state
                   (source_path, repository_name_canonical, size_bytes, mtime_epoch,
                    last_hash_sha256, last_processed_at, last_status, retry_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_path) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    mtime_epoch = excluded.mtime_epoch,
                    last_hash_sha256 = excluded.last_hash_sha256,
                    last_processed_at = excluded.last_processed_at,
                    last_status = excluded.last_status,
                    retry_count = excluded.retry_count""",
                (
                    state_data["source_path"],
                    state_data["repository_name_canonical"],
                    state_data["size_bytes"],
                    state_data["mtime_epoch"],
                    state_data.get("last_hash_sha256"),
                    now,
                    state_data["last_status"],
                    state_data.get("retry_count", 0),
                ),
            )

        return file_id

    # ── Processing runs ───────────────────────────────────────────────

    def start_run(self, mode: str, pipeline_version: str, policy_version: str) -> int:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO processing_runs
                   (started_at, mode, pipeline_version, policy_version)
                   VALUES (?, ?, ?, ?)""",
                (now, mode, pipeline_version, policy_version),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def finish_run(self, run_id: int, stats: dict[str, int], notes: str = "") -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """UPDATE processing_runs
                   SET finished_at = ?,
                       files_seen = ?, files_supported = ?, files_kept = ?,
                       files_duplicate = ?, files_review = ?, files_corrupted = ?,
                       files_skipped = ?, notes = ?
                 WHERE id = ?""",
                (
                    now,
                    stats.get("seen", 0),
                    stats.get("supported", 0),
                    stats.get("kept", 0),
                    stats.get("duplicate", 0),
                    stats.get("review", 0),
                    stats.get("corrupted", 0),
                    stats.get("skipped", 0),
                    notes,
                    run_id,
                ),
            )

    # ── Inconsistencies ───────────────────────────────────────────────

    def insert_inconsistency(self, data: dict[str, Any]) -> int:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """INSERT INTO inconsistencies
                   (inconsistency_type, repository_name_canonical, filesystem_path,
                    db_reference, detected_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    data["inconsistency_type"],
                    data.get("repository_name_canonical"),
                    data.get("filesystem_path"),
                    data.get("db_reference"),
                    now,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def get_all_kept_files(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM kept_files").fetchall()
        return [dict(r) for r in rows]
