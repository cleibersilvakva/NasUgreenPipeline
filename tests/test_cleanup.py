"""Testes para cleanup_input.py (run_cleanup) e endpoints do servidor.

Casos de teste cobertos:
─ run_cleanup (lógica core):
  TC-01  Arquivo sem registro no banco → pulado
  TC-02  Arquivo com status não autorizado (error) → pulado
  TC-03  Arquivo sem destination_path no banco → warn
  TC-04  Arquivo com destino ausente em disco → warn
  TC-05  Arquivo com tamanho divergente → warn
  TC-06  Arquivo elegível em dry-run → gera evento 'remove' sem deletar
  TC-07  Arquivo elegível em modo real → deleta fisicamente
  TC-08  Banco não existe → evento 'fatal'
  TC-09  Pasta de entrada não existe → evento 'fatal'
  TC-10  Status inválido (ex: 'pending') → evento 'fatal'
  TC-11  Múltiplos arquivos — stats corretos no summary
  TC-12  Diretórios vazios removidos no modo real
  TC-13  Erro de permissão ao deletar → evento 'error' sem parar
  TC-14  verbose=True emite eventos 'skip' extras

─ _api_cleanup_preview (servidor):
  TC-15  Banco ausente → 404
  TC-16  Pasta de entrada ausente → 200 com zeros
  TC-17  Banco presente com dados → retorna total_input, eligible, db_records

─ _api_cleanup_run (servidor):
  TC-18  dry_run=True por padrão
  TC-19  Retorna 409 se cleanup já em execução
  TC-20  Statuses inválidos filtrados → 400 se nenhum válido sobrar
  TC-21  Lista de statuses válidos → 200 e thread iniciada
  TC-22  statuses não-lista → defaulta para kept+duplicate

─ HTTP integration:
  TC-23  GET /api/cleanup/preview retorna JSON
  TC-24  POST /api/cleanup/run retorna 200 com started=True
  TC-25  POST /api/cleanup/run com body vazio → usa dry_run=True
  TC-26  POST /api/cleanup/run duplo simultâneo → segundo retorna 409
"""

from __future__ import annotations

import http.client
import json
import sqlite3
import sys
import threading
import unittest.mock as mock
from pathlib import Path

import pytest

import web.server as srv


# ── Fixture: módulo cleanup_input ─────────────────────────────────────────────


@pytest.fixture(scope="module")
def cleanup_mod():
    """Importa media_repo_pipeline.cleanup para os testes de lógica core."""
    import media_repo_pipeline.cleanup as mod
    return mod


# ── Fixture: banco mínimo ─────────────────────────────────────────────────────


def _make_db(db_path: Path, records: list[dict]) -> None:
    """Cria banco SQLite mínimo com source_state + files."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE source_state (
            source_path TEXT PRIMARY KEY,
            repository_name_canonical TEXT,
            size_bytes INTEGER,
            mtime_epoch REAL,
            last_hash_sha256 TEXT,
            last_processed_at TEXT,
            last_status TEXT,
            retry_count INTEGER DEFAULT 0
        );
        CREATE TABLE files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT,
            destination_path TEXT
        );
        CREATE TABLE kept_files (
            hash_sha256 TEXT PRIMARY KEY,
            canonical_destination_path TEXT
        );
    """)
    for r in records:
        conn.execute(
            "INSERT INTO source_state (source_path, repository_name_canonical, size_bytes, "
            "mtime_epoch, last_hash_sha256, last_processed_at, last_status, retry_count) "
            "VALUES (?, 'repo', ?, 0.0, NULL, '2024-01-01', ?, 0)",
            (r["source_path"], r.get("size", 100), r["status"]),
        )
        if r.get("destination_path"):
            conn.execute(
                "INSERT INTO files (source_path, destination_path) VALUES (?, ?)",
                (r["source_path"], r["destination_path"]),
            )
    conn.commit()
    conn.close()


def _collect(gen) -> list[dict]:
    return list(gen)


# ── TC-01 a TC-14: lógica core de run_cleanup ─────────────────────────────────


class TestRunCleanupLogic:
    def test_tc01_no_db_record_skipped(self, tmp_path, cleanup_mod):
        """TC-01: arquivo sem entrada no banco → pulado (pulado_sem_registro)."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        inp.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"data")
        _make_db(db, [])  # banco vazio

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["pulado_sem_registro"] == 1
        assert summary["stats"]["removido"] == 0

    def test_tc02_status_not_authorized(self, tmp_path, cleanup_mod):
        """TC-02: status 'error' → não autorizado para remoção."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        inp.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"data")
        _make_db(db, [{"source_path": str(f), "status": "error"}])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["pulado_status"] == 1
        assert summary["stats"]["removido"] == 0

    def test_tc03_no_destination_in_db(self, tmp_path, cleanup_mod):
        """TC-03: arquivo kept mas sem destination_path no banco → warn."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        inp.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"data")
        # sem destination_path
        _make_db(db, [{"source_path": str(f), "status": "kept", "destination_path": None}])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        warns = [e for e in events if e["type"] == "warn" and e["reason"] == "sem_destino_no_banco"]
        assert len(warns) == 1
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["pulado_destino_ausente"] == 1

    def test_tc04_destination_missing_on_disk(self, tmp_path, cleanup_mod):
        """TC-04: destination_path registrado mas arquivo não existe em disco → warn."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"data")
        dest = str(out / "NONEXISTENT.jpg")  # não cria o arquivo
        _make_db(db, [{"source_path": str(f), "status": "kept", "destination_path": dest}])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        warns = [e for e in events if e["type"] == "warn" and e["reason"] == "destino_ausente_em_disco"]
        assert len(warns) == 1

    def test_tc05_size_mismatch(self, tmp_path, cleanup_mod):
        """TC-05: tamanho origem != destino → warn pulado_tamanho."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"AAAA")        # 4 bytes
        dest = out / "dest.jpg"
        dest.write_bytes(b"BB")       # 2 bytes (divergência)
        _make_db(db, [{"source_path": str(f), "status": "kept",
                        "size": 4, "destination_path": str(dest)}])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        warns = [e for e in events if e["type"] == "warn" and e["reason"] == "tamanho_divergente"]
        assert len(warns) == 1
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["pulado_tamanho"] == 1

    def test_tc06_eligible_dry_run_not_deleted(self, tmp_path, cleanup_mod):
        """TC-06: arquivo elegível em dry-run → evento remove mas arquivo ainda existe."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"HELLO")
        dest = out / "dest.jpg"
        dest.write_bytes(b"HELLO")
        _make_db(db, [{"source_path": str(f), "status": "kept",
                        "size": 5, "destination_path": str(dest)}])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        removes = [e for e in events if e["type"] == "remove"]
        assert len(removes) == 1
        assert removes[0]["dry_run"] is True
        assert f.exists(), "arquivo NÃO deve ser apagado em dry-run"

    def test_tc07_eligible_real_mode_deleted(self, tmp_path, cleanup_mod):
        """TC-07: arquivo elegível em modo real → arquivo removido do disco."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"HELLO")
        dest = out / "dest.jpg"
        dest.write_bytes(b"HELLO")
        _make_db(db, [{"source_path": str(f), "status": "kept",
                        "size": 5, "destination_path": str(dest)}])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=False))
        removes = [e for e in events if e["type"] == "remove"]
        assert len(removes) == 1
        assert removes[0]["dry_run"] is False
        assert not f.exists(), "arquivo DEVE ser apagado no modo real"

    def test_tc08_db_not_found(self, tmp_path, cleanup_mod):
        """TC-08: banco inexistente → evento 'fatal'."""
        inp = tmp_path / "in"
        inp.mkdir()
        events = _collect(cleanup_mod.run_cleanup(tmp_path / "missing.db", inp, dry_run=True))
        assert events[0]["type"] == "fatal"
        assert "Banco" in events[0]["detail"]

    def test_tc09_input_not_found(self, tmp_path, cleanup_mod):
        """TC-09: pasta de entrada inexistente → evento 'fatal'."""
        db = tmp_path / "index.db"
        _make_db(db, [])
        events = _collect(cleanup_mod.run_cleanup(db, tmp_path / "nonexistent", dry_run=True))
        assert events[0]["type"] == "fatal"
        assert "entrada" in events[0]["detail"].lower()

    def test_tc10_invalid_status_in_allowed(self, tmp_path, cleanup_mod):
        """TC-10: status 'pending' (inválido/inseguro) na lista → evento 'fatal'."""
        db = tmp_path / "index.db"
        _make_db(db, [])
        inp = tmp_path / "in"
        inp.mkdir()
        events = _collect(cleanup_mod.run_cleanup(
            db, inp, allowed_statuses=("pending",), dry_run=True
        ))
        assert events[0]["type"] == "fatal"
        assert "inválid" in events[0]["detail"].lower() or "pending" in events[0]["detail"]

    def test_tc11_multiple_files_stats(self, tmp_path, cleanup_mod):
        """TC-11: mix de casos → stats completos no summary."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()

        # 1 sem registro
        no_rec = inp / "no_rec.jpg"
        no_rec.write_bytes(b"x")

        # 1 status errado
        bad_status = inp / "bad.jpg"
        bad_status.write_bytes(b"x")

        # 1 elegível
        good = inp / "good.jpg"
        good.write_bytes(b"HELLO")
        dest = out / "good_dest.jpg"
        dest.write_bytes(b"HELLO")

        _make_db(db, [
            {"source_path": str(bad_status), "status": "error"},
            {"source_path": str(good), "status": "kept",
             "size": 5, "destination_path": str(dest)},
        ])

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        summary = next(e for e in events if e["type"] == "summary")
        s = summary["stats"]
        assert s["pulado_sem_registro"] == 1
        assert s["pulado_status"] == 1
        assert s["removido"] == 1

    def test_tc12_empty_dirs_removed_in_real_mode(self, tmp_path, cleanup_mod):
        """TC-12: diretório vazio deve ser removido após limpeza real."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        subdir = inp / "2024" / "01"
        subdir.mkdir(parents=True)
        out.mkdir()

        f = subdir / "foto.jpg"
        f.write_bytes(b"DATA")
        dest = out / "dest.jpg"
        dest.write_bytes(b"DATA")

        _make_db(db, [{"source_path": str(f), "status": "kept",
                        "size": 4, "destination_path": str(dest)}])

        _collect(cleanup_mod.run_cleanup(db, inp, dry_run=False))
        assert not subdir.exists(), "subdir deve ser removido quando vazia"

    def test_tc13_permission_error_on_delete(self, tmp_path, cleanup_mod):
        """TC-13: erro de permissão ao deletar → evento 'error', execução continua."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"DATA")
        dest = out / "dest.jpg"
        dest.write_bytes(b"DATA")
        _make_db(db, [{"source_path": str(f), "status": "kept",
                        "size": 4, "destination_path": str(dest)}])

        original_unlink = Path.unlink
        def _mock_unlink(self_path, *args, **kwargs):
            if self_path.name == ".cleanup_write_test":
                return original_unlink(self_path, *args, **kwargs)
            raise OSError("permission denied")
            
        with mock.patch("pathlib.Path.unlink", side_effect=_mock_unlink, autospec=True):
            events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=False))

        error_evts = [e for e in events if e["type"] == "error"]
        assert len(error_evts) == 1
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["erro"] == 1

    def test_tc14_verbose_emits_skip_events(self, tmp_path, cleanup_mod):
        """TC-14: verbose=True → eventos 'skip' para arquivos sem registro."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        inp.mkdir()
        f = inp / "foto.jpg"
        f.write_bytes(b"x")
        _make_db(db, [])  # sem registro

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True, verbose=True))
        skip_evts = [e for e in events if e["type"] == "skip"]
        assert len(skip_evts) == 1
        assert skip_evts[0]["reason"] == "sem_registro"

    def test_tc27_large_volume_cleanup(self, tmp_path, cleanup_mod):
        """TC-27: Testa varredura de grandes volumes de arquivos, garantindo eventos 'progress'."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()

        n_files = 150
        records = []
        for i in range(n_files):
            f = inp / f"file_{i:03d}.jpg"
            f.write_bytes(b"DATA")
            dest = out / f"dest_{i:03d}.jpg"
            dest.write_bytes(b"DATA")
            records.append({
                "source_path": str(f), "status": "kept",
                "size": 4, "destination_path": str(dest)
            })

        _make_db(db, records)

        original_unlink = Path.unlink
        def _mock_unlink(self_path, *args, **kwargs):
            if self_path.name == ".cleanup_write_test":
                return original_unlink(self_path, *args, **kwargs)
            pass  # Pula o IO real pra acelerar o mock

        with mock.patch("pathlib.Path.unlink", side_effect=_mock_unlink, autospec=True):
            events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=False))

        # A cada 50 arquivos é emitido um progresso, logo 150/50 = 3 eventos
        prog_evts = [e for e in events if e["type"] == "progress"]
        assert len(prog_evts) == 3

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["removido"] == n_files
        assert summary["stats"]["erro"] == 0

    def test_tc28_duplicate_fallback_via_hash(self, tmp_path, cleanup_mod):
        """TC-28: Quando destiny nulo, deve achar o destino original em kept_files pelo hash."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(); out.mkdir()

        f = inp / "foto.jpg"
        f.write_bytes(b"DATA")
        dest = out / "dest.jpg"
        dest.write_bytes(b"DATA")

        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE source_state (
                source_path TEXT PRIMARY KEY, last_status TEXT,
                repository_name_canonical TEXT, size_bytes INTEGER,
                last_hash_sha256 TEXT
            );
            CREATE TABLE files (id INTEGER PRIMARY KEY, source_path TEXT, destination_path TEXT);
            CREATE TABLE kept_files (hash_sha256 TEXT PRIMARY KEY, canonical_destination_path TEXT);
        """)
        # Origem está preenchida mas na tabela source_state não existe mapping de arquivo em files
        conn.execute("INSERT INTO source_state VALUES (?, 'duplicate', 'r1', 4, 'XYZ123')", (str(f),))
        conn.execute("INSERT INTO kept_files VALUES ('XYZ123', ?)", (str(dest),))
        conn.commit(); conn.close()

        events = _collect(cleanup_mod.run_cleanup(db, inp, dry_run=True))
        summary = next(e for e in events if e["type"] == "summary")
        assert summary["stats"]["removido"] == 1
        assert summary["stats"]["pulado_destino_ausente"] == 0


# ── TC-15 a TC-22: endpoints do servidor ──────────────────────────────────────


class TestApiCleanupPreview:
    def test_tc15_db_missing_returns_404(self, tmp_path):
        """TC-15: banco ausente → 404."""
        with mock.patch("web.server._load_yaml", return_value={
            "sqlite_db_path": str(tmp_path / "missing.db"),
            "input_root": str(tmp_path / "in"),
            "output_root": str(tmp_path / "out"),
        }):
            code, body = srv._api_cleanup_preview()
        assert code == 404
        assert "error" in body

    def test_tc16_no_input_dir_returns_zeros(self, tmp_path):
        """TC-16: pasta de entrada não existe → 200 com zeros."""
        db = tmp_path / "index.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE source_state (source_path TEXT PRIMARY KEY, last_status TEXT);
            CREATE TABLE files (source_path TEXT, destination_path TEXT);
        """)
        conn.close()
        with mock.patch("web.server._load_yaml", return_value={
            "sqlite_db_path": str(db),
            "input_root": str(tmp_path / "nonexistent"),
            "output_root": str(tmp_path / "out"),
        }):
            code, body = srv._api_cleanup_preview()
        assert code == 200
        assert body["total_input"] == 0
        assert body["eligible"] == 0

    def test_tc17_with_data_returns_counts(self, tmp_path):
        """TC-17: banco com dados → retorna contagens corretas."""
        db = tmp_path / "index.db"
        inp = tmp_path / "in"
        inp.mkdir()
        (inp / "foto.jpg").write_bytes(b"x")

        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE source_state (
                source_path TEXT PRIMARY KEY, last_status TEXT,
                repository_name_canonical TEXT, size_bytes INTEGER,
                mtime_epoch REAL, last_hash_sha256 TEXT,
                last_processed_at TEXT, retry_count INTEGER
            );
            CREATE TABLE files (source_path TEXT, destination_path TEXT);
        """)
        src = str(inp / "foto.jpg")
        conn.execute(
            "INSERT INTO source_state VALUES (?, 'kept', 'repo', 1, 0, NULL, '2024-01-01', 0)",
            (src,),
        )
        conn.execute("INSERT INTO files VALUES (?, '/output/dest.jpg')", (src,))
        conn.commit()
        conn.close()

        with mock.patch("web.server._load_yaml", return_value={
            "sqlite_db_path": str(db),
            "input_root": str(inp),
            "output_root": str(tmp_path / "out"),
        }):
            code, body = srv._api_cleanup_preview()
        assert code == 200
        assert body["db_records"] == 1
        assert body["eligible"] == 1
        assert body["total_input"] == 1


class TestApiCleanupRun:
    def test_tc18_dry_run_default(self, tmp_path):
        """TC-18: sem body → dry_run=True por padrão."""
        with mock.patch("threading.Thread") as mock_th:
            mock_th.return_value = mock.MagicMock()
            code, body = srv._api_cleanup_run({})
        assert code == 200
        assert body["dry_run"] is True
        assert body["started"] is True
        srv._cleanup_running = False  # cleanup manual

    def test_tc19_returns_409_when_already_running(self):
        """TC-19: limpeza já em execução → 409."""
        srv._cleanup_running = True
        code, body = srv._api_cleanup_run({})
        assert code == 409
        assert "error" in body

    def test_tc20_invalid_statuses_only_returns_400(self):
        """TC-20: statuses=['pending','skipped'] → todos inválidos → 400."""
        # Garante estado idle antes do teste
        srv._cleanup_running = False
        code, body = srv._api_cleanup_run({"statuses": ["pending", "skipped"]})
        assert code == 400
        assert "error" in body

    def test_tc21_valid_statuses_start_thread(self, tmp_path):
        """TC-21: statuses=['kept','duplicate'] → 200 e thread iniciada."""
        srv._cleanup_running = False  # garantir idle
        with mock.patch("threading.Thread") as mock_th:
            inst = mock.MagicMock()
            mock_th.return_value = inst
            code, body = srv._api_cleanup_run({"dry_run": True, "statuses": ["kept", "duplicate"]})
        assert code == 200
        inst.start.assert_called_once()
        srv._cleanup_running = False  # reset pois thread nao executou

    def test_tc22_non_list_statuses_defaults(self):
        """TC-22: statuses como string → trata como padrão kept+duplicate."""
        srv._cleanup_running = False  # garantir idle
        with mock.patch("threading.Thread") as mock_th:
            mock_th.return_value = mock.MagicMock()
            code, body = srv._api_cleanup_run({"statuses": "kept"})
        # statuses=string → filtrado → kept permanece (é válido)
        assert code == 200
        srv._cleanup_running = False  # reset


# ── TC-23 a TC-26: integração HTTP ────────────────────────────────────────────


def _http(host: str, port: int, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"} if body else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


@pytest.fixture
def running_server_cleanup(tmp_path):
    """Servidor com banco SQLite mínimo pronto para testes de cleanup."""
    db = tmp_path / "index.db"
    inp = tmp_path / "in"
    inp.mkdir()

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE source_state (
            source_path TEXT PRIMARY KEY, last_status TEXT,
            repository_name_canonical TEXT, size_bytes INTEGER,
            mtime_epoch REAL, last_hash_sha256 TEXT,
            last_processed_at TEXT, retry_count INTEGER
        );
        CREATE TABLE files (source_path TEXT, destination_path TEXT);
        CREATE TABLE processing_runs (
            id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
            mode TEXT, pipeline_version TEXT, policy_version TEXT,
            files_seen INTEGER, files_supported INTEGER, files_kept INTEGER,
            files_duplicate INTEGER, files_review INTEGER, files_corrupted INTEGER,
            files_skipped INTEGER, notes TEXT
        );
    """)
    conn.close()

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"input_root: {inp}\noutput_root: {tmp_path / 'out'}\n"
        f"sqlite_db_path: {db}\n",
        encoding="utf-8",
    )
    srv._CONFIG_PATH = cfg_file

    server = srv._ThreadedHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield "127.0.0.1", port
    server.shutdown()
    t.join(timeout=5)


class TestHttpCleanupIntegration:
    def test_tc23_get_preview_returns_json(self, running_server_cleanup):
        """TC-23: GET /api/cleanup/preview → JSON válido."""
        host, port = running_server_cleanup
        status, body = _http(host, port, "GET", "/api/cleanup/preview")
        assert status == 200
        data = json.loads(body)
        assert "total_input" in data
        assert "eligible" in data
        assert "db_records" in data

    def test_tc24_post_run_returns_200(self, running_server_cleanup):
        """TC-24: POST /api/cleanup/run → 200 com started=True."""
        host, port = running_server_cleanup
        srv._cleanup_running = False  # garantir idle
        payload = json.dumps({"dry_run": True}).encode()
        status, body = _http(host, port, "POST", "/api/cleanup/run", payload)
        srv._cleanup_running = False  # limpar após thread iniciar
        assert status == 200
        data = json.loads(body)
        assert data.get("started") is True

    def test_tc25_post_run_empty_body_uses_dry_run(self, running_server_cleanup):
        """TC-25: body vazio → dry_run=True por padrão."""
        host, port = running_server_cleanup
        srv._cleanup_running = False  # garantir idle
        status, body = _http(host, port, "POST", "/api/cleanup/run", b"")
        assert status == 200
        data = json.loads(body)
        assert data.get("dry_run") is True

    def test_tc26_double_run_second_returns_409(self, running_server_cleanup):
        """TC-26: segunda chamada enquanto cleanup está running → 409."""
        host, port = running_server_cleanup
        srv._cleanup_running = True  # simula execução em andamento
        payload = json.dumps({"dry_run": True}).encode()
        status, body = _http(host, port, "POST", "/api/cleanup/run", payload)
        assert status == 409
        data = json.loads(body)
        assert "error" in data
        srv._cleanup_running = False  # cleanup
