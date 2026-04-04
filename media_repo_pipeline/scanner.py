"""Scanner — enumera repositórios e arquivos na origem."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PipelineConfig
from .constants import SUPPORTED_EXTENSIONS
from .utils import canonicalize_repo_name

logger = logging.getLogger("media_repo_pipeline.scanner")


def discover_repositories(cfg: PipelineConfig) -> list[tuple[str, Path]]:
    """Descobre subpastas de primeiro nível na origem.

    Retorna lista de (nome_bruto, caminho_absoluto).
    """
    input_root = cfg.input_root
    if not input_root.is_dir():
        logger.warning("Pasta de entrada não existe: %s", input_root)
        return []

    repos: list[tuple[str, Path]] = []
    for child in sorted(input_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        canonical = canonicalize_repo_name(child.name)
        if not canonical:
            logger.warning("Subpasta com nome inválido ignorada: %s", child.name)
            continue
        repos.append((child.name, child))

    logger.info("Repositórios descobertos: %d", len(repos))
    return repos


def scan_files(
    repo_path: Path,
    cfg: PipelineConfig,
) -> list[Path]:
    """Lista recursivamente todos os arquivos elegíveis dentro de um repositório.

    Aplica filtros de extensão e exclusões configuradas.
    """
    supported = cfg.supported_extensions or SUPPORTED_EXTENSIONS
    ignored = cfg.ignored_extensions
    excluded_paths = {p.lower() for p in cfg.excluded_relative_paths}

    files: list[Path] = []
    for item in repo_path.rglob("*"):
        if not item.is_file():
            continue

        # Verifica exclusão por caminho relativo
        rel = item.relative_to(repo_path)
        if _is_excluded(rel, excluded_paths):
            continue

        ext = item.suffix.lower()

        # Ignora extensões ignoradas
        if ignored and ext in ignored:
            continue

        # Apenas extensões suportadas
        if ext not in supported:
            continue

        files.append(item)

    logger.debug("Arquivos elegíveis em '%s': %d", repo_path.name, len(files))
    return files


def _is_excluded(rel_path: Path, excluded: set[str]) -> bool:
    """Verifica se algum componente do caminho relativo está na lista de exclusão."""
    for part in rel_path.parts:
        if part.lower() in excluded:
            return True
    return False
