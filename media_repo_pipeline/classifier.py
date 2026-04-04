"""Classificador de mídia e decisão de destino."""

from __future__ import annotations

import logging
from pathlib import Path

from .constants import (
    JPG_EXTENSIONS,
    MEDIA_KIND_OTHER,
    MEDIA_KIND_PHOTO,
    MEDIA_KIND_VIDEO,
    PHOTO_EXTENSIONS,
    RAW_EXTENSIONS,
    STATUS_CORRUPTED,
    STATUS_DUPLICATE,
    STATUS_KEPT,
    STATUS_REVIEW,
    VIDEO_EXTENSIONS,
)
from .models import Decision, FileInfo

logger = logging.getLogger("media_repo_pipeline.classifier")


def classify_media_kind(ext: str) -> str:
    """Classifica o tipo de mídia pela extensão."""
    ext = ext.lower()
    if ext in PHOTO_EXTENSIONS:
        return MEDIA_KIND_PHOTO
    if ext in VIDEO_EXTENSIONS:
        return MEDIA_KIND_VIDEO
    return MEDIA_KIND_OTHER


def decide(
    file_info: FileInfo,
    is_duplicate: bool,
    duplicate_ref: dict | None = None,
) -> Decision:
    """Determina a ação para o arquivo.

    Retorna objeto Decision com action, reason, etc.
    """
    # Duplicata
    if is_duplicate and duplicate_ref:
        return Decision(
            action=STATUS_DUPLICATE,
            reason=f"Duplicata de hash {file_info.hash_sha256[:8]} no repositório {file_info.repository_name_canonical}",
            duplicate_of_hash=file_info.hash_sha256,
        )

    # Arquivo corrupto (sem metadata e sem data)
    if not file_info.capture_dt and file_info.capture_dt_source == "fallback":
        return Decision(
            action=STATUS_REVIEW,
            reason="Sem data de captura confiável",
            requires_review=True,
        )

    # Aceito
    return Decision(
        action=STATUS_KEPT,
        reason="Arquivo aceito para organização",
    )


def check_raw_jpg_conflict(
    file_info: FileInfo,
    kept_files_in_repo: list[dict],
) -> Decision | None:
    """Verifica conflito RAW/JPG dentro do mesmo repositório.

    Se JPG tem RAW correlato já aceito, encaminha JPG para review.
    Se RAW correlato chega após JPG já aceito, aceita RAW e registra.
    """
    ext = file_info.extension.lower()
    stem = Path(file_info.base_name_normalized).stem.lower()

    is_jpg = ext in JPG_EXTENSIONS
    is_raw = ext in RAW_EXTENSIONS

    if not (is_jpg or is_raw):
        return None

    for kept in kept_files_in_repo:
        kept_ext = kept.get("extension", "").lower()
        kept_dest = kept.get("canonical_destination_path", "")
        kept_stem = Path(kept_dest).stem.lower() if kept_dest else ""

        # Correlação por stem normalizado
        if not _stems_match(stem, kept_stem):
            continue

        if is_jpg and kept_ext in RAW_EXTENSIONS:
            return Decision(
                action=STATUS_REVIEW,
                reason=f"JPG com RAW correlato já aceito ({kept_ext})",
                requires_review=True,
            )

        if is_raw and kept_ext in JPG_EXTENSIONS:
            logger.info(
                "RAW (%s) posterior a JPG já aceito. Aceitando RAW e registrando conflito.",
                file_info.source_path,
            )
            return None  # Aceitar RAW normalmente

    return None


def _stems_match(stem_a: str, stem_b: str) -> bool:
    """Verifica se stems são compatíveis (heurística simples)."""
    # Remove prefixo de data do nome padronizado se existir
    import re
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_")
    clean_a = pattern.sub("", stem_a)
    clean_b = pattern.sub("", stem_b)
    # Remove hash curto final (últimos 7 chars após último _)
    clean_a = re.sub(r"_[a-f0-9]{6,8}$", "", clean_a)
    clean_b = re.sub(r"_[a-f0-9]{6,8}$", "", clean_b)
    return clean_a == clean_b and clean_a != ""
