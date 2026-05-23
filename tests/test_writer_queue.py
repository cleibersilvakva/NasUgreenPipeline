"""Testes para a arquitetura de fila de escrita única (single writer thread)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.constants import MEDIA_KIND_PHOTO, STATUS_DUPLICATE, STATUS_KEPT
from media_repo_pipeline.db import Database
from media_repo_pipeline.models import Decision, FileInfo, ProcessingResult
from media_repo_pipeline.repository_service import ensure_repository
from media_repo_pipeline.dedup_service import compute_sha256
from media_repo_pipeline.transaction_service import (
    PendingWrite,
    commit_pending_write,
    process_file,
    process_file_io,
)
from tests.conftest import create_fake_image


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fi(source: Path, canonical: str = "repo") -> FileInfo:
    st = source.stat()
    return FileInfo(
        source_path=str(source),
        rel_input_path=source.name,
        repository_name_canonical=canonical,
        repository_display_name=canonical,
        extension=source.suffix.lower(),
        media_kind=MEDIA_KIND_PHOTO,
        size_bytes=st.st_size,
        mtime_epoch=st.st_mtime,
        ctime_epoch=st.st_ctime,
        hash_sha256="aabbcc001122",
        capture_dt="2026-05-22 10:00:00",
        base_name_normalized=source.stem,
    )


# ── PendingWrite / commit_pending_write ───────────────────────────────────────


class TestCommitPendingWrite:
    def test_writes_to_db(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """commit_pending_write deve gravar o arquivo no banco."""
        source = tmp_workspace["input"] / "foto.jpg"
        dest = tmp_workspace["output"] / "organized" / "repo" / "foto.jpg"
        create_fake_image(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(source, dest)

        fi = _make_fi(source)
        decision = Decision(action=STATUS_KEPT, reason="teste")
        pending = PendingWrite(
            file_info=fi,
            decision=decision,
            dest_path=dest,
            source_path=source,
            cfg_mode="copy",
        )

        commit_pending_write(pending, db)

        row = db.conn.execute(
            "SELECT status FROM files WHERE source_path = ?", (str(source),)
        ).fetchone()
        assert row is not None
        assert row["status"] == STATUS_KEPT

    def test_copy_mode_does_not_unlink_source(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """mode=copy não deve remover o arquivo de origem."""
        source = tmp_workspace["input"] / "foto.jpg"
        dest = tmp_workspace["output"] / "foto.jpg"
        create_fake_image(source)
        import shutil
        shutil.copy2(source, dest)

        fi = _make_fi(source)
        pending = PendingWrite(
            file_info=fi,
            decision=Decision(action=STATUS_KEPT),
            dest_path=dest,
            source_path=source,
            cfg_mode="copy",
        )
        commit_pending_write(pending, db)

        assert source.exists(), "Origem não deve ser removida em mode=copy"

    def test_move_mode_unlinks_source(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """mode=move deve remover o arquivo de origem após gravar no banco."""
        source = tmp_workspace["input"] / "foto.jpg"
        dest = tmp_workspace["output"] / "foto.jpg"
        create_fake_image(source)
        import shutil
        shutil.copy2(source, dest)

        fi = _make_fi(source)
        pending = PendingWrite(
            file_info=fi,
            decision=Decision(action=STATUS_KEPT),
            dest_path=dest,
            source_path=source,
            cfg_mode="move",
        )
        commit_pending_write(pending, db)

        assert not source.exists(), "Origem deve ser removida em mode=move"

    def test_duplicate_written_to_db(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Duplicatas também devem ser registradas no banco."""
        source = tmp_workspace["input"] / "dup.jpg"
        dest = tmp_workspace["output"] / "duplicates" / "dup.jpg"
        create_fake_image(source)
        dest.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(source, dest)

        fi = _make_fi(source)
        decision = Decision(action=STATUS_DUPLICATE, reason="hash igual")
        pending = PendingWrite(
            file_info=fi, decision=decision,
            dest_path=dest, source_path=source, cfg_mode="copy",
        )
        commit_pending_write(pending, db)

        row = db.conn.execute(
            "SELECT status FROM files WHERE source_path = ?", (str(source),)
        ).fetchone()
        assert row is not None
        assert row["status"] == STATUS_DUPLICATE


# ── process_file_io ───────────────────────────────────────────────────────────


class TestProcessFileIo:
    def test_success_returns_pending_write(self, cfg: PipelineConfig, tmp_workspace: dict):
        """I/O bem-sucedido deve retornar PendingWrite não-nulo."""
        source = tmp_workspace["input"] / "foto.jpg"
        dest = tmp_workspace["output"] / "organized" / "foto_dest.jpg"
        create_fake_image(source, b"unique-content-xyz")
        dest.parent.mkdir(parents=True, exist_ok=True)

        fi = _make_fi(source)
        fi.hash_sha256 = compute_sha256(source)
        decision = Decision(action=STATUS_KEPT)

        result, pending = process_file_io(fi, decision, dest, cfg)

        assert result.success is False  # ainda não commitado
        assert pending is not None
        assert pending.cfg_mode == cfg.mode
        assert dest.exists()  # arquivo foi copiado

    def test_io_failure_returns_none_pending(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Falha de I/O (arquivo fonte inexistente) retorna pending=None."""
        fi = FileInfo(
            source_path="/nao/existe/foto.jpg",
            rel_input_path="foto.jpg",
            repository_name_canonical="repo",
            extension=".jpg",
            media_kind=MEDIA_KIND_PHOTO,
            size_bytes=100,
            mtime_epoch=0.0,
            ctime_epoch=0.0,
            hash_sha256="aabb",
        )
        dest = tmp_workspace["output"] / "dest.jpg"
        decision = Decision(action=STATUS_KEPT)

        result, pending = process_file_io(fi, decision, dest, cfg)

        assert pending is None
        assert result.success is False

    def test_process_file_backward_compat(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """process_file (API legada) ainda deve funcionar com result.success=True."""
        source = tmp_workspace["input"] / "compat.jpg"
        dest = tmp_workspace["output"] / "compat_dest.jpg"
        create_fake_image(source, b"compat-content")
        dest.parent.mkdir(parents=True, exist_ok=True)

        fi = _make_fi(source)
        fi.hash_sha256 = compute_sha256(source)
        decision = Decision(action=STATUS_KEPT)

        result = process_file(fi, decision, dest, db, cfg)

        assert result.success is True
        row = db.conn.execute(
            "SELECT status FROM files WHERE source_path = ?", (str(source),)
        ).fetchone()
        assert row is not None


# ── Integração: single writer thread elimina lock contention ──────────────────


class TestSingleWriterConcurrency:
    def _setup_repo(self, db, cfg, tmp_workspace, n_files: int) -> tuple:
        """Cria repositório configurado e N arquivos de teste."""
        source = tmp_workspace["input"] / "album"
        source.mkdir(exist_ok=True)
        custom_root = tmp_workspace["output"] / "album-destino"

        ensure_repository("album", source, db, cfg)
        db.set_repository_output_root("album", str(custom_root))

        for i in range(n_files):
            content = f"conteudo-unico-{i}-{'x'*100}".encode()
            create_fake_image(source / f"foto_{i:03d}.jpg", content)

        return source, custom_root

    def test_all_files_processed_with_4_workers(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Com 4 workers e writer único, todos os arquivos devem ser processados sem lock."""
        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        from media_repo_pipeline.main import _run_cycle

        cfg_multi = PipelineConfig(
            input_root=cfg.input_root,
            output_root=cfg.output_root,
            sqlite_db_path=cfg.sqlite_db_path,
            mode="copy",
            workers_count=4,
            scan_interval_seconds=1,
            stable_check_interval_seconds=0,
            stable_check_count=1,
            max_retries_processing=1,
            low_disk_space_threshold_bytes=0,
            run_once=True,
        )

        n_files = 40
        source, custom_root = self._setup_repo(db, cfg_multi, tmp_workspace, n_files)

        # Não deve lançar exceção de lock
        _run_cycle(db, cfg_multi)

        # Todos os arquivos devem ter sido processados
        kept_count = db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status = 'kept'"
        ).fetchone()[0]
        assert kept_count == n_files, f"Esperado {n_files} kept, obtido {kept_count}"

    def test_no_duplicate_db_entries_with_concurrent_workers(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Nenhum arquivo deve ser gravado duas vezes no banco."""
        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        from media_repo_pipeline.main import _run_cycle

        cfg_multi = PipelineConfig(
            input_root=cfg.input_root,
            output_root=cfg.output_root,
            sqlite_db_path=cfg.sqlite_db_path,
            mode="copy",
            workers_count=4,
            scan_interval_seconds=1,
            stable_check_interval_seconds=0,
            stable_check_count=1,
            max_retries_processing=1,
            low_disk_space_threshold_bytes=0,
            run_once=True,
        )

        n_files = 30
        source, _ = self._setup_repo(db, cfg_multi, tmp_workspace, n_files)

        _run_cycle(db, cfg_multi)

        # Contar entradas únicas por source_path
        total = db.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        unique = db.conn.execute(
            "SELECT COUNT(DISTINCT source_path) FROM files"
        ).fetchone()[0]
        assert total == unique, "Nenhum source_path deve ser gravado duas vezes"

    def test_writer_thread_serializes_writes(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Writer thread deve ser a única thread gravando no banco (sem concurrent writes)."""
        import queue as _queue
        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        # Rastrear quantos commits simultâneos ocorrem
        concurrent_writes = [0]
        max_concurrent = [0]
        lock = threading.Lock()

        original_commit_pending = __import__(
            "media_repo_pipeline.transaction_service",
            fromlist=["commit_pending_write"]
        ).commit_pending_write

        def patched_commit(pending, writer_db):
            with lock:
                concurrent_writes[0] += 1
                if concurrent_writes[0] > max_concurrent[0]:
                    max_concurrent[0] = concurrent_writes[0]
            try:
                original_commit_pending(pending, writer_db)
            finally:
                with lock:
                    concurrent_writes[0] -= 1

        import media_repo_pipeline.transaction_service as _ts
        _ts.commit_pending_write = patched_commit
        try:
            from media_repo_pipeline.main import _run_cycle

            cfg_multi = PipelineConfig(
                input_root=cfg.input_root,
                output_root=cfg.output_root,
                sqlite_db_path=cfg.sqlite_db_path,
                mode="copy",
                workers_count=4,
                scan_interval_seconds=1,
                stable_check_interval_seconds=0,
                stable_check_count=1,
                max_retries_processing=1,
                low_disk_space_threshold_bytes=0,
                run_once=True,
            )

            source, _ = self._setup_repo(db, cfg_multi, tmp_workspace, 20)
            _run_cycle(db, cfg_multi)

            # Nunca mais de 1 commit simultâneo
            assert max_concurrent[0] <= 1, (
                f"max_concurrent={max_concurrent[0]}: escritas simultâneas detectadas"
            )
        finally:
            _ts.commit_pending_write = original_commit_pending

    def test_second_cycle_skips_unchanged_files(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Segundo ciclo não deve reprocessar arquivos inalterados (source_state funciona)."""
        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        from media_repo_pipeline.main import _run_cycle

        cfg_multi = PipelineConfig(
            input_root=cfg.input_root,
            output_root=cfg.output_root,
            sqlite_db_path=cfg.sqlite_db_path,
            mode="copy",
            workers_count=2,
            scan_interval_seconds=1,
            stable_check_interval_seconds=0,
            stable_check_count=1,
            max_retries_processing=1,
            low_disk_space_threshold_bytes=0,
            run_once=True,
        )

        n_files = 10
        source, _ = self._setup_repo(db, cfg_multi, tmp_workspace, n_files)

        # Primeiro ciclo: processa tudo
        _run_cycle(db, cfg_multi)
        count_after_first = db.conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        assert count_after_first == n_files

        # Segundo ciclo: não deve adicionar novas entradas
        _run_cycle(db, cfg_multi)
        count_after_second = db.conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        assert count_after_second == n_files, "Segundo ciclo não deve reprocessar arquivos inalterados"
