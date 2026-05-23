"""Serviço de repositório — criação, reativação, canonicalização."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PipelineConfig
from .constants import (
    DIR_CORRUPTED,
    DIR_DUPLICATES,
    DIR_ORGANIZED,
    DIR_REVIEW,
    DIR_SIDECARS,
    REPO_STATUS_ACTIVE,
    REPO_STATUS_INACTIVE,
    SUBDIR_PHOTOS,
    SUBDIR_VIDEOS,
)
from .db import Database
from .models import RepositoryRecord
from .utils import canonicalize_repo_name

logger = logging.getLogger("media_repo_pipeline.repository")


def ensure_repository(
    raw_name: str,
    source_path: Path,
    db: Database,
    cfg: PipelineConfig,
) -> RepositoryRecord:
    """Garante que o repositório existe no banco e no filesystem.

    - Canonicaliza o nome
    - Busca no banco
    - Cria ou reativa
    - Garante estrutura física idempotente
    """
    canonical = canonicalize_repo_name(raw_name)
    display = raw_name.strip()

    existing = db.get_repository(canonical)

    if existing:
        # Reativar se estava inativo
        if existing.status == REPO_STATUS_INACTIVE:
            logger.info("Reativando repositório '%s' (canonical: %s).", display, canonical)

        # Auto-migração: repos de bancos antigos (sem output_root_path) recebem o default
        if not existing.output_root_path:
            default_path = str(cfg.output_root / DIR_ORGANIZED / canonical)
            logger.info(
                "Repositório '%s' migrado: output_root_path definido como '%s'.",
                canonical, default_path,
            )
            db.set_repository_output_root(canonical, default_path)
            existing.output_root_path = default_path

        existing.status = REPO_STATUS_ACTIVE
        existing.is_source_present = True
        existing.display_name = display
        existing.last_source_root_path = str(source_path)
        db.upsert_repository(existing)
        _ensure_directory_structure(canonical, cfg, existing.output_root_path)
        return existing

    # Novo repositório — aguarda configuração de destino pelo usuário
    logger.info(
        "Novo repositório descoberto: '%s' (canonical: %s). "
        "Aguardando configuração de pasta de destino no dashboard.",
        display, canonical,
    )
    repo = RepositoryRecord(
        repository_name_canonical=canonical,
        display_name=display,
        status=REPO_STATUS_ACTIVE,
        is_source_present=True,
        last_source_root_path=str(source_path),
        output_root_path="",  # pendente — sem processamento até o usuário configurar
    )
    repo_id = db.upsert_repository(repo)
    repo.id = repo_id
    # Não cria estrutura de diretórios — sem caminho definido ainda
    return repo


def mark_repositories_inactive(
    active_canonical_names: set[str],
    db: Database,
) -> None:
    """Marca como inactive_source_missing repositórios que não apareceram neste ciclo."""
    all_repos = db.get_all_repositories()
    for repo in all_repos:
        if repo.status == REPO_STATUS_ACTIVE and repo.repository_name_canonical not in active_canonical_names:
            logger.info(
                "Repositório '%s' não encontrado na origem. Marcando como inactive_source_missing.",
                repo.repository_name_canonical,
            )
            db.mark_repository_inactive(repo.repository_name_canonical)


def _ensure_directory_structure(canonical: str, cfg: PipelineConfig, output_root_path: str = "") -> None:
    """Cria estrutura de diretórios do repositório no destino (idempotente).

    output_root_path define onde os arquivos organizados (kept) serão gravados.
    Os demais diretórios (duplicates, review, corrupted, sidecars) ficam sempre
    sob cfg.output_root.
    """
    root = cfg.output_root
    organized_base = Path(output_root_path) if output_root_path else root / DIR_ORGANIZED / canonical
    dirs = [
        organized_base / SUBDIR_PHOTOS,
        organized_base / SUBDIR_VIDEOS,
        root / DIR_DUPLICATES / canonical,
        root / DIR_REVIEW / canonical,
        root / DIR_CORRUPTED / canonical,
        root / DIR_SIDECARS / canonical,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
