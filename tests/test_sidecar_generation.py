"""Testes de geração de sidecar."""

import json
from pathlib import Path

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.models import FileInfo
from media_repo_pipeline.sidecar_service import generate_sidecar, validate_sidecar


class TestSidecarGeneration:
    def test_sidecar_created(self, cfg: PipelineConfig, tmp_workspace: dict):
        fi = FileInfo(
            source_path="/entrada/cleiber/foto.jpg",
            rel_input_path="foto.jpg",
            repository_name_canonical="cleiber",
            hash_sha256="abcdef123456",
            media_kind="photo",
            capture_dt="2026-03-29 18:45:01",
            capture_dt_source="DateTimeOriginal",
            capture_dt_confidence="high",
            device_make="Apple",
            device_model="iPhone 15",
            software="",
            size_bytes=1000,
            extension=".jpg",
            metadata_json={"some": "data"},
        )
        organized_path = (
            cfg.output_root / "organized" / "cleiber" / "photos"
            / "2026" / "03-Março" / "29" / "foto.jpg"
        )
        organized_path.parent.mkdir(parents=True, exist_ok=True)
        organized_path.write_bytes(b"fake")

        sidecar_path = generate_sidecar(fi, organized_path, cfg)
        assert sidecar_path.exists()
        assert sidecar_path.suffix == ".json"

        # Valida conteúdo
        with open(sidecar_path) as f:
            data = json.load(f)
        assert data["hash_sha256"] == "abcdef123456"
        assert data["repository_name_canonical"] == "cleiber"
        assert data["capture_dt"] == "2026-03-29 18:45:01"
        assert data["pipeline_version"] == cfg.pipeline_version

    def test_validate_sidecar_valid(self, tmp_workspace: dict):
        path = tmp_workspace["output"] / "test.json"
        path.write_text(json.dumps({
            "hash_sha256": "abc",
            "organized_path": "/some/path",
            "repository_name_canonical": "test",
        }))
        assert validate_sidecar(path) is True

    def test_validate_sidecar_invalid(self, tmp_workspace: dict):
        path = tmp_workspace["output"] / "broken.json"
        path.write_text("not json")
        assert validate_sidecar(path) is False

    def test_validate_sidecar_missing_fields(self, tmp_workspace: dict):
        path = tmp_workspace["output"] / "partial.json"
        path.write_text(json.dumps({"hash_sha256": "abc"}))
        assert validate_sidecar(path) is False
