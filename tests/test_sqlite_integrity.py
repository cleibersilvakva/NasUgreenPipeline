"""Testes de integridade do SQLite."""

from media_repo_pipeline.constants import REPO_STATUS_ACTIVE
from media_repo_pipeline.db import Database
from media_repo_pipeline.models import RepositoryRecord


class TestSQLiteIntegrity:
    def test_create_and_read_repository(self, db: Database):
        repo = RepositoryRecord(
            repository_name_canonical="test_repo",
            display_name="Test Repo",
            status=REPO_STATUS_ACTIVE,
            is_source_present=True,
            last_source_root_path="/entrada/test_repo",
        )
        repo_id = db.upsert_repository(repo)
        assert repo_id is not None

        fetched = db.get_repository("test_repo")
        assert fetched is not None
        assert fetched.repository_name_canonical == "test_repo"
        assert fetched.display_name == "Test Repo"
        assert fetched.status == REPO_STATUS_ACTIVE

    def test_unique_canonical_name(self, db: Database):
        repo = RepositoryRecord(
            repository_name_canonical="unique",
            display_name="Unique",
            status=REPO_STATUS_ACTIVE,
            is_source_present=True,
        )
        db.upsert_repository(repo)
        # Upsert com mesmo canonical deve atualizar, não duplicar
        repo.display_name = "Unique Updated"
        db.upsert_repository(repo)

        all_repos = db.get_all_repositories()
        matching = [r for r in all_repos if r.repository_name_canonical == "unique"]
        assert len(matching) == 1
        assert matching[0].display_name == "Unique Updated"

    def test_kept_files_unique_constraint(self, db: Database):
        """(repo, hash) deve ser unique."""
        db.insert_kept_file({
            "repository_name_canonical": "repo_a",
            "hash_sha256": "hash_1",
            "canonical_destination_path": "/path/a",
            "media_kind": "photo",
            "extension": ".jpg",
            "size_bytes": 100,
        })
        # Tentar inserir mesmo (repo, hash) deve falhar
        import sqlite3
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_kept_file({
                "repository_name_canonical": "repo_a",
                "hash_sha256": "hash_1",
                "canonical_destination_path": "/path/b",
                "media_kind": "photo",
                "extension": ".jpg",
                "size_bytes": 100,
            })

    def test_processing_run_lifecycle(self, db: Database):
        run_id = db.start_run("copy", "v4", "v4-policy")
        assert run_id is not None

        db.finish_run(run_id, {
            "seen": 10,
            "supported": 8,
            "kept": 5,
            "duplicate": 2,
            "review": 1,
            "corrupted": 0,
            "skipped": 2,
        })

    def test_source_state_upsert(self, db: Database):
        db.upsert_source_state({
            "source_path": "/entrada/repo/file.jpg",
            "repository_name_canonical": "repo",
            "size_bytes": 1000,
            "mtime_epoch": 1700000000.0,
            "last_hash_sha256": "aabb",
            "last_status": "kept",
            "retry_count": 0,
        })
        ss = db.get_source_state("/entrada/repo/file.jpg")
        assert ss is not None
        assert ss["size_bytes"] == 1000

        # Upsert atualiza
        db.upsert_source_state({
            "source_path": "/entrada/repo/file.jpg",
            "repository_name_canonical": "repo",
            "size_bytes": 2000,
            "mtime_epoch": 1700000001.0,
            "last_hash_sha256": "ccdd",
            "last_status": "kept",
            "retry_count": 1,
        })
        ss2 = db.get_source_state("/entrada/repo/file.jpg")
        assert ss2 is not None
        assert ss2["size_bytes"] == 2000
