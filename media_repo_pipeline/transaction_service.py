"""Transação lógica segura — copy→tmp, validate, rename, sidecar, DB commit."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from .config import PipelineConfig
from .constants import STATUS_KEPT
from .db import Database
from .dedup_service import compute_sha256
from .errors import CopyError, SidecarError, TransactionError, ValidationError
from .models import Decision, FileInfo, ProcessingResult
from .sidecar_service import generate_sidecar

logger = logging.getLogger("media_repo_pipeline.transaction")


def process_file(
    file_info: FileInfo,
    decision: Decision,
    dest_path: Path,
    db: Database,
    cfg: PipelineConfig,
    tmp_suffix: str = "",
) -> ProcessingResult:
    """Executa a transação lógica completa para um arquivo.

    Fluxo:
    1. Copiar origem para tmp/
    2. Validar tamanho (e hash se kept)
    3. Rename atômico para destino final
    4. Gerar sidecar (se kept e habilitado)
    5. Gravar no banco
    6. Se mode=move, remover origem após sucesso completo

    `tmp_suffix` é um sufixo único por thread para evitar colisões no
    diretório tmp quando workers_count > 1.

    Retorna ProcessingResult.
    """
    result = ProcessingResult(file_info=file_info, decision=decision)
    tmp_dir = cfg.tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)

    source = Path(file_info.source_path)
    # Constrói nome tmp único: stem_suffix.ext (evita colisão entre threads)
    if tmp_suffix:
        tmp_name = f"{dest_path.stem}_{tmp_suffix}{dest_path.suffix}"
    else:
        tmp_name = dest_path.name
    tmp_path = tmp_dir / tmp_name
    renamed = False  # rastreia se o rename já ocorreu

    try:
        # 1. Copiar para tmp
        _safe_copy(source, tmp_path)

        # 2. Validar integridade
        _validate_copy(source, tmp_path, file_info)

        # 3. Rename atômico para destino final
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(tmp_path), str(dest_path))
        renamed = True
        logger.debug("Arquivo movido para destino final: %s", dest_path)

        # 4. Sidecar (apenas para kept)
        sidecar_path = None
        if decision.action == STATUS_KEPT and cfg.sidecar_enabled:
            sidecar_path = generate_sidecar(file_info, dest_path, cfg)

        # 5. Gravar no banco
        _record_to_db(file_info, decision, dest_path, db)

        # 6. Se mode=move, remover origem
        if cfg.mode == "move":
            try:
                source.unlink()
                logger.debug("Origem removida (mode=move): %s", source)
            except OSError as exc:
                logger.warning("Não foi possível remover origem: %s — %s", source, exc)

        result.success = True
        logger.info(
            "✓ [%s] %s → %s",
            decision.action, file_info.rel_input_path, dest_path,
        )

    except (CopyError, ValidationError, SidecarError) as exc:
        result.success = False
        result.error_message = str(exc)
        result.error_type = type(exc).__name__
        logger.error("✗ Falha ao processar %s: %s", source, exc)
        if renamed:
            _cleanup_tmp(dest_path)
        else:
            _cleanup_tmp(tmp_path)

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)
        result.error_type = "UnexpectedError"
        logger.error("✗ Erro inesperado ao processar %s: %s", source, exc)
        if renamed:
            _cleanup_tmp(dest_path)
        else:
            _cleanup_tmp(tmp_path)

    return result


def _safe_copy(src: Path, dst: Path) -> None:
    try:
        shutil.copy2(str(src), str(dst))
    except OSError as exc:
        raise CopyError(f"Falha ao copiar {src} → {dst}: {exc}") from exc


def _validate_copy(src: Path, tmp: Path, file_info: FileInfo) -> None:
    # Verificar tamanho
    try:
        tmp_size = tmp.stat().st_size
    except OSError as exc:
        raise ValidationError(f"Não foi possível verificar tmp: {exc}") from exc

    if tmp_size != file_info.size_bytes:
        raise ValidationError(
            f"Tamanho divergente: origem={file_info.size_bytes}, tmp={tmp_size}"
        )

    # Verificar hash pós-cópia
    if file_info.hash_sha256:
        tmp_hash = compute_sha256(tmp)
        if tmp_hash != file_info.hash_sha256:
            raise ValidationError(
                f"Hash divergente pós-cópia: origem={file_info.hash_sha256[:12]}, tmp={tmp_hash[:12]}"
            )


def _record_to_db(
    fi: FileInfo, decision: Decision, dest: Path, db: Database
) -> None:
    """Grava todas as tabelas em UMA única transação atômica.

    Usa db.record_file_transaction para evitar contenção de lock SQLite em
    execuções multi-thread (substitui 3 commits separados por 1).
    """
    metadata_json = json.dumps(fi.metadata_json, default=str) if fi.metadata_json else None

    file_data = {
        "source_path": fi.source_path,
        "repository_name_canonical": fi.repository_name_canonical,
        "rel_input_path": fi.rel_input_path,
        "extension": fi.extension,
        "media_kind": fi.media_kind,
        "size_bytes": fi.size_bytes,
        "mtime_epoch": fi.mtime_epoch,
        "ctime_epoch": fi.ctime_epoch,
        "hash_sha256": fi.hash_sha256,
        "capture_dt": fi.capture_dt,
        "capture_dt_source": fi.capture_dt_source,
        "capture_dt_confidence": fi.capture_dt_confidence,
        "status": decision.action,
        "reason": decision.reason,
        "destination_path": str(dest),
        "duplicate_of_hash": decision.duplicate_of_hash,
        "metadata_json": metadata_json,
    }

    kept_data = None
    if decision.action == STATUS_KEPT:
        kept_data = {
            "repository_name_canonical": fi.repository_name_canonical,
            "hash_sha256": fi.hash_sha256,
            "canonical_destination_path": str(dest),
            "media_kind": fi.media_kind,
            "extension": fi.extension,
            "size_bytes": fi.size_bytes,
            "capture_dt": fi.capture_dt,
            "capture_dt_source": fi.capture_dt_source,
            "metadata_json": metadata_json,
        }

    state_data = {
        "source_path": fi.source_path,
        "repository_name_canonical": fi.repository_name_canonical,
        "size_bytes": fi.size_bytes,
        "mtime_epoch": fi.mtime_epoch,
        "last_hash_sha256": fi.hash_sha256,
        "last_status": decision.action,
        "retry_count": decision.processing_retry_count,
    }

    db.record_file_transaction(file_data, kept_data, state_data)


def _cleanup_tmp(tmp_path: Path) -> None:
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except OSError:
        pass
