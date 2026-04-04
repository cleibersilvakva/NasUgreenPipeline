"""Geração de relatórios CSV e resumos por execução."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .models import ProcessingResult

logger = logging.getLogger("media_repo_pipeline.reporting")


class RunReport:
    """Acumula resultados e gera relatórios de uma execução."""

    def __init__(self, run_id: int, cfg: PipelineConfig) -> None:
        self.run_id = run_id
        self.cfg = cfg
        self.results: list[ProcessingResult] = []
        self.stats: dict[str, int] = {
            "seen": 0,
            "supported": 0,
            "kept": 0,
            "duplicate": 0,
            "review": 0,
            "corrupted": 0,
            "skipped": 0,
            "error": 0,
            "disk_kept": -1,  # -1 = não verificado ainda
        }
        # Rastreia destinos confirmados nesta execução para verificação em disco
        self._kept_destinations: list[Path] = []

    def add_result(self, result: ProcessingResult) -> None:
        self.results.append(result)
        if result.decision:
            action = result.decision.action
            if action in self.stats:
                self.stats[action] += 1
            # Rastrear destinos de kept para validação em disco
            if action == "kept" and result.success and result.decision.destination:
                dest = Path(result.decision.destination)
                self._kept_destinations.append(dest)

    def increment(self, key: str, count: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + count

    def verify_disk_count(self) -> dict[str, Any]:
        """Conta arquivos fisicamente presentes em disco dos kept desta execução.

        Verifica cada destination_path registrado:
        - Se existe em disco
        - Se tamanho bate com o FileInfo registrado

        Retorna dict com: ok (bool), kept_in_db, present_on_disk, missing, size_mismatch.
        """
        kept_in_db = self.stats.get("kept", 0)
        present = 0
        missing: list[str] = []
        size_mismatch: list[str] = []

        # Mapa de destino → size_bytes a partir dos resultados
        dest_to_size: dict[Path, int] = {}
        for r in self.results:
            if (
                r.success
                and r.decision
                and r.decision.action == "kept"
                and r.decision.destination
                and r.file_info
            ):
                dest_to_size[Path(r.decision.destination)] = r.file_info.size_bytes

        for dest, expected_size in dest_to_size.items():
            try:
                actual_size = dest.stat().st_size
                if actual_size == expected_size:
                    present += 1
                else:
                    size_mismatch.append(
                        f"{dest.name} (esperado={expected_size}, disco={actual_size})"
                    )
            except OSError:
                missing.append(str(dest))

        self.stats["disk_kept"] = present
        ok = (present == kept_in_db) and not size_mismatch

        if missing:
            for m in missing:
                logger.warning("⚠ Arquivo kept ausente em disco: %s", m)
        if size_mismatch:
            for s in size_mismatch:
                logger.warning("⚠ Tamanho divergente em disco: %s", s)

        return {
            "ok": ok,
            "kept_in_db": kept_in_db,
            "present_on_disk": present,
            "missing": missing,
            "size_mismatch": size_mismatch,
        }

    def generate_csv(self) -> Path:
        """Gera CSV com os resultados da execução."""
        reports_dir = self.cfg.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        csv_path = reports_dir / f"run_{self.run_id}_{ts}.csv"

        fieldnames = [
            "source_path",
            "repository_name_canonical",
            "rel_input_path",
            "media_kind",
            "capture_dt",
            "capture_dt_source",
            "hash_sha256",
            "action",
            "reason",
            "destination_path",
            "duplicate_of_hash",
            "pipeline_version",
            "policy_version",
            "error",
        ]

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self.results:
                fi = r.file_info
                dec = r.decision
                row = {
                    "source_path": fi.source_path if fi else "",
                    "repository_name_canonical": fi.repository_name_canonical if fi else "",
                    "rel_input_path": fi.rel_input_path if fi else "",
                    "media_kind": fi.media_kind if fi else "",
                    "capture_dt": fi.capture_dt if fi else "",
                    "capture_dt_source": fi.capture_dt_source if fi else "",
                    "hash_sha256": fi.hash_sha256 if fi else "",
                    "action": dec.action if dec else "",
                    "reason": dec.reason if dec else "",
                    "destination_path": dec.destination if dec else "",
                    "duplicate_of_hash": dec.duplicate_of_hash if dec else "",
                    "pipeline_version": self.cfg.pipeline_version,
                    "policy_version": self.cfg.policy_version,
                    "error": r.error_message,
                }
                writer.writerow(row)

        logger.info("Relatório CSV gerado: %s", csv_path)
        return csv_path

    def summary(self) -> str:
        """Retorna resumo textual da execução."""
        kept = self.stats.get("kept", 0)
        disk_kept = self.stats.get("disk_kept", -1)

        if disk_kept == -1:
            disk_line = "  Verificação em disco:  (não executada)"
            integrity_line = ""
        elif disk_kept == kept:
            disk_line = f"  Arquivos em disco:     {disk_kept}  ✓ BATE com kept"
            integrity_line = ""
        else:
            diff = kept - disk_kept
            disk_line = f"  Arquivos em disco:     {disk_kept}  ✗ DIVERGÊNCIA: {diff} arquivo(s) ausente(s)!"
            integrity_line = "  ⚠ Verifique os logs — arquivos podem não ter sido gravados corretamente."

        lines = [
            f"═══ Resumo da execução (run {self.run_id}) ═══",
            f"  Arquivos vistos:      {self.stats.get('seen', 0)}",
            f"  Arquivos suportados:  {self.stats.get('supported', 0)}",
            f"  Aceitos (kept):       {kept}",
            disk_line,
        ]
        if integrity_line:
            lines.append(integrity_line)
        lines += [
            f"  Duplicatas:           {self.stats.get('duplicate', 0)}",
            f"  Review:               {self.stats.get('review', 0)}",
            f"  Corrompidos:          {self.stats.get('corrupted', 0)}",
            f"  Pulados (skipped):    {self.stats.get('skipped', 0)}",
            f"  Erros:                {self.stats.get('error', 0)}",
            "═══════════════════════════════════════════",
        ]
        return "\n".join(lines)

