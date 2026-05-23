"""Modelos de dados (dataclasses) do pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RepositoryRecord:
    """Registro lógico de um repositório."""

    id: int | None = None
    repository_name_canonical: str = ""
    display_name: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    status: str = ""
    is_source_present: bool = True
    last_source_root_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    output_root_path: str = ""  # pasta raiz de destino dos arquivos organizados (vazio = aguardando config)


@dataclass
class FileInfo:
    """Informações coletadas sobre um arquivo de mídia."""

    source_path: str = ""
    rel_input_path: str = ""
    repository_name_canonical: str = ""
    repository_display_name: str = ""
    extension: str = ""
    media_kind: str = ""
    size_bytes: int = 0
    mtime_epoch: float = 0.0
    ctime_epoch: float = 0.0
    hash_sha256: str = ""
    capture_dt: str = ""
    capture_dt_source: str = ""
    capture_dt_confidence: str = ""
    device_make: str = ""
    device_model: str = ""
    software: str = ""
    metadata_json: dict[str, Any] = field(default_factory=dict)
    base_name_normalized: str = ""


@dataclass
class Decision:
    """Decisão de destino para um arquivo."""

    action: str = ""           # kept, duplicate, review, corrupted, skipped, error
    reason: str = ""
    destination: str = ""      # caminho absoluto de destino
    duplicate_of_hash: str = ""
    processing_retry_count: int = 0
    requires_review: bool = False


@dataclass
class ProcessingResult:
    """Resultado final do processamento de um arquivo."""

    success: bool = False
    file_info: FileInfo | None = None
    decision: Decision | None = None
    error_message: str = ""
    error_type: str = ""
