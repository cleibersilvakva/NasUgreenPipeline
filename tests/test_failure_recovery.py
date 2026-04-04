"""Testes de recuperação de falhas."""

from pathlib import Path
from unittest.mock import patch

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.db import Database
from media_repo_pipeline.models import Decision, FileInfo
from media_repo_pipeline.transaction_service import process_file
from tests.conftest import create_fake_image


class TestFailureRecovery:
    def test_source_disappeared_during_processing(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Arquivo que desaparece durante processamento deve gerar erro, não crash."""
        fi = FileInfo(
            source_path=str(tmp_workspace["input"] / "gone.jpg"),
            rel_input_path="gone.jpg",
            repository_name_canonical="test",
            extension=".jpg",
            media_kind="photo",
            size_bytes=100,
            mtime_epoch=1700000000.0,
            ctime_epoch=1700000000.0,
            hash_sha256="deadbeef",
        )
        decision = Decision(action="kept", reason="test")
        dest = tmp_workspace["output"] / "organized" / "test" / "photos" / "2026" / "01-Janeiro" / "01" / "file.jpg"

        result = process_file(fi, decision, dest, db, cfg)
        assert result.success is False
        assert result.error_type is not None

    def test_dest_already_exists_gets_suffix(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Se destino já existe, safe_destination_path adiciona sufixo."""
        from media_repo_pipeline.utils import safe_destination_path
        dest = tmp_workspace["output"] / "file.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"existing")

        safe = safe_destination_path(dest)
        assert safe != dest
        assert "_1" in safe.stem

    def test_dest_file_cleaned_up_when_sidecar_fails(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Bug 2: se sidecar falhar após rename, dest_path deve ser removido (não ficar órfão)."""
        content = b"real-image-content-xyz"
        src = create_fake_image(tmp_workspace["input"] / "repo1" / "photo.jpg", content)

        fi = FileInfo(
            source_path=str(src),
            rel_input_path="repo1/photo.jpg",
            repository_name_canonical="repo1",
            repository_display_name="Repo 1",
            extension=".jpg",
            media_kind="photo",
            size_bytes=len(content),
            mtime_epoch=src.stat().st_mtime,
            ctime_epoch=src.stat().st_ctime,
            hash_sha256="ceebf889d7433b90511247e466cf14f8a82dccb0abec10c6960d364e2c8685ba",
            capture_dt="2026-01-15 10:00:00",
            capture_dt_source="DateTimeOriginal",
            capture_dt_confidence="high",
        )
        decision = Decision(action="kept", reason="test")
        dest = (
            tmp_workspace["output"]
            / "organized" / "repo1" / "photos" / "2026" / "01-Janeiro" / "15"
            / "photo_final.jpg"
        )

        from media_repo_pipeline.errors import SidecarError

        with patch(
            "media_repo_pipeline.transaction_service.generate_sidecar",
            side_effect=SidecarError("sidecar disk full"),
        ):
            result = process_file(fi, decision, dest, db, cfg)

        assert result.success is False
        assert result.error_type == "SidecarError"
        # dest_path deve ter sido removido — não pode ficar órfão
        assert not dest.exists(), "dest_path ficou órfão após falha no sidecar"

    def test_dest_file_cleaned_up_when_db_fails(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Bug 2: se gravação no banco falhar após rename, dest_path deve ser removido."""
        content = b"another-real-image"
        src = create_fake_image(tmp_workspace["input"] / "repo2" / "photo.jpg", content)

        fi = FileInfo(
            source_path=str(src),
            rel_input_path="repo2/photo.jpg",
            repository_name_canonical="repo2",
            repository_display_name="Repo 2",
            extension=".jpg",
            media_kind="photo",
            size_bytes=len(content),
            mtime_epoch=src.stat().st_mtime,
            ctime_epoch=src.stat().st_ctime,
            hash_sha256="ddeeff445566",
            capture_dt="2026-02-20 09:00:00",
            capture_dt_source="DateTimeOriginal",
            capture_dt_confidence="high",
        )
        # Desabilita sidecar para isolar a falha do banco
        cfg_no_sidecar = PipelineConfig(
            input_root=cfg.input_root,
            output_root=cfg.output_root,
            sqlite_db_path=cfg.sqlite_db_path,
            mode="copy",
            sidecar_enabled=False,
        )
        decision = Decision(action="kept", reason="test")
        dest = (
            tmp_workspace["output"]
            / "organized" / "repo2" / "photos" / "2026" / "02-Fevereiro" / "20"
            / "photo_db_fail.jpg"
        )

        with patch(
            "media_repo_pipeline.transaction_service._record_to_db",
            side_effect=Exception("DB connection lost"),
        ):
            result = process_file(fi, decision, dest, db, cfg_no_sidecar)

        assert result.success is False
        assert not dest.exists(), "dest_path ficou órfão após falha no banco"
