"""Testes de identidade de repositório e canonicalização."""

from media_repo_pipeline.utils import canonicalize_repo_name


class TestCanonicalizeRepoName:
    def test_simple_name(self):
        assert canonicalize_repo_name("cleiber") == "cleiber"

    def test_uppercase(self):
        assert canonicalize_repo_name("Cleiber") == "cleiber"

    def test_spaces(self):
        assert canonicalize_repo_name("  camera profissional  ") == "camera_profissional"

    def test_hyphens(self):
        assert canonicalize_repo_name("camera-profissional") == "camera_profissional"

    def test_dots(self):
        assert canonicalize_repo_name("fotos.vintage") == "fotos_vintage"

    def test_accents_removed(self):
        # Acentos devem ser removidos na canonicalização
        result = canonicalize_repo_name("câmera")
        assert result == "camera"

    def test_special_chars_removed(self):
        assert canonicalize_repo_name("fotos@#$%casa") == "fotoscasa"

    def test_multiple_separators_collapsed(self):
        assert canonicalize_repo_name("fotos---de___casa") == "fotos_de_casa"

    def test_unicode_normalization(self):
        # Caracteres Unicode complexos
        result = canonicalize_repo_name("Mélise")
        assert result == "melise"

    def test_empty_after_cleanup(self):
        assert canonicalize_repo_name("@#$%^") == ""

    def test_leading_trailing_underscores_stripped(self):
        assert canonicalize_repo_name("_test_") == "test"
