#!/usr/bin/env python3
"""CLI wrapper para limpeza segura da pasta de entrada.

A lógica principal está em media_repo_pipeline.cleanup (instalada pelo pacote).
Este script é mantido para uso avulso fora do container.

Uso:
    python tools/cleanup_input.py --db /output/db/index.db --input /input [--dry-run] [--verbose]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Tenta importar do pacote instalado; se não estiver instalado, usa o caminho relativo
try:
    from media_repo_pipeline.cleanup import run_cleanup, DEFAULT_STATUSES
except ImportError:
    # fallback para desenvolvimento sem instalação do pacote
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "cleanup_core",
        Path(__file__).parent.parent / "media_repo_pipeline" / "cleanup.py",
    )
    _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    run_cleanup = _mod.run_cleanup
    DEFAULT_STATUSES = _mod.DEFAULT_STATUSES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Limpeza segura da pasta de entrada.")
    p.add_argument("--db",      required=True, help="Caminho para o banco SQLite (index.db)")
    p.add_argument("--input",   required=True, help="Pasta de entrada (input_root)")
    p.add_argument("--statuses", nargs="+", default=list(DEFAULT_STATUSES),
                   help="Statuses aceitos para remoção (default: kept duplicate)")
    p.add_argument("--dry-run", action="store_true", help="Não apaga, apenas lista")
    p.add_argument("--verbose", action="store_true", help="Log detalhado")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logger = logging.getLogger("cleanup_input")
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    had_error = False
    for event in run_cleanup(
        db_path=Path(args.db),
        input_root=Path(args.input),
        allowed_statuses=tuple(args.statuses),
        dry_run=args.dry_run,
        verbose=args.verbose,
    ):
        t = event["type"]
        if t == "fatal":
            logger.error("FATAL: %s", event["detail"]); return 1
        elif t == "start":
            logger.info("Modo: %s | Statuses: %s | Arquivos: %d | Banco: %d",
                        "DRY-RUN" if event["dry_run"] else "REAL",
                        event["statuses"], event["total"], event["db_records"])
        elif t == "remove":
            prefix = "[DRY-RUN] removeria" if event.get("dry_run") else "✓ removido"
            logger.info("  %s  %s", prefix, event["path"])
        elif t == "warn":
            logger.warning("  ⚠ %s  %s", event["reason"], event["path"])
        elif t == "error":
            logger.error("  ✗ %s: %s", event["path"], event["detail"]); had_error = True
        elif t == "skip" and args.verbose:
            logger.debug("  SKIP(%s)  %s", event["reason"], event["path"])
        elif t == "summary":
            s = event["stats"]
            logger.info("═══ Resumo ═══")
            for k, v in s.items():
                logger.info("  %-28s %d", k + ":", v)
            if event["dry_run"]:
                logger.info("(DRY-RUN — nada foi apagado)")

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
