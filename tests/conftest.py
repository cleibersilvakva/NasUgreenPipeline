"""Fixtures compartilhadas para os testes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.db import Database


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> dict[str, Path]:
    """Cria workspace temporário com pastas de entrada e saída."""
    input_root = tmp_path / "entrada"
    output_root = tmp_path / "destino"
    input_root.mkdir()
    output_root.mkdir()
    return {
        "root": tmp_path,
        "input": input_root,
        "output": output_root,
    }


@pytest.fixture
def cfg(tmp_workspace: dict[str, Path]) -> PipelineConfig:
    """Configuração de teste com caminhos temporários."""
    return PipelineConfig(
        input_root=tmp_workspace["input"],
        output_root=tmp_workspace["output"],
        sqlite_db_path=tmp_workspace["output"] / "db" / "test.db",
        mode="copy",
        scan_interval_seconds=1,
        stable_check_interval_seconds=0,  # instantâneo para testes
        stable_check_count=1,
        max_retries_processing=1,
        default_timezone="America/Sao_Paulo",
        sidecar_enabled=True,
        workers_count=1,
        max_filename_length=180,
        external_tool_timeout_seconds=10,
        low_disk_space_threshold_bytes=0,
        pipeline_version="v4-test",
        policy_version="v4-test-policy",
        dry_run=False,
        run_once=True,
        verbose=False,
    )


@pytest.fixture
def db(cfg: PipelineConfig) -> Database:
    """Banco de dados de teste."""
    database = Database(cfg.sqlite_db_path)
    database.open()
    yield database  # type: ignore[misc]
    database.close()


def create_fake_image(path: Path, content: bytes = b"fake-jpeg-data-12345") -> Path:
    """Cria um arquivo fake para testes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path
