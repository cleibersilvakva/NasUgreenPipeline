"""Organizer — constrói caminhos de destino e nomes padronizados."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import PipelineConfig
from .constants import (
    DIR_CORRUPTED,
    DIR_DUPLICATES,
    DIR_ORGANIZED,
    DIR_REVIEW,
    FILENAME_DATETIME_FORMAT,
    HASH_SHORT_LENGTH,
    MEDIA_KIND_PHOTO,
    MEDIA_KIND_VIDEO,
    STATUS_CORRUPTED,
    STATUS_DUPLICATE,
    STATUS_KEPT,
    STATUS_REVIEW,
    SUBDIR_PHOTOS,
    SUBDIR_VIDEOS,
)
from .models import Decision, FileInfo
from .month_map import month_label
from .utils import safe_destination_path, sanitize_filename, truncate_filename

logger = logging.getLogger("media_repo_pipeline.organizer")


def build_destination(
    file_info: FileInfo,
    decision: Decision,
    cfg: PipelineConfig,
) -> Path:
    """Constrói o caminho absoluto final para o arquivo."""
    action = decision.action
    repo = file_info.repository_name_canonical

    # Resolve data para estrutura de pastas
    year, month_label_str, day = _resolve_date_parts(file_info, cfg)

    # Nome padronizado do arquivo
    filename = _build_filename(file_info, cfg)

    if action == STATUS_KEPT:
        media_subdir = SUBDIR_PHOTOS if file_info.media_kind == MEDIA_KIND_PHOTO else SUBDIR_VIDEOS
        dest = (
            cfg.output_root / DIR_ORGANIZED / repo / media_subdir
            / year / month_label_str / day / filename
        )
    elif action == STATUS_DUPLICATE:
        dest = (
            cfg.output_root / DIR_DUPLICATES / repo
            / year / month_label_str / day / filename
        )
    elif action == STATUS_REVIEW:
        dest = (
            cfg.output_root / DIR_REVIEW / repo
            / year / month_label_str / day / filename
        )
    elif action == STATUS_CORRUPTED:
        dest = (
            cfg.output_root / DIR_CORRUPTED / repo
            / year / month_label_str / day / filename
        )
    else:
        dest = cfg.output_root / DIR_REVIEW / repo / filename

    # Garante que não sobrescreve
    dest = safe_destination_path(dest)
    decision.destination = str(dest)
    return dest


def _resolve_date_parts(
    file_info: FileInfo, cfg: PipelineConfig
) -> tuple[str, str, str]:
    """Extrai YYYY, MM-NomeDoMes, DD da data de captura."""
    if file_info.capture_dt:
        try:
            dt = datetime.strptime(file_info.capture_dt, "%Y-%m-%d %H:%M:%S")
            return str(dt.year), month_label(dt.month), f"{dt.day:02d}"
        except (ValueError, KeyError):
            pass

    # Fallback: usar mtime
    try:
        tz = ZoneInfo(cfg.default_timezone)
        dt = datetime.fromtimestamp(file_info.mtime_epoch, tz=tz)
        return str(dt.year), month_label(dt.month), f"{dt.day:02d}"
    except (ValueError, KeyError, OSError):
        return "unknown", "00-Desconhecido", "00"


def _build_filename(file_info: FileInfo, cfg: PipelineConfig) -> str:
    """Gera nome padronizado: YYYY-MM-DD_HHMMSS_nomeoriginal_hashcurto.ext"""
    ext = file_info.extension.lower()
    hash_short = file_info.hash_sha256[:HASH_SHORT_LENGTH] if file_info.hash_sha256 else "000000"

    # Data para o nome do arquivo
    dt_str = ""
    if file_info.capture_dt:
        try:
            dt = datetime.strptime(file_info.capture_dt, "%Y-%m-%d %H:%M:%S")
            dt_str = dt.strftime(FILENAME_DATETIME_FORMAT)
        except ValueError:
            pass
    if not dt_str:
        try:
            tz = ZoneInfo(cfg.default_timezone)
            dt = datetime.fromtimestamp(file_info.mtime_epoch, tz=tz)
            dt_str = dt.strftime(FILENAME_DATETIME_FORMAT)
        except (ValueError, OSError):
            dt_str = "0000-00-00_000000"

    # Nome original sanitizado
    original_stem = Path(file_info.source_path).stem
    original_clean = sanitize_filename(original_stem)
    if not original_clean:
        original_clean = "file"

    base_name = f"{dt_str}_{original_clean}_{hash_short}"
    return truncate_filename(base_name, cfg.max_filename_length, ext)
