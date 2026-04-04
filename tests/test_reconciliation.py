"""Testes de reconciliação."""

import json
from pathlib import Path

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.db import Database
from media_repo_pipeline.reconciler import reconcile


class TestReconciliation:
    def test_detect_db_record_without_file(self, db: Database, cfg: PipelineConfig):
        """Registro no banco sem arquivo no filesystem."""
        db.insert_kept_file({
            "repository_name_canonical": "cleiber",
            "hash_sha256": "ghost_hash",
            "canonical_destination_path": str(
                cfg.output_root / "organized" / "cleiber" / "photos" / "2026" / "ghost.jpg"
            ),
            "media_kind": "photo",
            "extension": ".jpg",
            "size_bytes": 1000,
        })

        issues = reconcile(db, cfg)
        types = [i["inconsistency_type"] for i in issues]
        assert "db_record_without_file" in types

    def test_detect_orphan_tmp(self, db: Database, cfg: PipelineConfig):
        """Temporários órfãos em tmp/."""
        tmp_dir = cfg.tmp_dir
        tmp_dir.mkdir(parents=True, exist_ok=True)
        orphan = tmp_dir / "orphan_file.tmp"
        orphan.write_bytes(b"orphan")

        issues = reconcile(db, cfg)
        types = [i["inconsistency_type"] for i in issues]
        assert "orphan_tmp_file" in types

    def test_clean_state_no_issues(self, db: Database, cfg: PipelineConfig):
        """Estado limpo não deve gerar inconsistências (exceto se existirem dirs extras)."""
        issues = reconcile(db, cfg)
        # Sem dados, não deve ter inconsistências de DB
        db_issues = [i for i in issues if i["inconsistency_type"] in (
            "db_record_without_file", "file_without_db_record",
        )]
        assert len(db_issues) == 0

    def test_no_sidecar_issues_when_sidecar_disabled(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Bug 3: com sidecar_enabled=False, reconciliador não deve reportar
        'organized_without_sidecar' mesmo quando existem arquivos organized sem sidecar."""
        # Cria um arquivo em organized sem sidecar correspondente
        organized_file = (
            cfg.output_root / "organized" / "repo1" / "photos" / "2026" / "03-Março" / "01" / "photo.jpg"
        )
        organized_file.parent.mkdir(parents=True, exist_ok=True)
        organized_file.write_bytes(b"fake-photo")

        # Configuração com sidecar desabilitado
        cfg_no_sidecar = PipelineConfig(
            input_root=cfg.input_root,
            output_root=cfg.output_root,
            sqlite_db_path=cfg.sqlite_db_path,
            mode="copy",
            sidecar_enabled=False,
        )

        issues = reconcile(db, cfg_no_sidecar)
        sidecar_issues = [
            i for i in issues if i["inconsistency_type"] == "organized_without_sidecar"
        ]
        assert len(sidecar_issues) == 0, (
            "Não deve haver inconsistências de sidecar quando sidecar_enabled=False"
        )

    def test_sidecar_issue_reported_when_sidecar_enabled(
        self, db: Database, cfg: PipelineConfig
    ):
        """Com sidecar_enabled=True, arquivo organized sem sidecar deve ser reportado."""
        organized_file = (
            cfg.output_root / "organized" / "repo2" / "photos" / "2026" / "01-Janeiro" / "10" / "photo.jpg"
        )
        organized_file.parent.mkdir(parents=True, exist_ok=True)
        organized_file.write_bytes(b"fake-photo")

        # cfg padrão tem sidecar_enabled=True
        issues = reconcile(db, cfg)
        sidecar_issues = [
            i for i in issues if i["inconsistency_type"] == "organized_without_sidecar"
        ]
        assert len(sidecar_issues) >= 1
