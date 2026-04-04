"""Testes de estabilidade de arquivo."""

import time
from pathlib import Path

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.file_stability import is_file_stable
from tests.conftest import create_fake_image


class TestFileStability:
    def test_stable_file(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Arquivo estável (não em cópia) deve retornar True."""
        path = tmp_workspace["input"] / "stable.jpg"
        create_fake_image(path)
        assert is_file_stable(path, cfg) is True

    def test_nonexistent_file(self, cfg: PipelineConfig, tmp_workspace: dict):
        """Arquivo inexistente deve retornar False."""
        path = tmp_workspace["input"] / "ghost.jpg"
        assert is_file_stable(path, cfg) is False
