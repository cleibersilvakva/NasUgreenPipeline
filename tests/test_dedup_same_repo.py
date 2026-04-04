"""Testes de deduplicação dentro do mesmo repositório."""

from media_repo_pipeline.db import Database
from media_repo_pipeline.dedup_service import compute_sha256, find_duplicate
from tests.conftest import create_fake_image


class TestDedupSameRepo:
    def test_same_hash_same_repo_is_duplicate(self, db: Database, tmp_workspace: dict):
        """Mesmo hash no mesmo repositório = duplicata."""
        content = b"identical-image-content-12345"

        # Inserir primeiro arquivo como kept
        db.insert_kept_file({
            "repository_name_canonical": "cleiber",
            "hash_sha256": "abc123def456",
            "canonical_destination_path": "/destino/organized/cleiber/photos/2026/03-Março/29/file.jpg",
            "media_kind": "photo",
            "extension": ".jpg",
            "size_bytes": len(content),
            "capture_dt": "2026-03-29 18:45:01",
            "capture_dt_source": "DateTimeOriginal",
        })

        # Verificar duplicata
        dup = find_duplicate("cleiber", "abc123def456", db)
        assert dup is not None
        assert dup["hash_sha256"] == "abc123def456"

    def test_hash_computation_consistent(self, tmp_workspace: dict):
        """Computação de hash deve ser determinística."""
        path = tmp_workspace["input"] / "test.jpg"
        create_fake_image(path, b"consistent-content")
        h1 = compute_sha256(path)
        h2 = compute_sha256(path)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex
