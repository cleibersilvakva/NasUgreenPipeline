#!/usr/bin/env python3
"""Limpeza segura da pasta de entrada.

Remove arquivos da pasta /input SOMENTE se:
  1. Estiverem registrados em source_state com status kept|duplicate|review|corrupted
  2. O arquivo de destino existir em disco com o mesmo tamanho

ATENÇÃO: o volume /input deve estar montado com permissão de escrita no Docker
(remova ':ro' do docker-compose.yaml para que a limpeza real funcione).
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cleanup_input")

SAFE_TO_DELETE_STATUSES = frozenset({"kept", "duplicate", "review", "corrupted"})
DEFAULT_STATUSES = ("kept", "duplicate")


# ── Leitura do banco ──────────────────────────────────────────────────────────


def _load_db(db_path: Path) -> tuple[dict[str, dict], dict[str, str]]:
    """Carrega source_state e o destino mais recente por source_path."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row

    # source_state: status e tamanho de cada arquivo de origem
    source_state: dict[str, dict] = {
        row["source_path"]: dict(row)
        for row in conn.execute("SELECT * FROM source_state").fetchall()
    }

    # Destino mais recente por source_path (MAX(id) garante o registro mais novo)
    dest_map: dict[str, str] = {}
    for row in conn.execute("""
        SELECT f.source_path, f.destination_path
        FROM files f
        WHERE f.destination_path IS NOT NULL
          AND f.id IN (
              SELECT MAX(id) FROM files
              WHERE destination_path IS NOT NULL
              GROUP BY source_path
          )
    """).fetchall():
        dest_map[row["source_path"]] = row["destination_path"]

    # Fallback para duplicatas: buscar via kept_files usando hash
    hash_to_dest: dict[str, str] = {
        row["hash_sha256"]: row["canonical_destination_path"]
        for row in conn.execute(
            "SELECT hash_sha256, canonical_destination_path FROM kept_files"
        ).fetchall()
    }

    conn.close()
    return source_state, dest_map, hash_to_dest  # type: ignore[return-value]


# ── Core ──────────────────────────────────────────────────────────────────────


def run_cleanup(
    db_path: Path,
    input_root: Path,
    allowed_statuses: tuple[str, ...] = DEFAULT_STATUSES,
    dry_run: bool = True,
    verbose: bool = False,
) -> Iterator[dict[str, Any]]:
    """Executa (ou simula) a limpeza da pasta de entrada.

    Geradora: emite dicts de evento à medida que processa.

    Eventos emitidos:
        {type: "fatal",    detail: str}
        {type: "scanning", message: str}          ← novo: feedback durante rglob
        {type: "start",    total: int, dry_run: bool, statuses: list,
                           db_records: int, eligible: int}
        {type: "progress", done: int, total: int}  ← novo: a cada 50 arquivos
        {type: "skip",     reason: str, path: str} (verbose only)
        {type: "warn",     reason: str, path: str, ...}
        {type: "remove",   path: str, dest: str, dry_run: bool}
        {type: "error",    path: str, detail: str}
        {type: "summary",  stats: dict, dry_run: bool}
    """
    allowed = frozenset(allowed_statuses)

    # Validação prévia dos statuses
    invalid = allowed - SAFE_TO_DELETE_STATUSES
    if invalid:
        yield {"type": "fatal", "detail": f"Statuses inválidos/inseguros: {sorted(invalid)}"}
        return

    if not db_path.exists():
        yield {"type": "fatal", "detail": f"Banco não encontrado: {db_path}"}
        return

    if not input_root.exists():
        yield {"type": "fatal", "detail": f"Pasta de entrada não encontrada: {input_root}"}
        return

    # (Teste de permissão no diretório raiz omitido para evitar falsos positivos
    # com ACLs do Synology NAS. Erros de permissão serão pegos individualmente no unlink.)

    # Avisa que está varrendo (pode demorar em filesystems de rede)
    yield {"type": "scanning", "message": f"Varrendo {input_root}…"}

    # Carregar banco
    try:
        source_state, dest_map, hash_to_dest = _load_db(db_path)
    except Exception as exc:
        yield {"type": "fatal", "detail": f"Erro ao ler banco: {exc}"}
        return

    # Coletar todos os arquivos de entrada
    all_input_files = sorted(f for f in input_root.rglob("*") if f.is_file())
    total = len(all_input_files)

    # Calcular elegíveis antecipadamente para o header
    eligible = sum(
        1 for f in all_input_files
        if (s := source_state.get(str(f))) and s["last_status"] in allowed
        and (dest_map.get(str(f)) or hash_to_dest.get(s.get("last_hash_sha256", "")))
    )

    yield {
        "type": "start",
        "total": total,
        "dry_run": dry_run,
        "statuses": list(allowed),
        "db_records": len(source_state),
        "eligible": eligible,
    }

    stats: dict[str, int] = {
        "removido": 0,
        "pulado_sem_registro": 0,
        "pulado_status": 0,
        "pulado_destino_ausente": 0,
        "pulado_tamanho": 0,
        "erro": 0,
    }

    for idx, src_file in enumerate(all_input_files, 1):
        # Progresso a cada 50 arquivos
        if idx % 50 == 0:
            yield {"type": "progress", "done": idx, "total": total}

        src_str = str(src_file)
        state = source_state.get(src_str)

        # 1. Sem registro no banco → não toca
        if state is None:
            stats["pulado_sem_registro"] += 1
            if verbose:
                yield {"type": "skip", "reason": "sem_registro", "path": src_str}
            continue

        # 2. Status não autorizado → não toca
        if state["last_status"] not in allowed:
            stats["pulado_status"] += 1
            if verbose:
                yield {
                    "type": "skip",
                    "reason": "status_nao_autorizado",
                    "status": state["last_status"],
                    "path": src_str,
                }
            continue

        # 3. Verificar destino no banco (tenta dest_map; fallback via hash para duplicatas)
        dest_str = dest_map.get(src_str)
        if not dest_str:
            file_hash = state.get("last_hash_sha256")
            if file_hash:
                dest_str = hash_to_dest.get(file_hash)

        if not dest_str:
            stats["pulado_destino_ausente"] += 1
            yield {"type": "warn", "reason": "sem_destino_no_banco", "path": src_str}
            continue

        # 4. Verificar destino em disco
        dest_path = Path(dest_str)
        if not dest_path.exists():
            stats["pulado_destino_ausente"] += 1
            yield {
                "type": "warn",
                "reason": "destino_ausente_em_disco",
                "path": src_str,
                "dest": dest_str,
            }
            continue

        # 5. Verificar tamanho
        try:
            src_size = src_file.stat().st_size
            dst_size = dest_path.stat().st_size
        except OSError as exc:
            stats["erro"] += 1
            yield {"type": "error", "path": src_str, "detail": f"Erro ao verificar tamanho: {exc}"}
            continue

        if src_size != dst_size:
            stats["pulado_tamanho"] += 1
            yield {
                "type": "warn",
                "reason": "tamanho_divergente",
                "path": src_str,
                "dest": dest_str,
                "src_size": src_size,
                "dst_size": dst_size,
            }
            continue

        # ✅ Seguro para remover
        if dry_run:
            stats["removido"] += 1
            yield {"type": "remove", "path": src_str, "dest": dest_str, "dry_run": True}
        else:
            try:
                src_file.unlink()
                stats["removido"] += 1
                yield {"type": "remove", "path": src_str, "dest": dest_str, "dry_run": False}
            except OSError as exc:
                stats["erro"] += 1
                yield {"type": "error", "path": src_str, "detail": f"Erro ao remover: {exc}"}

    # Remover diretórios vazios (bottom-up) — apenas no modo real
    if not dry_run:
        for dirpath in sorted(input_root.rglob("*"), reverse=True):
            if dirpath.is_dir():
                try:
                    dirpath.rmdir()
                except OSError:
                    pass

    yield {"type": "summary", "stats": stats, "dry_run": dry_run}


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Limpeza segura da pasta de entrada.")
    p.add_argument("--db",      required=True, help="Caminho para index.db")
    p.add_argument("--input",   required=True, help="Pasta de entrada (input_root)")
    p.add_argument("--statuses", nargs="+", default=list(DEFAULT_STATUSES))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
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
        elif t == "scanning":
            logger.info(event["message"])
        elif t == "start":
            logger.info(
                "Modo: %s | Statuses: %s | Total entrada: %d | No banco: %d | Elegíveis: %d",
                "DRY-RUN" if event["dry_run"] else "REAL",
                event["statuses"], event["total"],
                event["db_records"], event["eligible"],
            )
        elif t == "progress":
            logger.info("  ⏳ %d/%d arquivos avaliados…", event["done"], event["total"])
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
            logger.info("  Removidos:         %d", s["removido"])
            logger.info("  Sem registro:      %d", s["pulado_sem_registro"])
            logger.info("  Status não auth.:  %d", s["pulado_status"])
            logger.info("  Destino ausente:   %d", s["pulado_destino_ausente"])
            logger.info("  Tamanho diverge:   %d", s["pulado_tamanho"])
            logger.info("  Erros:             %d", s["erro"])
            if event["dry_run"]:
                logger.info("(DRY-RUN — nada foi apagado)")

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
