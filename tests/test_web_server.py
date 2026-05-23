"""Tests for the web server API handlers (web/server.py).

Cobre funções de handler diretamente (unit) e a camada HTTP (integration).
Usa apenas stdlib: pytest + unittest.mock + http.client + threading.
"""

from __future__ import annotations

import http.client
import json
import queue
import sqlite3
import threading
import unittest.mock as mock
from pathlib import Path

import pytest

import web.server as srv


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_global_state():
    """Garante estado limpo antes e depois de cada teste."""
    srv._pipeline_proc = None
    srv._pipeline_starting = False
    srv._cleanup_running = False
    with srv._sse_lock:
        srv._sse_clients.clear()
    srv._CONFIG_PATH = None
    yield
    srv._pipeline_proc = None
    srv._pipeline_starting = False
    srv._cleanup_running = False
    with srv._sse_lock:
        srv._sse_clients.clear()
    srv._CONFIG_PATH = None


@pytest.fixture
def tmp_base(tmp_path: Path) -> Path:
    d = tmp_path / "media"
    d.mkdir()
    return d


@pytest.fixture
def running_server(tmp_path: Path):
    """Sobe _ThreadedHTTPServer em porta aleatória e faz teardown após o teste."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("input_root: entrada\noutput_root: destino\n", encoding="utf-8")
    srv._CONFIG_PATH = cfg_file

    server = srv._ThreadedHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield "127.0.0.1", port
    server.shutdown()
    t.join(timeout=5)


# ── _safe_resolve ──────────────────────────────────────────────────────────────


class TestSafeResolve:
    def test_valid_path_inside_base(self, tmp_base: Path) -> None:
        sub = tmp_base / "sub"
        sub.mkdir()
        result = srv._safe_resolve(tmp_base, "sub")
        assert result == sub.resolve()

    def test_empty_rel_returns_base(self, tmp_base: Path) -> None:
        result = srv._safe_resolve(tmp_base, "")
        assert result == tmp_base.resolve()

    def test_traversal_dotdot_blocked(self, tmp_base: Path) -> None:
        result = srv._safe_resolve(tmp_base, "../outside")
        assert result is None

    def test_prefix_name_attack_blocked(self, tmp_path: Path) -> None:
        """`/tmp/safe_evil` não deve ser aceito quando base é `/tmp/safe`."""
        base = tmp_path / "safe"
        base.mkdir()
        evil = tmp_path / "safe_evil"
        evil.mkdir()
        # rel que resolve para caminho com mesmo prefixo de nome
        result = srv._safe_resolve(base, "../safe_evil/x")
        assert result is None

    def test_deep_valid_path(self, tmp_base: Path) -> None:
        deep = tmp_base / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = srv._safe_resolve(tmp_base, "a/b/c")
        assert result == deep.resolve()

    def test_multiple_dotdot_blocked(self, tmp_base: Path) -> None:
        result = srv._safe_resolve(tmp_base, "../../..")
        assert result is None

    def test_absolute_path_used_as_rel_blocked(self, tmp_base: Path) -> None:
        result = srv._safe_resolve(tmp_base, "/etc/passwd")
        assert result is None


# ── _api_status ────────────────────────────────────────────────────────────────


class TestApiStatus:
    def test_idle_when_no_proc(self) -> None:
        assert srv._api_status() == {"status": "idle"}

    def test_running_when_proc_set(self) -> None:
        srv._pipeline_proc = mock.MagicMock()
        assert srv._api_status() == {"status": "running"}

    def test_running_when_starting_flag_set(self) -> None:
        srv._pipeline_starting = True
        assert srv._api_status() == {"status": "running"}

    def test_idle_after_both_proc_and_starting_cleared(self) -> None:
        srv._pipeline_proc = mock.MagicMock()
        srv._pipeline_starting = True
        srv._pipeline_proc = None
        srv._pipeline_starting = False
        assert srv._api_status() == {"status": "idle"}


# ── _api_run ───────────────────────────────────────────────────────────────────


class TestApiRun:
    def test_returns_404_when_no_config(self, tmp_path: Path) -> None:
        with mock.patch("web.server._find_config_file", side_effect=FileNotFoundError("sem config")):
            code, body = srv._api_run()
        assert code == 404
        assert "error" in body

    def test_returns_409_when_proc_already_set(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_root: e\noutput_root: d\n", encoding="utf-8")
        srv._CONFIG_PATH = cfg
        srv._pipeline_proc = mock.MagicMock()
        code, body = srv._api_run()
        assert code == 409
        assert "error" in body

    def test_returns_409_when_starting_flag_set(self, tmp_path: Path) -> None:
        """Verifica o fix: _pipeline_starting=True deve bloquear segunda chamada."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_root: e\noutput_root: d\n", encoding="utf-8")
        srv._CONFIG_PATH = cfg
        srv._pipeline_starting = True  # simula thread saindo do lock mas ainda não setou proc
        code, body = srv._api_run()
        assert code == 409
        assert "error" in body

    def test_returns_200_and_starts_thread(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_root: e\noutput_root: d\n", encoding="utf-8")
        srv._CONFIG_PATH = cfg
        with mock.patch("threading.Thread") as mock_thread:
            mock_instance = mock.MagicMock()
            mock_thread.return_value = mock_instance
            code, body = srv._api_run()
        assert code == 200
        assert body.get("started") is True
        mock_instance.start.assert_called_once()

    def test_pipeline_starting_set_before_thread_runs(self, tmp_path: Path) -> None:
        """`_pipeline_starting` deve ser True logo após _api_run retornar 200."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_root: e\noutput_root: d\n", encoding="utf-8")
        srv._CONFIG_PATH = cfg
        with mock.patch("threading.Thread") as mock_thread:
            mock_thread.return_value = mock.MagicMock()
            code, _ = srv._api_run()
        assert code == 200
        assert srv._pipeline_starting is True


# ── _api_stop ──────────────────────────────────────────────────────────────────


class TestApiStop:
    def test_returns_409_when_not_running(self) -> None:
        code, body = srv._api_stop()
        assert code == 409
        assert "error" in body

    def test_returns_200_and_calls_terminate(self) -> None:
        proc = mock.MagicMock()
        srv._pipeline_proc = proc
        code, body = srv._api_stop()
        assert code == 200
        assert body.get("stopped") is True
        proc.terminate.assert_called_once()


# ── _api_files ─────────────────────────────────────────────────────────────────


class TestApiFiles:
    def test_returns_empty_for_nonexistent_root(self, tmp_path: Path) -> None:
        with mock.patch("web.server._get_roots", return_value=(tmp_path / "x", tmp_path / "y")):
            code, body = srv._api_files({"root": "input", "path": ""})
        assert code == 200
        assert body["entries"] == []

    def test_lists_files_in_valid_dir(self, tmp_base: Path) -> None:
        (tmp_base / "foto.jpg").write_bytes(b"fake")
        (tmp_base / "sub").mkdir()
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, body = srv._api_files({"root": "input", "path": ""})
        assert code == 200
        names = [e["name"] for e in body["entries"]]
        assert "foto.jpg" in names
        assert "sub" in names

    def test_blocks_path_traversal(self, tmp_base: Path) -> None:
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, body = srv._api_files({"root": "input", "path": "../../etc"})
        assert code == 403

    def test_hidden_files_excluded(self, tmp_base: Path) -> None:
        (tmp_base / ".hidden").write_bytes(b"secret")
        (tmp_base / "visible.jpg").write_bytes(b"fake")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            _, body = srv._api_files({"root": "input", "path": ""})
        names = [e["name"] for e in body["entries"]]
        assert ".hidden" not in names
        assert "visible.jpg" in names

    def test_image_and_video_flags(self, tmp_base: Path) -> None:
        (tmp_base / "photo.jpg").write_bytes(b"fake")
        (tmp_base / "clip.mp4").write_bytes(b"fake")
        (tmp_base / "doc.txt").write_bytes(b"fake")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            _, body = srv._api_files({"root": "input", "path": ""})
        by_name = {e["name"]: e for e in body["entries"]}
        assert by_name["photo.jpg"]["is_image"] is True
        assert by_name["clip.mp4"]["is_video"] is True
        assert by_name["doc.txt"]["is_image"] is False
        assert by_name["doc.txt"]["is_video"] is False

    def test_subdirectory_listing(self, tmp_base: Path) -> None:
        sub = tmp_base / "2024" / "01"
        sub.mkdir(parents=True)
        (sub / "img.jpg").write_bytes(b"fake")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, body = srv._api_files({"root": "input", "path": "2024/01"})
        assert code == 200
        names = [e["name"] for e in body["entries"]]
        assert "img.jpg" in names

    def test_entry_has_expected_fields(self, tmp_base: Path) -> None:
        (tmp_base / "img.png").write_bytes(b"fake")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            _, body = srv._api_files({"root": "input", "path": ""})
        entry = body["entries"][0]
        for field in ("name", "path", "is_dir", "size", "mtime", "ext", "is_image", "is_video"):
            assert field in entry, f"campo '{field}' ausente"

    def test_dirs_sorted_before_files(self, tmp_base: Path) -> None:
        """Pastas devem aparecer antes de arquivos (is_file() == False < True)."""
        (tmp_base / "zzz_dir").mkdir()
        (tmp_base / "aaa.jpg").write_bytes(b"x")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            _, body = srv._api_files({"root": "input", "path": ""})
        entries = body["entries"]
        dir_idx = next(i for i, e in enumerate(entries) if e["name"] == "zzz_dir")
        file_idx = next(i for i, e in enumerate(entries) if e["name"] == "aaa.jpg")
        assert dir_idx < file_idx


# ── _api_thumbnail ─────────────────────────────────────────────────────────────


class TestApiThumbnail:
    def test_404_for_nonexistent_file(self, tmp_base: Path) -> None:
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, _, _ = srv._api_thumbnail({"root": "input", "path": "ghost.jpg"})
        assert code == 404

    def test_404_for_path_traversal(self, tmp_base: Path) -> None:
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, _, _ = srv._api_thumbnail({"root": "input", "path": "../../etc/passwd"})
        assert code == 404

    def test_204_or_success_for_regular_file(self, tmp_base: Path) -> None:
        """Sem PIL: 204. Com PIL + bytes inválidos: 204. Com PIL + imagem real: 200."""
        img = tmp_base / "photo.jpg"
        img.write_bytes(b"not-a-real-jpeg-payload")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, _, _ = srv._api_thumbnail({"root": "input", "path": "photo.jpg"})
        assert code in (200, 204)

    def test_size_clamped_at_upper_bound(self, tmp_base: Path) -> None:
        img = tmp_base / "img.jpg"
        img.write_bytes(b"fake")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            # size=999999 → clamped para 3000 → não deve lançar erro 500
            code, _, _ = srv._api_thumbnail({"root": "input", "path": "img.jpg", "size": "999999"})
        assert code in (200, 204)  # não deve ser 500 por overflow

    def test_size_clamped_at_lower_bound(self, tmp_base: Path) -> None:
        img = tmp_base / "img.jpg"
        img.write_bytes(b"fake")
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, _, _ = srv._api_thumbnail({"root": "input", "path": "img.jpg", "size": "0"})
        assert code in (200, 204)

    def test_directory_path_returns_404(self, tmp_base: Path) -> None:
        (tmp_base / "subdir").mkdir()
        with mock.patch("web.server._get_roots", return_value=(tmp_base, tmp_base)):
            code, _, _ = srv._api_thumbnail({"root": "input", "path": "subdir"})
        assert code == 404  # is_file() é False para diretório


# ── _api_last_run ──────────────────────────────────────────────────────────────


class TestApiLastRun:
    def test_returns_none_when_db_missing(self, tmp_path: Path) -> None:
        with mock.patch("web.server._load_yaml", return_value={"sqlite_db_path": str(tmp_path / "missing.db")}):
            result = srv._api_last_run()
        assert result == {"last_run": None}

    def test_returns_none_when_table_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE processing_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT)"
        )
        conn.commit()
        conn.close()
        with mock.patch("web.server._load_yaml", return_value={"sqlite_db_path": str(db)}):
            result = srv._api_last_run()
        assert result["last_run"] is None

    def test_returns_last_row(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE processing_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT)"
        )
        conn.execute("INSERT INTO processing_runs VALUES (1, '2024-01-01', '2024-01-01', 'ok')")
        conn.execute("INSERT INTO processing_runs VALUES (2, '2024-06-01', '2024-06-01', 'ok')")
        conn.commit()
        conn.close()
        with mock.patch("web.server._load_yaml", return_value={"sqlite_db_path": str(db)}):
            result = srv._api_last_run()
        assert result["last_run"]["id"] == 2

    def test_returns_none_on_bad_db_structure(self, tmp_path: Path) -> None:
        """Tabela inexistente não deve estourar — retorna last_run=None com error."""
        db = tmp_path / "empty.db"
        db.touch()
        with mock.patch("web.server._load_yaml", return_value={"sqlite_db_path": str(db)}):
            result = srv._api_last_run()
        assert result["last_run"] is None


# ── _broadcast ─────────────────────────────────────────────────────────────────


class TestBroadcast:
    def test_delivers_to_all_clients(self) -> None:
        q1: queue.Queue = queue.Queue()
        q2: queue.Queue = queue.Queue()
        with srv._sse_lock:
            srv._sse_clients.extend([q1, q2])
        srv._broadcast({"type": "log", "line": "hello"})
        assert not q1.empty()
        assert not q2.empty()

    def test_removes_full_dead_client(self) -> None:
        """Fila cheia (cliente morto) deve ser removida silenciosamente."""
        dead_q: queue.Queue = queue.Queue(maxsize=1)
        dead_q.put_nowait(b"already-full")
        live_q: queue.Queue = queue.Queue()
        with srv._sse_lock:
            srv._sse_clients.extend([dead_q, live_q])
        srv._broadcast({"type": "log", "line": "test"})
        with srv._sse_lock:
            assert dead_q not in srv._sse_clients
            assert live_q in srv._sse_clients
        assert not live_q.empty()

    def test_broadcast_with_no_clients_does_not_raise(self) -> None:
        srv._broadcast({"type": "ping"})  # não deve lançar exceção

    def test_broadcast_payload_is_valid_sse(self) -> None:
        q: queue.Queue = queue.Queue()
        with srv._sse_lock:
            srv._sse_clients.append(q)
        srv._broadcast({"type": "status", "status": "running"})
        payload: bytes = q.get_nowait()
        text = payload.decode()
        assert text.startswith("data: ")
        assert text.endswith("\n\n")
        data = json.loads(text[len("data: "):-2])
        assert data["type"] == "status"


# ── HTTP integration ───────────────────────────────────────────────────────────


def _http(host: str, port: int, method: str, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def _http_json(
    host: str, port: int, method: str, path: str, payload: dict
) -> tuple[int, bytes]:
    body = json.dumps(payload).encode()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def _make_repo_db(db_path: Path, repos: list[dict]) -> None:
    """Cria um SQLite mínimo com a tabela repositories populada."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS repositories (
            id INTEGER PRIMARY KEY,
            repository_name_canonical TEXT UNIQUE,
            display_name TEXT,
            status TEXT DEFAULT 'active',
            output_root_path TEXT,
            is_source_present INTEGER DEFAULT 1,
            last_source_root_path TEXT DEFAULT '',
            first_seen_at TEXT DEFAULT '',
            last_seen_at TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )"""
    )
    for r in repos:
        conn.execute(
            "INSERT INTO repositories (repository_name_canonical, display_name, status, output_root_path) VALUES (?,?,?,?)",
            (r["canonical"], r.get("display_name", r["canonical"]), r.get("status", "active"), r.get("output_root_path") or None),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def server_with_db(tmp_path: Path):
    """Servidor HTTP com config apontando para um DB populado."""
    db_dir = tmp_path / "output" / "db"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "index.db"

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"input_root: entrada\noutput_root: {tmp_path / 'output'}\n",
        encoding="utf-8",
    )
    srv._CONFIG_PATH = cfg_file

    server = srv._ThreadedHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield "127.0.0.1", port, db_path
    server.shutdown()
    t.join(timeout=5)


class TestHttpIntegration:
    def test_get_root_returns_html(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, body = _http(host, port, "GET", "/")
        assert status == 200
        assert b"<html" in body.lower() or b"<!DOCTYPE".lower() in body.lower()

    def test_get_status_returns_valid_json(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, body = _http(host, port, "GET", "/api/status")
        assert status == 200
        data = json.loads(body)
        assert "status" in data
        assert data["status"] in ("idle", "running")

    def test_post_run_without_config_returns_404(self) -> None:
        server = srv._ThreadedHTTPServer(("127.0.0.1", 0), srv.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with mock.patch("web.server._find_config_file", side_effect=FileNotFoundError("sem config")):
                status, body = _http("127.0.0.1", port, "POST", "/api/pipeline/run")
            assert status == 404
            data = json.loads(body)
            assert "error" in data
        finally:
            server.shutdown()
            t.join(timeout=5)

    def test_post_stop_when_idle_returns_409(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, body = _http(host, port, "POST", "/api/pipeline/stop")
        assert status == 409
        data = json.loads(body)
        assert "error" in data

    def test_get_files_returns_json_with_entries(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, body = _http(host, port, "GET", "/api/files?root=input&path=")
        assert status == 200
        data = json.loads(body)
        assert "entries" in data
        assert isinstance(data["entries"], list)

    def test_get_last_run_returns_json(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, body = _http(host, port, "GET", "/api/db/last-run")
        assert status == 200
        data = json.loads(body)
        assert "last_run" in data

    def test_unknown_get_route_returns_404(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, _ = _http(host, port, "GET", "/api/does-not-exist")
        assert status == 404

    def test_unknown_post_route_returns_404(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, _ = _http(host, port, "POST", "/api/does-not-exist")
        assert status == 404

    def test_options_cors_preflight_returns_204(self, running_server: tuple[str, int]) -> None:
        host, port = running_server
        status, _ = _http(host, port, "OPTIONS", "/api/pipeline/run")
        assert status == 204

    def test_simultaneous_run_only_one_succeeds(self, tmp_path: Path) -> None:
        """Race condition: N POSTs simultâneos → exatamente um retorna 200."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("input_root: entrada\noutput_root: destino\n", encoding="utf-8")
        srv._CONFIG_PATH = cfg

        server = srv._ThreadedHTTPServer(("127.0.0.1", 0), srv.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        results: list[int] = []
        lock = threading.Lock()

        def do_run() -> None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/api/pipeline/run")
            r = conn.getresponse()
            with lock:
                results.append(r.status)
            r.read()
            conn.close()

        try:
            with mock.patch("web.server._pipeline_thread"):
                threads = [threading.Thread(target=do_run) for _ in range(6)]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join(timeout=10)

            count_200 = results.count(200)
            count_409 = results.count(409)
            assert count_200 == 1, f"Esperado 1×200, obtido {count_200}: {results}"
            assert count_200 + count_409 == 6
        finally:
            server.shutdown()
            t.join(timeout=5)


# ── _api_repositories (unit) ──────────────────────────────────────────────────


class TestApiRepositoriesUnit:
    def test_no_db_returns_empty_list(self, tmp_path: Path) -> None:
        with mock.patch.object(srv, "_get_db_path", return_value=tmp_path / "missing.db"):
            code, data = srv._api_repositories()
        assert code == 200
        assert data == {"repositories": []}

    def test_pending_repo_has_pending_config_true(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        _make_repo_db(db_path, [{"canonical": "maria", "output_root_path": None}])
        with mock.patch.object(srv, "_get_db_path", return_value=db_path):
            code, data = srv._api_repositories()
        assert code == 200
        repos = data["repositories"]
        assert len(repos) == 1
        assert repos[0]["canonical"] == "maria"
        assert repos[0]["pending_config"] is True
        assert repos[0]["output_root_path"] == ""

    def test_configured_repo_has_pending_config_false(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        _make_repo_db(db_path, [{"canonical": "joao", "output_root_path": "/output/joao"}])
        with mock.patch.object(srv, "_get_db_path", return_value=db_path):
            code, data = srv._api_repositories()
        repos = data["repositories"]
        assert repos[0]["pending_config"] is False
        assert repos[0]["output_root_path"] == "/output/joao"

    def test_multiple_repos_mixed(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        _make_repo_db(db_path, [
            {"canonical": "a", "output_root_path": "/out/a"},
            {"canonical": "b", "output_root_path": None},
        ])
        with mock.patch.object(srv, "_get_db_path", return_value=db_path):
            _, data = srv._api_repositories()
        by_canonical = {r["canonical"]: r for r in data["repositories"]}
        assert by_canonical["a"]["pending_config"] is False
        assert by_canonical["b"]["pending_config"] is True


# ── _api_set_repo_output_root (unit) ──────────────────────────────────────────


class TestApiSetRepoOutputRootUnit:
    def test_empty_path_returns_400(self, tmp_path: Path) -> None:
        code, data = srv._api_set_repo_output_root("maria", {"output_root_path": ""})
        assert code == 400
        assert "error" in data

    def test_missing_key_returns_400(self) -> None:
        code, data = srv._api_set_repo_output_root("maria", {})
        assert code == 400

    def test_relative_path_returns_400(self) -> None:
        code, data = srv._api_set_repo_output_root("maria", {"output_root_path": "relativo/caminho"})
        assert code == 400
        assert "absoluto" in data["error"].lower()

    def test_no_db_returns_500(self, tmp_path: Path) -> None:
        abs_path = str(tmp_path / "destino")
        with mock.patch.object(srv, "_get_db_path", return_value=None):
            code, data = srv._api_set_repo_output_root("maria", {"output_root_path": abs_path})
        assert code == 500

    def test_unknown_canonical_returns_404(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        _make_repo_db(db_path, [])
        abs_path = str(tmp_path / "destino")
        with mock.patch.object(srv, "_get_db_path", return_value=db_path):
            code, data = srv._api_set_repo_output_root("naoexiste", {"output_root_path": abs_path})
        assert code == 404

    def test_valid_call_creates_dirs_and_returns_200(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        _make_repo_db(db_path, [{"canonical": "maria", "output_root_path": None}])
        dest = tmp_path / "maria-album"
        with mock.patch.object(srv, "_get_db_path", return_value=db_path):
            code, data = srv._api_set_repo_output_root("maria", {"output_root_path": str(dest)})
        assert code == 200
        assert data["ok"] is True
        assert (dest / "photos").is_dir()
        assert (dest / "videos").is_dir()

    def test_valid_call_persists_to_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "index.db"
        _make_repo_db(db_path, [{"canonical": "joao", "output_root_path": None}])
        dest = str(tmp_path / "joao-fotos")
        with mock.patch.object(srv, "_get_db_path", return_value=db_path):
            srv._api_set_repo_output_root("joao", {"output_root_path": dest})
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT output_root_path FROM repositories WHERE repository_name_canonical = 'joao'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == dest


# ── /api/repositories HTTP integration ────────────────────────────────────────


class TestApiRepositoriesHttp:
    def test_get_repositories_returns_json(self, server_with_db) -> None:
        host, port, db_path = server_with_db
        _make_repo_db(db_path, [{"canonical": "pedro", "output_root_path": None}])
        status, body = _http(host, port, "GET", "/api/repositories")
        assert status == 200
        data = json.loads(body)
        assert "repositories" in data

    def test_get_repositories_no_db_returns_empty(self, server_with_db) -> None:
        host, port, db_path = server_with_db
        # Não cria o DB — deve retornar lista vazia
        status, body = _http(host, port, "GET", "/api/repositories")
        assert status == 200
        data = json.loads(body)
        assert data["repositories"] == []

    def test_post_output_root_valid(self, server_with_db, tmp_path: Path) -> None:
        host, port, db_path = server_with_db
        _make_repo_db(db_path, [{"canonical": "beatriz", "output_root_path": None}])
        dest = str(tmp_path / "beatriz-album")
        status, body = _http_json(
            host, port, "POST",
            "/api/repositories/beatriz/output-root",
            {"output_root_path": dest},
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_post_output_root_empty_body_returns_400(self, server_with_db) -> None:
        host, port, _ = server_with_db
        status, body = _http_json(
            host, port, "POST",
            "/api/repositories/alguem/output-root",
            {"output_root_path": ""},
        )
        assert status == 400

    def test_post_output_root_relative_returns_400(self, server_with_db) -> None:
        host, port, _ = server_with_db
        status, body = _http_json(
            host, port, "POST",
            "/api/repositories/alguem/output-root",
            {"output_root_path": "relativo/path"},
        )
        assert status == 400

    def test_post_output_root_unknown_repo_returns_404(self, server_with_db, tmp_path: Path) -> None:
        host, port, db_path = server_with_db
        _make_repo_db(db_path, [])
        status, body = _http_json(
            host, port, "POST",
            "/api/repositories/fantasma/output-root",
            {"output_root_path": str(tmp_path / "destino")},
        )
        assert status == 404

    def test_post_malformed_url_returns_404(self, server_with_db) -> None:
        host, port, _ = server_with_db
        status, _ = _http(host, port, "POST", "/api/repositories/sem-sufixo")
        assert status == 404
