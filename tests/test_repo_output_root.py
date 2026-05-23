"""Testes para a feature de pasta de destino por repositório."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.constants import (
    DIR_ORGANIZED,
    MEDIA_KIND_PHOTO,
    MEDIA_KIND_VIDEO,
    STATUS_DUPLICATE,
    STATUS_KEPT,
    STATUS_REVIEW,
)
from media_repo_pipeline.db import Database
from media_repo_pipeline.models import Decision, FileInfo, RepositoryRecord
from media_repo_pipeline.organizer import build_destination
from media_repo_pipeline.repository_service import ensure_repository
from tests.conftest import create_fake_image


# ── DB ────────────────────────────────────────────────────────────────────────


class TestDbOutputRoot:
    def test_new_repo_has_null_output_root(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Repositório recém-criado deve ter output_root_path vazio."""
        source = tmp_workspace["input"] / "maria"
        source.mkdir()

        repo = ensure_repository("maria", source, db, cfg)
        assert repo.output_root_path == ""

        stored = db.get_repository("maria")
        assert stored is not None
        assert stored.output_root_path == ""

    def test_set_repository_output_root(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """set_repository_output_root deve persistir o caminho corretamente."""
        source = tmp_workspace["input"] / "joao"
        source.mkdir()
        ensure_repository("joao", source, db, cfg)

        custom_path = str(tmp_workspace["output"] / "joao-album")
        db.set_repository_output_root("joao", custom_path)

        stored = db.get_repository("joao")
        assert stored is not None
        assert stored.output_root_path == custom_path

    def test_set_output_root_empty_clears_to_empty(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Passar string vazia limpa o campo (útil para testes de estado)."""
        source = tmp_workspace["input"] / "ana"
        source.mkdir()
        ensure_repository("ana", source, db, cfg)

        db.set_repository_output_root("ana", "/algum/caminho")
        db.set_repository_output_root("ana", "")

        stored = db.get_repository("ana")
        assert stored is not None
        assert stored.output_root_path == ""


# ── Repository service ────────────────────────────────────────────────────────


class TestRepositoryServiceOutputRoot:
    def test_brand_new_repo_is_pending(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Repositório novo não deve ter output_root_path definido."""
        source = tmp_workspace["input"] / "pedro"
        source.mkdir()

        repo = ensure_repository("pedro", source, db, cfg)
        assert repo.output_root_path == ""

    def test_brand_new_repo_does_not_create_dirs(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Repos novos não devem criar estrutura de diretórios antes de configurar destino."""
        source = tmp_workspace["input"] / "luisa"
        source.mkdir()

        ensure_repository("luisa", source, db, cfg)

        organized = cfg.output_root / DIR_ORGANIZED / "luisa"
        assert not organized.exists(), "Diretório organized não deve ser criado para repo pendente"

    def test_existing_repo_without_output_root_is_auto_migrated(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Repos existentes com output_root_path NULL devem ser migrados para o caminho default."""
        source = tmp_workspace["input"] / "carlos"
        source.mkdir()

        # Cria repo e depois força output_root_path = "" (simula banco antigo)
        ensure_repository("carlos", source, db, cfg)
        db.set_repository_output_root("carlos", "")

        stored_before = db.get_repository("carlos")
        assert stored_before is not None
        assert stored_before.output_root_path == ""

        # Segunda chamada a ensure_repository simula próximo ciclo do pipeline
        repo = ensure_repository("carlos", source, db, cfg)
        expected_default = str(cfg.output_root / DIR_ORGANIZED / "carlos")
        assert repo.output_root_path == expected_default

        stored_after = db.get_repository("carlos")
        assert stored_after is not None
        assert stored_after.output_root_path == expected_default

    def test_auto_migrated_repo_creates_dirs(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Após auto-migração, a estrutura de diretórios deve ser criada."""
        source = tmp_workspace["input"] / "fernanda"
        source.mkdir()
        ensure_repository("fernanda", source, db, cfg)
        db.set_repository_output_root("fernanda", "")  # força NULL

        ensure_repository("fernanda", source, db, cfg)  # dispara auto-migração

        default_path = cfg.output_root / DIR_ORGANIZED / "fernanda"
        assert (default_path / "photos").exists()
        assert (default_path / "videos").exists()

    def test_configured_repo_creates_custom_dirs(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Repo com output_root_path configurado cria dirs no caminho personalizado."""
        source = tmp_workspace["input"] / "rafael"
        source.mkdir()
        custom_root = tmp_workspace["output"] / "rafael-album"

        # Cria repo, depois configura caminho, depois chama ensure_repository novamente
        ensure_repository("rafael", source, db, cfg)
        db.set_repository_output_root("rafael", str(custom_root))
        ensure_repository("rafael", source, db, cfg)

        assert (custom_root / "photos").exists()
        assert (custom_root / "videos").exists()


# ── Organizer ─────────────────────────────────────────────────────────────────


class TestOrganizerCustomOutputRoot:
    def _make_photo_fi(self, canonical: str = "maria") -> FileInfo:
        return FileInfo(
            source_path=f"/entrada/{canonical}/foto.jpg",
            repository_name_canonical=canonical,
            extension=".jpg",
            media_kind=MEDIA_KIND_PHOTO,
            capture_dt="2026-05-15 10:30:00",
            hash_sha256="aabbcc112233",
            mtime_epoch=1747302600.0,
            size_bytes=1000,
        )

    def test_custom_output_root_used_for_kept(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Arquivos kept devem ir para o caminho personalizado."""
        fi = self._make_photo_fi()
        decision = Decision(action=STATUS_KEPT)
        custom_root = str(tmp_workspace["output"] / "maria-album")

        dest = build_destination(fi, decision, cfg, repo_output_root=custom_root)

        assert str(dest).startswith(custom_root)
        assert "/photos/" in str(dest)
        assert "/2026/" in str(dest)

    def test_custom_root_used_for_video_kept(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Vídeos kept devem ir para <custom_root>/videos/..."""
        fi = FileInfo(
            source_path="/entrada/joao/clip.mp4",
            repository_name_canonical="joao",
            extension=".mp4",
            media_kind=MEDIA_KIND_VIDEO,
            capture_dt="2026-05-15 10:30:00",
            hash_sha256="ddeeff445566",
            mtime_epoch=1747302600.0,
            size_bytes=50000,
        )
        decision = Decision(action=STATUS_KEPT)
        custom_root = str(tmp_workspace["output"] / "joao-album")

        dest = build_destination(fi, decision, cfg, repo_output_root=custom_root)

        assert str(dest).startswith(custom_root)
        assert "/videos/" in str(dest)

    def test_duplicates_always_use_cfg_output_root(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Duplicatas devem continuar indo para cfg.output_root, não para o custom path."""
        fi = self._make_photo_fi()
        decision = Decision(action=STATUS_DUPLICATE)
        custom_root = str(tmp_workspace["output"] / "maria-album")

        dest = build_destination(fi, decision, cfg, repo_output_root=custom_root)

        assert str(dest).startswith(str(cfg.output_root))
        assert "/duplicates/" in str(dest)
        assert custom_root not in str(dest)

    def test_review_always_use_cfg_output_root(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Review deve continuar indo para cfg.output_root."""
        fi = self._make_photo_fi()
        decision = Decision(action=STATUS_REVIEW)
        custom_root = str(tmp_workspace["output"] / "maria-album")

        dest = build_destination(fi, decision, cfg, repo_output_root=custom_root)

        assert "/review/" in str(dest)
        assert custom_root not in str(dest)

    def test_fallback_to_default_when_no_custom_root(self, cfg: PipelineConfig):
        """Sem custom root, deve usar o caminho padrão organized/<repo>/..."""
        fi = self._make_photo_fi("cleiber")
        decision = Decision(action=STATUS_KEPT)

        dest = build_destination(fi, decision, cfg, repo_output_root="")

        assert "/organized/cleiber/photos/" in str(dest)

    def test_no_repo_dir_infix_in_custom_path(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Ao usar custom root, NÃO deve haver /<canonical>/ no meio do caminho."""
        fi = self._make_photo_fi("maria")
        decision = Decision(action=STATUS_KEPT)
        custom_root = str(tmp_workspace["output"] / "maria-album")

        dest = build_destination(fi, decision, cfg, repo_output_root=custom_root)

        # O caminho não deve conter o nome do repo como subdir extra
        rel = str(dest)[len(custom_root):]
        assert "maria" not in rel.split("/")[1]  # primeiro segmento após custom_root = photos|videos


# ── Pipeline integration ──────────────────────────────────────────────────────


class TestPipelineBlocksWithoutOutputRoot:
    def test_pending_repo_does_not_process_files(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Repositório sem output_root_path deve bloquear processamento."""
        from media_repo_pipeline.main import _run_cycle
        from media_repo_pipeline.utils import canonicalize_repo_name

        # Usar nome simples para evitar transformação de hífen → underscore
        source = tmp_workspace["input"] / "novousuario"
        source.mkdir()
        create_fake_image(source / "foto.jpg")

        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        _run_cycle(db, cfg)

        canonical = canonicalize_repo_name("novousuario")
        repo = db.get_repository(canonical)
        assert repo is not None
        assert repo.output_root_path == ""

        # Nenhum arquivo deve ter sido gravado em destino
        organized = cfg.output_root / "organized"
        files_in_dest = list(organized.rglob("*.jpg")) if organized.exists() else []
        assert files_in_dest == []

    def test_configured_repo_processes_files(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Repositório com output_root_path configurado deve processar arquivos."""
        from media_repo_pipeline.main import _run_cycle
        from media_repo_pipeline.utils import canonicalize_repo_name

        source = tmp_workspace["input"] / "usuariook"
        source.mkdir()
        create_fake_image(source / "foto.jpg")

        canonical = canonicalize_repo_name("usuariook")
        ensure_repository("usuariook", source, db, cfg)
        custom_root = tmp_workspace["output"] / "usuariook-album"
        db.set_repository_output_root(canonical, str(custom_root))

        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        _run_cycle(db, cfg)

        all_files = list(custom_root.rglob("*.jpg")) if custom_root.exists() else []
        assert len(all_files) > 0, "Arquivo deveria ter sido copiado para o custom root"

    def test_files_land_in_correct_custom_subdirectory(
        self, db: Database, cfg: PipelineConfig, tmp_workspace: dict
    ):
        """Arquivo foto deve ir para <custom_root>/photos/... e não organized/."""
        from media_repo_pipeline.main import _run_cycle

        source = tmp_workspace["input"] / "beatriz"
        source.mkdir()
        create_fake_image(source / "retrato.jpg")

        ensure_repository("beatriz", source, db, cfg)
        custom_root = tmp_workspace["output"] / "beatriz-fotos"
        db.set_repository_output_root("beatriz", str(custom_root))

        import logging
        import media_repo_pipeline.main as _main
        _main.logger = logging.getLogger("test")

        _run_cycle(db, cfg)

        photos_dir = custom_root / "photos"
        found = list(photos_dir.rglob("*.jpg")) if photos_dir.exists() else []
        assert len(found) > 0, "Foto deveria estar em <custom_root>/photos/"

        # E NÃO em organized/
        organized = cfg.output_root / "organized"
        in_organized = list(organized.rglob("*.jpg")) if organized.exists() else []
        assert in_organized == [], "Não deve haver arquivo em organized/ quando custom root está definido"
