"""Serviço de sidecar JSON — gera metadado paralelo para todo arquivo aceito."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .constants import DIR_ORGANIZED, DIR_SIDECARS
from .errors import SidecarError
from .models import FileInfo

logger = logging.getLogger("media_repo_pipeline.sidecar")


def generate_sidecar(
    file_info: FileInfo,
    organized_path: Path,
    cfg: PipelineConfig,
) -> Path:
    """Gera e grava sidecar JSON em árvore paralela a organized/.

    Retorna o caminho do sidecar criado.
    Lança SidecarError se a gravação falhar.
    """
    sidecar_data = _build_sidecar_data(file_info, organized_path, cfg)
    sidecar_path = _compute_sidecar_path(organized_path, cfg)

    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(sidecar_data, f, ensure_ascii=False, indent=2, default=str)
        logger.debug("Sidecar gerado: %s", sidecar_path)
        return sidecar_path
    except OSError as exc:
        raise SidecarError(f"Falha ao gravar sidecar {sidecar_path}: {exc}") from exc


def _build_sidecar_data(
    fi: FileInfo,
    organized_path: Path,
    cfg: PipelineConfig,
) -> dict[str, Any]:
    return {
        "source_path": fi.source_path,
        "rel_input_path": fi.rel_input_path,
        "repository_name_canonical": fi.repository_name_canonical,
        "organized_path": str(organized_path),
        "hash_sha256": fi.hash_sha256,
        "media_kind": fi.media_kind,
        "capture_dt": fi.capture_dt,
        "capture_dt_source": fi.capture_dt_source,
        "capture_dt_confidence": fi.capture_dt_confidence,
        "device_make": fi.device_make,
        "device_model": fi.device_model,
        "software": fi.software,
        "size_bytes": fi.size_bytes,
        "extension": fi.extension,
        "processing_timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": cfg.pipeline_version,
        "policy_version": cfg.policy_version,
        "metadata": fi.metadata_json,
    }


def _compute_sidecar_path(organized_path: Path, cfg: PipelineConfig) -> Path:
    """Calcula caminho do sidecar espelhando a árvore organized/ em sidecars/."""
    organized_root = cfg.output_root / DIR_ORGANIZED
    try:
        rel = organized_path.relative_to(organized_root)
    except ValueError:
        # Se não é sub-path de organized, coloca em sidecars/misc/
        rel = Path(organized_path.name)

    sidecar_path = cfg.output_root / DIR_SIDECARS / rel.with_suffix(rel.suffix + ".json")
    return sidecar_path


def validate_sidecar(sidecar_path: Path) -> bool:
    """Verifica se um sidecar é válido (JSON parseable e com campos mínimos)."""
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        required = ("hash_sha256", "organized_path", "repository_name_canonical")
        return all(k in data for k in required)
    except (OSError, json.JSONDecodeError):
        return False
