"""Transação lógica segura — copy→tmp, validate, rename, sidecar, DB commit."""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import PipelineConfig
from .constants import STATUS_KEPT
from .db import Database
from .dedup_service import compute_sha256
from .errors import CopyError, SidecarError, TransactionError, ValidationError
from .models import Decision, FileInfo, ProcessingResult
from .sidecar_service import generate_sidecar

logger = logging.getLogger("media_repo_pipeline.transaction")


@dataclass
class PendingWrite:
    """Dados necessários para gravar no banco após a fase de I/O.

    Produzido pelos workers e consumido exclusivamente pelo writer thread,
    eliminando contenção de lock no SQLite em execuções multi-thread.
    """

    file_info: FileInfo
    decision: Decision
    dest_path: Path
    source_path: Path
    cfg_mode: str  # 'copy' | 'move'


def process_file_io(
    file_info: FileInfo,
    decision: Decision,
    dest_path: Path,
    cfg: PipelineConfig,
    tmp_suffix: str = "",
) -> tuple[ProcessingResult, PendingWrite | None]:
    """Fase de I/O: copia, valida, renomeia e gera sidecar. NÃO toca o banco.

    Retorna (result, pending). pending é None se houve falha — nenhuma escrita
    será feita no banco para este arquivo.
    """
    result = ProcessingResult(file_info=file_info, decision=decision)
    tmp_dir = cfg.tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)

    source = Path(file_info.source_path)
    tmp_name = f"{dest_path.stem}_{tmp_suffix}{dest_path.suffix}" if tmp_suffix else dest_path.name
    tmp_path = tmp_dir / tmp_name
    renamed = False

    try:
        _safe_copy(source, tmp_path)
        _validate_copy(source, tmp_path, file_info)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(tmp_path), str(dest_path))
        renamed = True
        logger.debug("Arquivo movido para destino final: %s", dest_path)

        if decision.action == STATUS_KEPT and cfg.sidecar_enabled:
            generate_sidecar(file_info, dest_path, cfg)

        pending = PendingWrite(
            file_info=file_info,
            decision=decision,
            dest_path=dest_path,
            source_path=source,
            cfg_mode=cfg.mode,
        )
        return result, pending

    except (CopyError, ValidationError, SidecarError) as exc:
        result.success = False
        result.error_message = str(exc)
        result.error_type = type(exc).__name__
        logger.error("✗ Falha ao processar %s: %s", source, exc)
        _cleanup_tmp(dest_path if renamed else tmp_path)
        return result, None

    except Exception as exc:
        result.success = False
        result.error_message = str(exc)
        result.error_type = "UnexpectedError"
        logger.error("✗ Erro inesperado ao processar %s: %s", source, exc)
        _cleanup_tmp(dest_path if renamed else tmp_path)
        return result, None


def commit_pending_write(pending: PendingWrite, db: Database) -> None:
    """Grava no banco e faz unlink da origem se mode=move.

    Sempre chamado pelo writer thread (single-threaded) — zero contenção de lock.
    """
    _record_to_db(pending.file_info, pending.decision, pending.dest_path, db)

    if pending.cfg_mode == "move":
        try:
            pending.source_path.unlink()
            logger.debug("Origem removida (mode=move): %s", pending.source_path)
        except OSError as exc:
            logger.warning("Não foi possível remover origem: %s — %s", pending.source_path, exc)

    logger.info(
        "✓ [%s] %s → %s",
        pending.decision.action, pending.file_info.rel_input_path, pending.dest_path,
    )


def process_file(
    file_info: FileInfo,
    decision: Decision,
    dest_path: Path,
    db: Database,
    cfg: PipelineConfig,
    tmp_suffix: str = "",
) -> ProcessingResult:
    """Versão completa (I/O + DB) para retrocompatibilidade com testes e CLI.

    Em produção prefer process_file_io + commit_pending_write separados.
    """
    result, pending = process_file_io(file_info, decision, dest_path, cfg, tmp_suffix)
    if pending is not None:
        try:
            commit_pending_write(pending, db)
            result.success = True
        except Exception as exc:
            result.success = False
            result.error_message = str(exc)
            result.error_type = "DBWriteError"
            logger.error("✗ Falha na escrita ao banco para %s: %s", file_info.source_path, exc)
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
