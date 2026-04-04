"""Serviço de deduplicação — por repositório + SHA-256."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from .db import Database
from .errors import HashComputationError

logger = logging.getLogger("media_repo_pipeline.dedup")

_HASH_BUF_SIZE = 1024 * 1024  # 1 MiB


def compute_sha256(path: Path) -> str:
    """Calcula hash SHA-256 do arquivo."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                buf = f.read(_HASH_BUF_SIZE)
                if not buf:
                    break
                h.update(buf)
    except OSError as exc:
        raise HashComputationError(f"Falha ao calcular hash de {path}: {exc}") from exc
    return h.hexdigest()


def find_duplicate(
    repository_name_canonical: str,
    hash_sha256: str,
    db: Database,
) -> dict[str, Any] | None:
    """Verifica se já existe arquivo com mesmo hash no mesmo repositório.

    Retorna o registro kept_file correspondente ou None.
    """
    return db.find_kept_file(repository_name_canonical, hash_sha256)
