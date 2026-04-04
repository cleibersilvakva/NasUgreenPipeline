"""Healthcheck de inicialização — valida dependências antes do loop principal."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from .config import PipelineConfig
from .errors import HealthCheckError

logger = logging.getLogger("media_repo_pipeline.healthcheck")


def run_healthcheck(cfg: PipelineConfig) -> None:
    """Executa todas as verificações de saúde. Lança HealthCheckError se falhar."""
    checks = [
        ("Bibliotecas Python de metadata disponíveis", _check_python_libs),
        ("Pasta de entrada existe e é legível", lambda: _check_input_dir(cfg.input_root)),
        ("Pasta de saída acessível", lambda: _check_output_dir(cfg.output_root)),
        ("Banco SQLite acessível", lambda: _check_db(cfg.sqlite_db_path)),  # type: ignore[arg-type]
        ("Espaço livre suficiente", lambda: _check_disk_space(cfg.output_root, cfg.low_disk_space_threshold_bytes)),
    ]

    failed: list[str] = []
    for name, check_fn in checks:
        try:
            check_fn()
            logger.info("✓ %s", name)
        except HealthCheckError as exc:
            logger.error("✗ %s — %s", name, exc)
            failed.append(f"{name}: {exc}")

    if failed:
        raise HealthCheckError(
            "Healthcheck falhou:\n" + "\n".join(f"  - {f}" for f in failed)
        )
    logger.info("Healthcheck concluído com sucesso.")


def _check_python_libs() -> None:
    """Verifica se Pillow e mutagen estão instalados."""
    missing = []
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow")
    try:
        import mutagen  # noqa: F401
    except ImportError:
        missing.append("mutagen")
    if missing:
        raise HealthCheckError(
            f"Bibliotecas ausentes: {', '.join(missing)}. "
            f"Instale com: pip install {' '.join(missing)}"
        )


def _check_input_dir(path: Path) -> None:
    if not path.exists():
        raise HealthCheckError(f"Pasta de entrada não existe: {path}")
    if not path.is_dir():
        raise HealthCheckError(f"Caminho de entrada não é diretório: {path}")
    if not os.access(str(path), os.R_OK):
        raise HealthCheckError(f"Sem permissão de leitura: {path}")


def _check_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not os.access(str(path), os.W_OK):
        raise HealthCheckError(f"Sem permissão de escrita: {path}")


def _check_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Tenta abrir e fechar
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:
        raise HealthCheckError(f"Não foi possível acessar banco SQLite: {exc}")


def _check_disk_space(path: Path, threshold: int) -> None:
    stat = shutil.disk_usage(str(path))
    if stat.free < threshold:
        free_gb = stat.free / (1024**3)
        thresh_gb = threshold / (1024**3)
        raise HealthCheckError(
            f"Espaço livre insuficiente: {free_gb:.1f} GiB (mínimo: {thresh_gb:.1f} GiB)"
        )
