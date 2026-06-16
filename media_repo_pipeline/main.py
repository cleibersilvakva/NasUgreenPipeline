"""Ponto de entrada principal — loop do pipeline e CLI."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .classifier import check_raw_jpg_conflict, classify_media_kind, decide
from .config import PipelineConfig, load_config
from .constants import STATUS_ERROR, STATUS_SKIPPED, SUPPORTED_EXTENSIONS
from .db import Database
from .dedup_service import compute_sha256, find_duplicate
from .errors import HashComputationError, HealthCheckError, LockAcquisitionError, PipelineError
from .file_stability import is_file_stable
from .healthcheck import run_healthcheck
from .lock_service import LockService
from .logging_setup import setup_logging
from .metadata_service import extract as extract_metadata
from .models import Decision, FileInfo, ProcessingResult
from .organizer import build_destination
from .profiler import NullTimer, PhaseTimer, TimerProtocol
from .reconciler import reconcile
from .reporting import RunReport
from .repository_service import ensure_repository, mark_repositories_inactive
from .scanner import discover_repositories, scan_files
from .transaction_service import PendingWrite, commit_pending_write, process_file, process_file_io
from .utils import canonicalize_repo_name

logger: logging.Logger | None = None

_shutdown_requested = False


def _signal_handler(signum: int, frame) -> None:  # type: ignore[no-untyped-def]
    global _shutdown_requested
    _shutdown_requested = True
    if logger:
        logger.info("Sinal %d recebido. Finalizando após o ciclo atual…", signum)


def main(argv: list[str] | None = None) -> int:
    global logger

    args = _parse_args(argv)
    cfg = load_config(
        config_path=args.config,
        cli_overrides={
            "input_root": args.input_root,
            "output_root": args.output_root,
            "sqlite_db_path": args.db_path,
            "mode": args.mode,
            "run_once": args.once,
            "scan_interval_seconds": args.scan_interval,
            "verbose": args.verbose,
            "dry_run": args.dry_run,
            "reconcile_only": args.reconcile_only,
        },
    )

    logger = setup_logging(cfg.logs_dir, cfg.verbose)
    logger.info("Media Repository Pipeline %s iniciando…", __version__)

    # Registrar handlers de sinal
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Healthcheck
    try:
        run_healthcheck(cfg)
    except HealthCheckError as exc:
        logger.error("Healthcheck falhou: %s", exc)
        return 1

    # Lock
    lock = LockService(cfg.output_root / "pipeline.lock")
    try:
        lock.acquire()
    except LockAcquisitionError as exc:
        logger.error("%s", exc)
        return 1

    # Banco
    db = Database(cfg.sqlite_db_path)  # type: ignore[arg-type]
    db.open()

    try:
        if cfg.reconcile_only:
            logger.info("Executando apenas reconciliação…")
            issues = reconcile(db, cfg)
            for issue in issues:
                logger.warning("Inconsistência: %s", issue)
            return 0

        # Loop principal
        while not _shutdown_requested:
            _run_cycle(db, cfg)

            if cfg.run_once:
                logger.info("Modo --once: encerrando após um ciclo.")
                break

            logger.info("Aguardando %d segundos para o próximo ciclo…", cfg.scan_interval_seconds)
            for _ in range(cfg.scan_interval_seconds):
                if _shutdown_requested:
                    break
                time.sleep(1)

    except Exception as exc:
        logger.error("Erro fatal no loop principal: %s", exc, exc_info=True)
        return 1
    finally:
        db.close()
        lock.release()
        logger.info("Pipeline encerrado.")

    return 0


def _run_cycle(db: Database, cfg: PipelineConfig) -> None:
    """Executa um ciclo completo do scanner."""
    assert logger is not None
    import queue as _queue_module
    import threading
    import uuid
    from concurrent.futures import ThreadPoolExecutor, as_completed

    run_id = db.start_run(cfg.mode, cfg.pipeline_version, cfg.policy_version)
    report = RunReport(run_id, cfg)
    report_lock = threading.Lock()
    timer: TimerProtocol = PhaseTimer() if cfg.profile_enabled else NullTimer()

    logger.info("═══ Ciclo %d iniciado (workers=%d) ═══", run_id, cfg.workers_count)

    # 1. Descobrir repositórios e coletar todos os arquivos
    repos = discover_repositories(cfg)
    active_names: set[str] = set()

    # Lista de trabalho: (file_path, source_path, repo_record)
    work_items: list[tuple[Path, Path, object]] = []

    for raw_name, source_path in repos:
        canonical = canonicalize_repo_name(raw_name)
        active_names.add(canonical)
        repo_record = ensure_repository(raw_name, source_path, db, cfg)

        if not repo_record.output_root_path:
            logger.warning(
                "Repositório '%s' aguarda configuração de pasta de destino — "
                "acesse o dashboard para configurar antes de executar.",
                raw_name,
            )
            continue

        files = scan_files(source_path, cfg)
        n_files = len(files)
        with report_lock:
            report.increment("seen", n_files)
        logger.info("  📂 %s: %d arquivo(s) encontrado(s)", raw_name, n_files)

        for file_path in files:
            if _shutdown_requested:
                break
            work_items.append((file_path, source_path, repo_record))

    total_work = len(work_items)
    logger.info("Total a processar neste ciclo: %d arquivo(s)", total_work)
    if total_work == 0:
        logger.info("Nenhum arquivo novo ou modificado. Aguardando próximo ciclo.")
        db.finish_run(run_id, report.stats)
        return

    # Snapshot leve (sem metadata_json) para check RAW/JPG — reduz uso de RAM
    _SUBMIT_CHUNK = 500
    logger.info("⏳ Carregando snapshot do banco…")
    kept_files_snapshot = db.get_kept_files_snapshot()
    n_lotes = -(-total_work // _SUBMIT_CHUNK)
    logger.info("▶ Snapshot pronto (%d registros). Iniciando processamento em %d lote(s)…", len(kept_files_snapshot), n_lotes)

    # 2. Writer thread — única thread que escreve no banco, zero contenção de lock.
    #    Workers fazem I/O (cópia, hash, sidecar) em paralelo e empurram PendingWrite
    #    para esta fila; o writer thread consome e commita serialmente.
    _SENTINEL = object()
    write_queue: _queue_module.Queue = _queue_module.Queue()

    def _writer_thread_fn() -> None:
        writer_db = Database(cfg.sqlite_db_path)  # type: ignore[arg-type]
        writer_db.open()
        writer_completed = 0
        try:
            while True:
                item = write_queue.get()
                if item is _SENTINEL:
                    write_queue.task_done()
                    break

                result, pending = item
                if pending is not None:
                    try:
                        commit_pending_write(pending, writer_db)
                        result.success = True
                    except Exception as exc:
                        result.success = False
                        result.error_message = str(exc)
                        result.error_type = "DBWriteError"
                        logger.error(
                            "✗ Falha na escrita ao banco para %s: %s",
                            pending.file_info.source_path, exc,
                        )

                with report_lock:
                    report.add_result(result)
                    writer_completed += 1
                    if writer_completed % 100 == 0:
                        s = report.stats
                        logger.info(
                            "  ⚙ Progresso: %d/%d | kept=%d dup=%d rev=%d err=%d",
                            writer_completed, total_work,
                            s.get("kept", 0), s.get("duplicate", 0),
                            s.get("review", 0), s.get("corrupted", 0),
                        )
                write_queue.task_done()
        finally:
            writer_db.close()

    writer = threading.Thread(target=_writer_thread_fn, daemon=True, name="db-writer")
    writer.start()

    # 3. Workers — apenas I/O (cópia, hash, sidecar). Leituras ao banco são ok em paralelo.
    max_workers = max(1, cfg.workers_count)

    def _worker(args: tuple) -> tuple[ProcessingResult, PendingWrite | None]:
        """Fase de I/O: cada thread abre conexão somente para leituras."""
        file_path, source_path, repo_record = args
        thread_db = Database(cfg.sqlite_db_path)  # type: ignore[arg-type]
        thread_db.open()
        thread_tmp_suffix = uuid.uuid4().hex[:8]
        try:
            return _process_single_file_io(
                file_path, source_path, repo_record,
                thread_db, cfg, kept_files_snapshot, thread_tmp_suffix,
                timer=timer,
            )
        finally:
            thread_db.close()

    # Processa em lotes para evitar alocar dezenas de milhares de futures de uma vez
    # (OOM em NAS/ARM com muitos arquivos).
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        import itertools as _itertools
        items_iter = iter(work_items)
        while not _shutdown_requested:
            chunk = list(_itertools.islice(items_iter, _SUBMIT_CHUNK))
            if not chunk:
                break
            futures = {executor.submit(_worker, item): item[0] for item in chunk}
            for future in as_completed(futures):
                if _shutdown_requested:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    result, pending = future.result()
                    write_queue.put((result, pending))
                except Exception as exc:
                    fp = futures[future]
                    logger.error("Erro inesperado em thread para %s: %s", fp, exc, exc_info=True)

    # Aguarda o writer terminar todos os commits antes de prosseguir
    write_queue.put(_SENTINEL)
    write_queue.join()
    writer.join()

    # 4. Marcar repos ausentes como inativo
    mark_repositories_inactive(active_names, db)

    # 5. Fechar run
    db.finish_run(run_id, report.stats)

    # 6. Gerar CSV e resumo
    report.generate_csv()
    disk_check = report.verify_disk_count()
    if not disk_check["ok"]:
        logger.warning(
            "⚠ Integridade em disco: kept=%d, em disco=%d, ausentes=%d, tamanho errado=%d",
            disk_check["kept_in_db"],
            disk_check["present_on_disk"],
            len(disk_check["missing"]),
            len(disk_check["size_mismatch"]),
        )
    summary = report.summary()
    logger.info("\n%s", summary)

    if cfg.profile_enabled:
        logger.info("\n%s", timer.summary())


def _process_single_file_io(
    file_path: Path,
    repo_source_path: Path,
    repo_record,
    db: Database,
    cfg: PipelineConfig,
    kept_files_snapshot: list,
    tmp_suffix: str = "",
    timer: TimerProtocol | None = None,
) -> tuple[ProcessingResult, PendingWrite | None]:
    """Fase de I/O de um único arquivo — lógica + cópia + sidecar. Não grava no banco.

    db é usado exclusivamente para leituras (source_state, kept_files).
    Retorna (result, pending): pending=None se não há escrita necessária
    (skip, erro de I/O, dry_run).
    """
    assert logger is not None
    canonical = repo_record.repository_name_canonical
    if timer is None:
        timer = NullTimer()

    # Construir FileInfo básico
    with timer.phase("stat"):
        try:
            st = file_path.stat()
        except OSError as exc:
            logger.warning("Não foi possível acessar %s: %s", file_path, exc)
            return ProcessingResult(success=False, error_message=str(exc), error_type="OSError"), None

    ext = file_path.suffix.lower()
    rel_input = str(file_path.relative_to(repo_source_path))

    fi = FileInfo(
        source_path=str(file_path),
        rel_input_path=rel_input,
        repository_name_canonical=canonical,
        repository_display_name=repo_record.display_name,
        extension=ext,
        size_bytes=st.st_size,
        mtime_epoch=st.st_mtime,
        ctime_epoch=st.st_ctime,
    )

    # 1. Extensão suportada — já filtrado pelo scanner, mas check extra
    supported = cfg.supported_extensions or SUPPORTED_EXTENSIONS
    if ext not in supported:
        return ProcessingResult(
            success=True, file_info=fi,
            decision=Decision(action=STATUS_SKIPPED, reason="Extensão não suportada"),
        ), None

    # 2. Verificar source_state — arquivo inalterado? (leitura ok em paralelo)
    with timer.phase("source_state_lookup"):
        ss = db.get_source_state(str(file_path))
    if ss and ss["size_bytes"] == fi.size_bytes and ss["mtime_epoch"] == fi.mtime_epoch:
        if ss["last_status"] not in (STATUS_ERROR,):
            return ProcessingResult(
                success=True, file_info=fi,
                decision=Decision(action=STATUS_SKIPPED, reason="Inalterado desde última execução"),
            ), None

    # 3. Estabilidade
    if not cfg.dry_run:
        with timer.phase("stability_check"):
            stable = is_file_stable(file_path, cfg)
        if not stable:
            logger.debug("Arquivo instável (em cópia?): %s", file_path)
            return ProcessingResult(
                success=False, file_info=fi,
                error_message="Arquivo instável", error_type="FileStabilityError",
            ), None

    # 4. Classificar mídia
    fi.media_kind = classify_media_kind(ext)

    # 5. Extrair metadata
    with timer.phase("metadata_extract"):
        try:
            meta = extract_metadata(file_path, fi.media_kind, cfg)
            fi.capture_dt = meta["capture_dt"]
            fi.capture_dt_source = meta["capture_dt_source"]
            fi.capture_dt_confidence = meta["capture_dt_confidence"]
            fi.device_make = meta["device_make"]
            fi.device_model = meta["device_model"]
            fi.software = meta["software"]
            fi.metadata_json = meta["metadata_json"]
        except Exception as exc:
            logger.warning("Falha de metadata para %s: %s", file_path, exc)

    # 6. Calcular hash
    with timer.phase("hash_sha256_source", bytes_processed=fi.size_bytes):
        try:
            fi.hash_sha256 = compute_sha256(file_path)
        except HashComputationError as exc:
            logger.error("Falha ao calcular hash: %s", exc)
            return ProcessingResult(
                success=False, file_info=fi,
                error_message=str(exc), error_type="HashComputationError",
            ), None

    # 7. Nome normalizado
    fi.base_name_normalized = file_path.stem

    # 8. Deduplicação — leitura ok em paralelo (kept_files é imutável no ciclo)
    with timer.phase("dedup_lookup"):
        dup_ref = find_duplicate(canonical, fi.hash_sha256, db)
    is_dup = dup_ref is not None

    # 9. RAW/JPG — snapshot pré-calculado, thread-safe
    with timer.phase("raw_jpg_check"):
        raw_decision = check_raw_jpg_conflict(fi, kept_files_snapshot)
    decision = raw_decision if raw_decision else decide(fi, is_dup, dup_ref)

    # 10. Construir caminho de destino
    dest_path = build_destination(fi, decision, cfg, repo_output_root=repo_record.output_root_path)

    # 11. Dry-run → sem I/O, sem DB
    if cfg.dry_run:
        logger.info("[DRY-RUN] %s → %s (%s)", fi.rel_input_path, dest_path, decision.action)
        return ProcessingResult(success=True, file_info=fi, decision=decision), None

    # 12. Fase de I/O (cópia, validate, rename, sidecar) — sem DB
    return process_file_io(fi, decision, dest_path, cfg, tmp_suffix=tmp_suffix, timer=timer)



def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="media_repo_pipeline",
        description="Pipeline de ingestão e consolidação de mídia.",
    )
    parser.add_argument("--config", type=str, default=None, help="Caminho para arquivo de configuração YAML.")
    parser.add_argument("--input-root", type=str, default=None, dest="input_root")
    parser.add_argument("--output-root", type=str, default=None, dest="output_root")
    parser.add_argument("--db-path", type=str, default=None, dest="db_path")
    parser.add_argument("--mode", choices=["copy", "move"], default=None)
    parser.add_argument("--once", action="store_true", default=False, help="Executa apenas um ciclo.")
    parser.add_argument("--scan-interval", type=int, default=None, dest="scan_interval")
    parser.add_argument("--verbose", action="store_true", default=False)
    parser.add_argument("--reconcile-only", action="store_true", default=False, dest="reconcile_only")
    parser.add_argument("--dry-run", action="store_true", default=False, dest="dry_run")
    return parser.parse_args(argv)


def __main_entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    __main_entry()
