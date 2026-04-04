"""Extração de metadata via Pillow (fotos) e mutagen (vídeos) — sem dependências externas."""

from __future__ import annotations

import logging
import struct
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import PipelineConfig
from .constants import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    EXIF_DATE_FIELDS_PRIORITY,
    MEDIA_KIND_VIDEO,
)
from .errors import MetadataExtractionError  # noqa: F401 — mantido para compatibilidade

logger = logging.getLogger("media_repo_pipeline.metadata")

# Formatos de data comuns
_EXIF_DATE_FORMATS = (
    "%Y:%m:%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y:%m:%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f%z",
)

# Mapeamento de tag EXIF numérica → nome canônico
_EXIF_TAG_MAP: dict[int, str] = {
    0x9003: "DateTimeOriginal",
    0x9004: "CreateDate",           # DateTimeDigitized
    0x0132: "ModifyDate",           # DateTime
    0x010F: "Make",
    0x0110: "Model",
    0x0131: "Software",
}

# Tags de data adicionais para vídeos via mutagen (MP4/MOV)
_MUTAGEN_DATE_KEYS = ("©day", "date", "\xa9day")


def extract(
    path: Path, media_kind: str, cfg: PipelineConfig
) -> dict[str, Any]:
    """Extrai metadata de um arquivo de mídia.

    Retorna dict com: metadata_json, capture_dt, capture_dt_source,
    capture_dt_confidence, device_make, device_model, software.
    """
    metadata: dict[str, Any] = {}

    # Fotos — Pillow
    if media_kind != MEDIA_KIND_VIDEO:
        metadata = _read_exif_pillow(path)

    # Vídeos — mutagen; também tenta para fotos sem data
    if media_kind == MEDIA_KIND_VIDEO or not _has_useful_date(metadata):
        video_meta = _read_video_mutagen(path)
        if video_meta:
            metadata.update(video_meta)

    capture_dt, source, confidence = _resolve_capture_date(metadata, path, cfg)

    return {
        "metadata_json": metadata,
        "capture_dt": capture_dt,
        "capture_dt_source": source,
        "capture_dt_confidence": confidence,
        "device_make": str(metadata.get("Make", "")).strip(),
        "device_model": str(metadata.get("Model", "")).strip(),
        "software": str(metadata.get("Software", "")).strip(),
    }


# ── Pillow — EXIF de fotos ─────────────────────────────────────────────────────


def _read_exif_pillow(path: Path) -> dict[str, Any]:
    """Lê campos EXIF de fotos via Pillow. Retorna dict vazio em caso de falha."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        # Suporte a HEIC/HEIF via pillow-heif se disponível
        if path.suffix.lower() in (".heic", ".heif"):
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass

        with Image.open(path) as img:
            raw_exif = img._getexif()  # type: ignore[attr-defined]
            if not raw_exif:
                return {}

            result: dict[str, Any] = {}
            for tag_id, value in raw_exif.items():
                name = _EXIF_TAG_MAP.get(tag_id) or TAGS.get(tag_id, str(tag_id))
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="replace")
                    except Exception:
                        value = repr(value)
                result[str(name)] = value
            return result
    except ImportError:
        logger.debug("Pillow não instalado — metadados EXIF não disponíveis para %s", path)
        return {}
    except Exception as exc:
        logger.debug("Falha ao ler EXIF de %s: %s", path, exc)
        return {}


# ── mutagen — metadados de vídeo ──────────────────────────────────────────────


def _read_video_mutagen(path: Path) -> dict[str, Any]:
    """Lê tags de vídeo via mutagen. Retorna dict vazio em caso de falha."""
    try:
        import mutagen

        mf = mutagen.File(path, easy=False)
        if mf is None:
            return {}

        result: dict[str, Any] = {}
        tags = mf.tags
        if tags is None:
            return {}

        # MP4/MOV: tags é um dict-like com chaves como '©day', '\xa9day', etc.
        if hasattr(tags, "items"):
            for k, v in tags.items():
                key = str(k)
                # Converte listas de um elemento para valor simples
                if isinstance(v, list) and len(v) == 1:
                    v = v[0]
                # MP4FreeForm → string
                if hasattr(v, "decode"):
                    try:
                        v = v.decode("utf-8", errors="replace")
                    except Exception:
                        v = str(v)
                result[key] = str(v) if not isinstance(v, (str, int, float)) else v

        # Mapear chaves de data conhecidas para campo canônico
        for key in _MUTAGEN_DATE_KEYS:
            if key in result and "MediaCreateDate" not in result:
                result["MediaCreateDate"] = result[key]

        return result
    except ImportError:
        logger.debug("mutagen não instalado — metadados de vídeo não disponíveis para %s", path)
        return {}
    except Exception as exc:
        logger.debug("Falha ao ler metadados de vídeo de %s: %s", path, exc)
        return {}


# ── Resolução de data ──────────────────────────────────────────────────────────


def _has_useful_date(meta: dict[str, Any]) -> bool:
    return any(meta.get(f) for f in EXIF_DATE_FIELDS_PRIORITY)


def _resolve_capture_date(
    meta: dict[str, Any], path: Path, cfg: PipelineConfig
) -> tuple[str, str, str]:
    """Resolve a data de captura na melhor prioridade disponível."""
    tz = ZoneInfo(cfg.default_timezone)

    # Campos EXIF na ordem de prioridade
    for field_name in EXIF_DATE_FIELDS_PRIORITY:
        raw = meta.get(field_name)
        if raw:
            dt = _parse_date(str(raw), tz)
            if dt:
                confidence = CONFIDENCE_HIGH if field_name in ("DateTimeOriginal", "CreateDate") else CONFIDENCE_MEDIUM
                return dt.strftime("%Y-%m-%d %H:%M:%S"), field_name, confidence

    # mtime como fallback
    try:
        mtime = path.stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S"), "mtime", CONFIDENCE_LOW
    except OSError:
        return "", "fallback", CONFIDENCE_LOW


def _parse_date(raw: str, default_tz: ZoneInfo) -> datetime | None:
    raw = raw.strip()
    if not raw or raw == "0000:00:00 00:00:00":
        return None
    for fmt in _EXIF_DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=default_tz)
            return dt
        except ValueError:
            continue
    return None
