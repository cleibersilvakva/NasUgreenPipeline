"""Teste crítico de reativação de repositório."""

from pathlib import Path

from media_repo_pipeline.constants import REPO_STATUS_ACTIVE, REPO_STATUS_INACTIVE
from media_repo_pipeline.db import Database
from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.repository_service import ensure_repository, mark_repositories_inactive


class TestReactivation:
    def test_create_deactivate_reactivate_preserves_id(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """
        Cenário obrigatório:
        1. criar entrada/cleiber → registrar repo
        2. simular ausência → marcar inactive
        3. recriar entrada/cleiber → reativar
        4. verificar que é o MESMO repositório (mesmo ID)
        """
        input_root = tmp_workspace["input"]
        cleiber_path = input_root / "cleiber"
        cleiber_path.mkdir()

        # 1. Primeiro registro
        repo1 = ensure_repository("cleiber", cleiber_path, db, cfg)
        assert repo1.status == REPO_STATUS_ACTIVE
        original_id = repo1.id

        # 2. Marcar como inativo (pasta desapareceu)
        mark_repositories_inactive(set(), db)
        repo_after_inactive = db.get_repository("cleiber")
        assert repo_after_inactive is not None
        assert repo_after_inactive.status == REPO_STATUS_INACTIVE
        assert repo_after_inactive.is_source_present is False

        # 3. Reativar (pasta reapareceu)
        repo2 = ensure_repository("cleiber", cleiber_path, db, cfg)
        assert repo2.status == REPO_STATUS_ACTIVE
        assert repo2.is_source_present is True

        # 4. Mesmo repositório lógico
        repo_final = db.get_repository("cleiber")
        assert repo_final is not None
        assert repo_final.id == original_id

    def test_no_parallel_repository_on_reactivation(self, db: Database, cfg: PipelineConfig, tmp_workspace: dict):
        """Reativação não deve criar repositório paralelo."""
        input_root = tmp_workspace["input"]
        cleiber_path = input_root / "cleiber"
        cleiber_path.mkdir()

        ensure_repository("cleiber", cleiber_path, db, cfg)
        mark_repositories_inactive(set(), db)
        ensure_repository("cleiber", cleiber_path, db, cfg)

        all_repos = db.get_all_repositories()
        cleiber_repos = [r for r in all_repos if r.repository_name_canonical == "cleiber"]
        assert len(cleiber_repos) == 1
