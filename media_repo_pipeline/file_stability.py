"""Verificação de estabilidade de arquivo — garante que não está em cópia."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .config import PipelineConfig

logger = logging.getLogger("media_repo_pipeline.stability")


def is_file_stable(path: Path, cfg: PipelineConfig) -> bool:
    """Retorna True se o arquivo estiver estável (não em cópia).

    Verifica ``stable_check_count`` vezes, com intervalo de
    ``stable_check_interval_seconds``, se tamanho e mtime permanecem iguais.
    """
    try:
        prev_size = path.stat().st_size
        prev_mtime = path.stat().st_mtime
    except OSError:
        logger.debug("Arquivo inacessível para verificação de estabilidade: %s", path)
        return False

    for i in range(cfg.stable_check_count):
        time.sleep(cfg.stable_check_interval_seconds)
        try:
            st = path.stat()
        except OSError:
            logger.debug("Arquivo desapareceu durante verificação: %s", path)
            return False
        if st.st_size != prev_size or st.st_mtime != prev_mtime:
            logger.debug(
                "Arquivo ainda instável (checagem %d/%d): %s",
                i + 1, cfg.stable_check_count, path,
            )
            return False
        prev_size = st.st_size
        prev_mtime = st.st_mtime

    logger.debug("Arquivo estável: %s", path)
    return True
