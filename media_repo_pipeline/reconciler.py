"""Reconciler — verifica inconsistências entre banco e filesystem."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .constants import DIR_ORGANIZED, DIR_SIDECARS
from .db import Database
from .sidecar_service import validate_sidecar

logger = logging.getLogger("media_repo_pipeline.reconciler")


def reconcile(db: Database, cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Executa reconciliação completa e retorna lista de inconsistências encontradas."""
    issues: list[dict[str, Any]] = []

    issues.extend(_check_kept_without_file(db, cfg))
    issues.extend(_check_file_without_record(db, cfg))
    issues.extend(_check_organized_without_sidecar(db, cfg))
    issues.extend(_check_sidecar_without_file(cfg))
    issues.extend(_check_orphan_tmp(cfg))

    # Registra no banco
    for issue in issues:
        db.insert_inconsistency(issue)

    logger.info("Reconciliação concluída: %d inconsistências encontradas.", len(issues))
    return issues


def _check_kept_without_file(db: Database, cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Registro no banco sem arquivo em organized."""
    issues: list[dict[str, Any]] = []
    for kept in db.get_all_kept_files():
        dest = Path(kept["canonical_destination_path"])
        if not dest.exists():
            issues.append({
                "inconsistency_type": "db_record_without_file",
                "repository_name_canonical": kept["repository_name_canonical"],
                "filesystem_path": str(dest),
                "db_reference": f"kept_files.id={kept['id']}",
            })
            logger.warning(
                "Registro sem arquivo: %s (repo: %s)",
                dest, kept["repository_name_canonical"],
            )
    return issues


def _check_file_without_record(db: Database, cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Arquivo em organized sem registro no banco."""
    issues: list[dict[str, Any]] = []
    organized_root = cfg.output_root / DIR_ORGANIZED
    if not organized_root.exists():
        return issues

    # Monta set de caminhos conhecidos
    known_paths = {
        kept["canonical_destination_path"]
        for kept in db.get_all_kept_files()
    }

    for path in organized_root.rglob("*"):
        if not path.is_file():
            continue
        if str(path) not in known_paths:
            # Descobre repo do caminho
            try:
                rel = path.relative_to(organized_root)
                repo = rel.parts[0] if rel.parts else "unknown"
            except ValueError:
                repo = "unknown"
            issues.append({
                "inconsistency_type": "file_without_db_record",
                "repository_name_canonical": repo,
                "filesystem_path": str(path),
                "db_reference": None,
            })
            logger.warning("Arquivo sem registro: %s", path)

    return issues


def _check_organized_without_sidecar(db: Database, cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Arquivo em organized sem sidecar correspondente."""
    issues: list[dict[str, Any]] = []

    if not cfg.sidecar_enabled:
        return issues

    organized_root = cfg.output_root / DIR_ORGANIZED
    sidecars_root = cfg.output_root / DIR_SIDECARS

    if not organized_root.exists():
        return issues

    for path in organized_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(organized_root)
        except ValueError:
            continue
        sidecar_path = sidecars_root / rel.with_suffix(rel.suffix + ".json")
        if not sidecar_path.exists():
            repo = rel.parts[0] if rel.parts else "unknown"
            issues.append({
                "inconsistency_type": "organized_without_sidecar",
                "repository_name_canonical": repo,
                "filesystem_path": str(path),
                "db_reference": None,
            })

    return issues


def _check_sidecar_without_file(cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Sidecar sem arquivo correspondente em organized."""
    issues: list[dict[str, Any]] = []
    sidecars_root = cfg.output_root / DIR_SIDECARS
    organized_root = cfg.output_root / DIR_ORGANIZED

    if not sidecars_root.exists():
        return issues

    for sidecar in sidecars_root.rglob("*.json"):
        try:
            rel = sidecar.relative_to(sidecars_root)
        except ValueError:
            continue
        # Remove o .json final para obter o path original
        original_name = rel.with_suffix("")  # remove ".json" -> keeps ".ext"
        organized_path = organized_root / original_name
        if not organized_path.exists():
            repo = rel.parts[0] if rel.parts else "unknown"
            issues.append({
                "inconsistency_type": "sidecar_without_file",
                "repository_name_canonical": repo,
                "filesystem_path": str(sidecar),
                "db_reference": None,
            })

    return issues


def _check_orphan_tmp(cfg: PipelineConfig) -> list[dict[str, Any]]:
    """Temporários órfãos em destino/tmp/."""
    issues: list[dict[str, Any]] = []
    tmp_dir = cfg.tmp_dir
    if not tmp_dir.exists():
        return issues

    for path in tmp_dir.iterdir():
        if path.is_file():
            issues.append({
                "inconsistency_type": "orphan_tmp_file",
                "repository_name_canonical": None,
                "filesystem_path": str(path),
                "db_reference": None,
            })
            logger.warning("Temporário órfão: %s", path)

    return issues
