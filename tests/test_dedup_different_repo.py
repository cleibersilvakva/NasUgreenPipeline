"""Testes de deduplicação entre repositórios diferentes."""

from media_repo_pipeline.db import Database
from media_repo_pipeline.dedup_service import find_duplicate


class TestDedupDifferentRepo:
    def test_same_hash_different_repo_not_duplicate(self, db: Database):
        """
        Cenário obrigatório:
        Mesmo hash em repositórios diferentes NÃO é duplicata.
        """
        shared_hash = "deadbeef12345678abcdef1234567890abcdef1234567890abcdef1234567890"

        # Inserir em cleiber
        db.insert_kept_file({
            "repository_name_canonical": "cleiber",
            "hash_sha256": shared_hash,
            "canonical_destination_path": "/destino/organized/cleiber/photos/2026/03-Março/29/foto.jpg",
            "media_kind": "photo",
            "extension": ".jpg",
            "size_bytes": 1000,
        })

        # Inserir em vintage
        db.insert_kept_file({
            "repository_name_canonical": "vintage",
            "hash_sha256": shared_hash,
            "canonical_destination_path": "/destino/organized/vintage/photos/2026/03-Março/29/foto.jpg",
            "media_kind": "photo",
            "extension": ".jpg",
            "size_bytes": 1000,
        })

        # Buscar duplicata em cleiber → deve encontrar
        dup_cleiber = find_duplicate("cleiber", shared_hash, db)
        assert dup_cleiber is not None

        # Buscar duplicata em vintage → deve encontrar
        dup_vintage = find_duplicate("vintage", shared_hash, db)
        assert dup_vintage is not None

        # Mas buscar em melise → NÃO deve encontrar
        dup_melise = find_duplicate("melise", shared_hash, db)
        assert dup_melise is None

    def test_both_repos_accept_same_file(self, db: Database):
        """Ambos repositórios devem aceitar o mesmo arquivo independentemente."""
        shared_hash = "aabbccdd"

        # Primeiro: nenhum dos dois tem o hash
        assert find_duplicate("cleiber", shared_hash, db) is None
        assert find_duplicate("vintage", shared_hash, db) is None

        # Inserir em cleiber
        db.insert_kept_file({
            "repository_name_canonical": "cleiber",
            "hash_sha256": shared_hash,
            "canonical_destination_path": "/path/a",
            "media_kind": "photo",
            "extension": ".jpg",
            "size_bytes": 500,
        })

        # vintage ainda não tem duplicata
        assert find_duplicate("vintage", shared_hash, db) is None

        # cleiber agora tem
        assert find_duplicate("cleiber", shared_hash, db) is not None
