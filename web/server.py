"""Dashboard web — servidor stdlib puro (sem dependências externas).

Usa apenas módulos da biblioteca padrão do Python 3.11:
    http.server, threading, subprocess, json, sqlite3, pathlib, urllib.parse
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import socketserver
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

try:
    import yaml  # já é dependência do projeto principal
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# ── Estado global ──────────────────────────────────────────────────────────────

_pipeline_proc: subprocess.Popen | None = None
_pipeline_lock = threading.Lock()
_cleanup_running = False
_cleanup_lock = threading.Lock()
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()
_CONFIG_PATH: Path | None = None

_STATIC = Path(__file__).parent / "static"

# Docker: caminho do binário docker e nome da imagem
_DOCKER_BIN = "/Applications/Docker.app/Contents/Resources/bin/docker"
_DOCKER_IMAGE = "media-repo-pipeline:4.0.0"


def _has_local_tools() -> bool:
    """Verifica se exiftool e ffprobe estão disponíveis no PATH do host."""
    import shutil
    return shutil.which("exiftool") is not None and shutil.which("ffprobe") is not None

_IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
    ".gif", ".bmp", ".avif", ".tiff", ".tif",
    ".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf",
    ".raf", ".rw2", ".srw", ".pef", ".raw",
})
_VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts",
    ".3gp", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
})

# ── Config ─────────────────────────────────────────────────────────────────────


def _load_yaml() -> dict[str, Any]:
    if not _HAS_YAML:
        return {}
    candidates: list[Path | None] = [_CONFIG_PATH]
    candidates += [
        Path("config.yaml"),
        Path("config.docker.yaml"),
        Path("config.example.yaml"),
    ]
    for p in candidates:
        if p and Path(p).exists():
            with open(p, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def _get_roots() -> tuple[Path, Path]:
    cfg = _load_yaml()
    return (
        Path(cfg.get("input_root", "entrada")),
        Path(cfg.get("output_root", "destino")),
    )


def _find_config_file() -> str:
    candidates: list[Path | None] = [_CONFIG_PATH]
    candidates += [
        Path("config.yaml"),
        Path("config.docker.yaml"),
        Path("config.example.yaml"),
    ]
    for p in candidates:
        if p and Path(p).exists():
            return str(p)
    raise FileNotFoundError("Nenhum arquivo de configuração encontrado.")


# ── Segurança ──────────────────────────────────────────────────────────────────


def _safe_resolve(base: Path, rel: str) -> Path | None:
    """Resolve caminho prevenindo path traversal. Retorna None se fora de base.

    Usa comparação de prefixo com separador para evitar bypass por nome de pasta
    que começa com o mesmo prefixo (ex: /tmp/dir_evil vs /tmp/dir).
    """
    try:
        base_resolved = base.resolve()
        resolved = (base / rel).resolve()
        # Garante que resolved é exatamente base ou está dentro dele
        if resolved == base_resolved or str(resolved).startswith(str(base_resolved) + os.sep):
            return resolved
    except Exception:
        pass
    return None


# ── SSE helpers ────────────────────────────────────────────────────────────────


def _broadcast(data: dict[str, Any]) -> None:
    payload = f"data: {json.dumps(data)}\n\n".encode()
    with _sse_lock:
        dead: list[queue.Queue] = []
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


# ── Pipeline runner ────────────────────────────────────────────────────────────


def _pipeline_thread(config_path: str) -> None:
    global _pipeline_proc, _pipeline_starting
    _broadcast({"type": "status", "status": "running"})
    returncode = 1
    try:
        # No ambiente local ou em contêiner (v4+), simplesmente invoca o módulo
        cmd = [sys.executable, "-m", "media_repo_pipeline", "--config", config_path, "--once"]
        env = None

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        with _pipeline_lock:
            _pipeline_proc = proc
            _pipeline_starting = False
        if proc.stdout is None:
            raise RuntimeError("stdout do processo é None")
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                _broadcast({"type": "log", "line": line})
        proc.wait()
        returncode = proc.returncode
    except Exception as exc:
        _broadcast({"type": "log", "line": f"[ERRO] {exc}"})
    finally:
        with _pipeline_lock:
            _pipeline_proc = None
            _pipeline_starting = False
        _broadcast({"type": "status", "status": "idle", "returncode": returncode})


# ── API handlers ───────────────────────────────────────────────────────────────


def _api_status() -> dict[str, Any]:
    with _pipeline_lock:
        running = _pipeline_proc is not None or _pipeline_starting
    return {"status": "running" if running else "idle"}


def _api_run() -> tuple[int, dict[str, Any]]:
    # Mantém o lock durante toda a decisão + start da thread para evitar race condition:
    # sem isso, duas chamadas simultâneas poderiam ambas passar pela checagem antes
    # de _pipeline_proc ser atribuído pela thread recém-iniciada.
    with _pipeline_lock:
        if _pipeline_proc is not None or _pipeline_starting:
            return 409, {"error": "Pipeline já está em execução."}
        try:
            cfg_path = _find_config_file()
        except FileNotFoundError as exc:
            return 404, {"error": str(exc)}
        # Marca como running imediatamente antes de soltar o lock
        _mark_pipeline_starting()
    threading.Thread(target=_pipeline_thread, args=(cfg_path,), daemon=True).start()
    return 200, {"started": True}


# Sentinela usada para sinalizar "iniciando" sem um Popen real ainda
_STARTING = object()
_pipeline_starting = False


def _mark_pipeline_starting() -> None:
    global _pipeline_starting
    _pipeline_starting = True


def _api_stop() -> tuple[int, dict[str, Any]]:
    with _pipeline_lock:
        proc = _pipeline_proc
    if proc is None:
        return 409, {"error": "Pipeline não está em execução."}
    proc.terminate()
    return 200, {"stopped": True}


# ── Cleanup API ────────────────────────────────────────────────────────────────


def _get_db_path() -> Path | None:
    """Resolve o caminho do banco SQLite a partir da config."""
    cfg = _load_yaml()
    db = cfg.get("sqlite_db_path")
    if db:
        return Path(str(db))
    output = Path(cfg.get("output_root", "destino"))
    return output / "db" / "index.db"


def _api_cleanup_preview() -> tuple[int, dict[str, Any]]:
    """Retorna contagens para o modal de confirmação (sem apagar nada)."""
    import sqlite3 as _sq

    try:
        input_root, _ = _get_roots()
        db_path = _get_db_path()

        if db_path is None or not db_path.exists():
            return 404, {"error": "Banco de dados não encontrado."}
        if not input_root.exists():
            return 200, {"total_input": 0, "eligible": 0, "db_records": 0}

        conn = _sq.connect(str(db_path), timeout=10)
        conn.row_factory = _sq.Row
        db_records = conn.execute("SELECT COUNT(*) FROM source_state").fetchone()[0]
        eligible = conn.execute(
            """SELECT COUNT(*) FROM source_state s
               JOIN files f ON f.source_path = s.source_path
              WHERE s.last_status IN ('kept','duplicate')
                AND f.destination_path IS NOT NULL"""
        ).fetchone()[0]
        conn.close()

        total_input = sum(1 for _ in input_root.rglob("*") if _.is_file())
        return 200, {
            "total_input": total_input,
            "eligible": eligible,
            "db_records": db_records,
        }
    except Exception as exc:
        return 500, {"error": str(exc)}


def _cleanup_thread(
    dry_run: bool,
    statuses: list[str],
) -> None:
    """Executa a limpeza em background e transmite eventos via SSE + stdout."""
    global _cleanup_running
    log = logging.getLogger("nasugreen.cleanup")
    mode_tag = "DRY-RUN" if dry_run else "REAL"
    log.info("── Limpeza iniciada (%s) statuses=%s ──", mode_tag, statuses)
    _broadcast({"type": "cleanup_status", "running": True, "dry_run": dry_run})
    try:
        from media_repo_pipeline.cleanup import run_cleanup  # noqa: PLC0415

        input_root, _ = _get_roots()
        db_path = _get_db_path()

        if db_path is None:
            log.error("Banco não configurado — limpeza abortada.")
            _broadcast({"type": "cleanup_log", "line": "[ERRO] Banco não configurado."})
            return

        for event in run_cleanup(
            db_path=db_path,
            input_root=input_root,
            allowed_statuses=tuple(statuses),
            dry_run=dry_run,
        ):
            # ── log no stdout do container ─────────────────────────
            t = event.get("type", "")
            if t == "start":
                log.info("  total_input=%d  db_records=%d  elegíveis=%d",
                         event.get("total", 0), event.get("db_records", 0),
                         event.get("eligible", 0))
            elif t == "remove":
                prefix = "[DRY-RUN] removeria" if event.get("dry_run") else "✓ removido"
                log.info("  %s  %s", prefix, event.get("path", ""))
            elif t == "warn":
                log.warning("  ⚠ (%s) %s", event.get("reason", ""), event.get("path", ""))
            elif t == "error":
                log.error("  ✗ %s: %s", event.get("path", ""), event.get("detail", ""))
            elif t == "fatal":
                log.error("  FATAL: %s", event.get("detail", ""))
            elif t == "summary":
                s = event.get("stats", {})
                log.info("── Resumo ──  removido=%d  erros=%d  pulados=%d",
                         s.get("removido", 0), s.get("erro", 0),
                         s.get("pulado_sem_registro", 0) + s.get("pulado_status", 0)
                         + s.get("pulado_destino_ausente", 0) + s.get("pulado_tamanho", 0))
            # ── broadcast SSE para o browser ───────────────────────
            _broadcast({"type": "cleanup_event", "event": event})

    except Exception as exc:
        log.error("Exceção inesperada na limpeza: %s", exc, exc_info=True)
        _broadcast({"type": "cleanup_event", "event": {"type": "fatal", "detail": str(exc)}})
    finally:
        with _cleanup_lock:
            _cleanup_running = False
        log.info("── Limpeza encerrada (%s) ──", mode_tag)
        _broadcast({"type": "cleanup_status", "running": False, "dry_run": dry_run})




def _api_cleanup_run(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Dispara a limpeza em background."""
    global _cleanup_running
    with _cleanup_lock:
        if _cleanup_running:
            return 409, {"error": "Limpeza já está em execução."}
        dry_run = bool(body.get("dry_run", True))
        statuses = body.get("statuses", ["kept", "duplicate"])
        if not isinstance(statuses, list):
            statuses = ["kept", "duplicate"]
        # Filtrar statuses inválidos por segurança
        valid = {"kept", "duplicate", "review", "corrupted"}
        statuses = [s for s in statuses if s in valid]
        if not statuses:
            return 400, {"error": "Nenhum status válido fornecido."}
        _cleanup_running = True
    threading.Thread(
        target=_cleanup_thread,
        args=(dry_run, statuses),
        daemon=True,
    ).start()
    return 200, {"started": True, "dry_run": dry_run}


def _api_files(params: dict[str, str]) -> tuple[int, dict[str, Any]]:
    root = params.get("root", "input")
    path = params.get("path", "")
    ir, ou = _get_roots()
    base = ir if root == "input" else ou

    if not base.exists():
        return 200, {"entries": [], "path": path, "root": root}

    if path:
        target = _safe_resolve(base, path)
        if target is None:
            return 403, {"error": "Acesso negado."}
    else:
        target = base.resolve()

    if not target or not target.exists():
        return 404, {"error": "Pasta não encontrada."}

    entries: list[dict[str, Any]] = []
    try:
        items = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        for item in items:
            if item.name.startswith("."):
                continue
            ext = item.suffix.lower()
            try:
                stat = item.stat()
            except OSError:
                continue
            entries.append({
                "name": item.name,
                "path": str(item.relative_to(base)),
                "is_dir": item.is_dir(),
                "size": stat.st_size if item.is_file() else None,
                "mtime": stat.st_mtime,
                "ext": ext,
                "is_image": ext in _IMAGE_EXTS,
                "is_video": ext in _VIDEO_EXTS,
            })
    except PermissionError:
        return 403, {"error": "Sem permissão para acessar esta pasta."}

    return 200, {"entries": entries, "path": path, "root": root}


def _api_thumbnail(params: dict[str, str]) -> tuple[int, bytes, str]:
    """Retorna (status_code, body_bytes, content_type).

    size ≤ 800  → miniaturas (qualidade 82, otimizado)
    size > 800  → visão em lightbox (qualidade 90, sem degradação extra)
    """
    root = params.get("root", "input")
    path = params.get("path", "")
    size = int(params.get("size", "200"))
    size = max(32, min(size, 3000))   # 3000px cobre monitores 4K

    ir, ou = _get_roots()
    base = ir if root == "input" else ou
    fp = _safe_resolve(base, path)
    if fp is None or not fp.is_file():
        return 404, b"not found", "text/plain"

    try:
        from PIL import Image  # noqa: PLC0415
        from io import BytesIO  # noqa: PLC0415
        try:
            from pillow_heif import register_heif_opener  # noqa: PLC0415
            register_heif_opener()
        except ImportError:
            pass
        img = Image.open(fp)
        img.thumbnail((size, size), Image.LANCZOS)
        if img.mode not in ("RGB", "L", "RGBA"):
            img = img.convert("RGB")
        buf = BytesIO()
        fmt     = "PNG" if img.mode == "RGBA" else "JPEG"
        quality = 90 if size > 800 else 82
        img.save(buf, format=fmt, quality=quality, optimize=True)
        return 200, buf.getvalue(), f"image/{fmt.lower()}"
    except ImportError:
        # Pillow não instalado: retorna 204 → frontend mostra ícone
        return 204, b"", "text/plain"
    except Exception:
        return 204, b"", "text/plain"


def _api_repositories() -> tuple[int, dict[str, Any]]:
    """Lista todos os repositórios com status de configuração."""
    db_path = _get_db_path()
    if db_path is None or not db_path.exists():
        return 200, {"repositories": []}
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        cols = {row[1] for row in conn.execute("PRAGMA table_info(repositories)").fetchall()}
        has_output_root = "output_root_path" in cols
        rows = conn.execute("SELECT * FROM repositories ORDER BY display_name").fetchall()
        conn.close()
        repos = []
        for row in rows:
            d = dict(row)
            output_root_path = (d.get("output_root_path") or "") if has_output_root else ""
            repos.append({
                "canonical": d["repository_name_canonical"],
                "display_name": d["display_name"],
                "status": d["status"],
                "output_root_path": output_root_path,
                "pending_config": not output_root_path,
            })
        return 200, {"repositories": repos}
    except sqlite3.Error as exc:
        return 500, {"error": str(exc)}


def _api_set_repo_output_root(canonical: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Define a pasta de destino de um repositório e cria sua estrutura."""
    from datetime import datetime, timezone

    path_str = (body.get("output_root_path") or "").strip()
    if not path_str:
        return 400, {"error": "output_root_path é obrigatório."}
    path = Path(path_str)
    if not path.is_absolute():
        return 400, {"error": "O caminho deve ser absoluto (ex: /output/Maria)."}

    try:
        (path / "photos").mkdir(parents=True, exist_ok=True)
        (path / "videos").mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as exc:
        return 500, {"error": f"Não foi possível criar o diretório: {exc}"}

    db_path = _get_db_path()
    if db_path is None:
        return 500, {"error": "Banco não configurado."}
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db_path), timeout=10)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(repositories)").fetchall()}
        if "output_root_path" not in cols:
            conn.close()
            return 500, {"error": "Banco desatualizado — execute o pipeline uma vez primeiro."}
        conn.execute(
            "UPDATE repositories SET output_root_path = ?, updated_at = ? WHERE repository_name_canonical = ?",
            (path_str, now, canonical),
        )
        conn.commit()
        changes = conn.execute("SELECT changes()").fetchone()[0]
        conn.close()
        if changes == 0:
            return 404, {"error": f"Repositório '{canonical}' não encontrado."}
    except sqlite3.Error as exc:
        return 500, {"error": str(exc)}

    _broadcast({"type": "repo_configured", "canonical": canonical, "output_root_path": path_str})
    return 200, {"ok": True, "canonical": canonical, "output_root_path": path_str}


def _api_last_run() -> dict[str, Any]:
    try:
        cfg = _load_yaml()
        db_path = cfg.get("sqlite_db_path") or (
            Path(cfg.get("output_root", "destino")) / "db" / "index.db"
        )
        if not Path(str(db_path)).exists():
            return {"last_run": None}
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM processing_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return {"last_run": dict(row) if row else None}
    except Exception as exc:
        return {"last_run": None, "error": str(exc)}


# ── HTTP Handler ───────────────────────────────────────────────────────────────


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Servidor HTTP multi-threaded — necessário para que SSE não bloqueie outros requests."""
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # silencia logs de acesso
        pass

    def _send_json(self, code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, code: int, body: bytes, ct: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _parse_qs(self) -> dict[str, str]:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        return {k: v[0] for k, v in qs.items()}

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    # ── GET ──────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        p = self._path()

        # Index
        if p in ("/", ""):
            html = (_STATIC / "index.html").read_bytes()
            self._send_bytes(200, html, "text/html; charset=utf-8")
            return

        # SSE
        if p == "/api/pipeline/events":
            q: queue.Queue = queue.Queue(maxsize=500)
            with _sse_lock:
                _sse_clients.append(q)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                # Heartbeat inicial
                self.wfile.write(b'data: {"type":"ping"}\n\n')
                self.wfile.flush()
                while True:
                    try:
                        payload = q.get(timeout=20)
                        self.wfile.write(payload)
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b'data: {"type":"ping"}\n\n')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with _sse_lock:
                    try:
                        _sse_clients.remove(q)
                    except ValueError:
                        pass
            return

        # Thumbnail
        if p == "/api/thumbnail":
            code, body, ct = _api_thumbnail(self._parse_qs())
            self._send_bytes(code, body, ct)
            return

        # JSON endpoints
        routes_get: dict[str, Any] = {
            "/api/status":          lambda: (200, _api_status()),
            "/api/files":           lambda: _api_files(self._parse_qs()),
            "/api/db/last-run":     lambda: (200, _api_last_run()),
            "/api/cleanup/preview": lambda: _api_cleanup_preview(),
            "/api/repositories":    lambda: _api_repositories(),
        }
        if p in routes_get:
            result = routes_get[p]()
            if isinstance(result, tuple) and len(result) == 2:
                code, data = result
            else:
                code, data = 200, result
            self._send_json(code, data)
            return

        self._send_json(404, {"error": "Not found"})

    # ── POST ─────────────────────────────────────

    def do_POST(self) -> None:  # noqa: N802
        p = self._path()
        if p == "/api/pipeline/run":
            code, data = _api_run()
            self._send_json(code, data)
        elif p == "/api/pipeline/stop":
            code, data = _api_stop()
            self._send_json(code, data)
        elif p.startswith("/api/repositories/") and p.endswith("/output-root"):
            parts = p.split("/")
            # /api/repositories/<canonical>/output-root → 5 partes
            if len(parts) == 5 and parts[4] == "output-root":
                canonical = parts[3]
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw) if raw.strip() else {}
                except Exception:
                    body = {}
                code, data = _api_set_repo_output_root(canonical, body)
                self._send_json(code, data)
            else:
                self._send_json(404, {"error": "Not found"})
        elif p == "/api/cleanup/run":
            # Ler body JSON
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw) if raw.strip() else {}
            except Exception:
                body = {}
            code, data = _api_cleanup_run(body)
            self._send_json(code, data)
        else:
            self._send_json(404, {"error": "Not found"})

    # ── OPTIONS (CORS preflight) ──────────────────

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ── Entrypoint ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="NASUgreen Pipeline Dashboard")
    parser.add_argument("--config", default=None, help="Caminho do config YAML")
    parser.add_argument("--host", default="0.0.0.0", help="Host (padrão: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9090, help="Porta (padrão: 9090)")
    args = parser.parse_args()

    global _CONFIG_PATH
    if args.config:
        _CONFIG_PATH = Path(args.config)

    server = _ThreadedHTTPServer((args.host, args.port), Handler)
    print(f"\n  NASUgreen Dashboard → http://{args.host}:{args.port}\n")
    print("  Ctrl+C para encerrar\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
        server.server_close()


if __name__ == "__main__":
    main()
