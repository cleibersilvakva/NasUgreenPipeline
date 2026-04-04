"""Configuração de logging: arquivo e console."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(logs_dir: Path, verbose: bool = False) -> logging.Logger:
    """Configura o logger raiz do pipeline.

    - Console: INFO (ou DEBUG se verbose)
    - Arquivo: DEBUG sempre
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "pipeline.log"

    logger = logging.getLogger("media_repo_pipeline")
    logger.setLevel(logging.DEBUG)

    # Limpa handlers anteriores (reentrada)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de arquivo
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Handler de console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger
