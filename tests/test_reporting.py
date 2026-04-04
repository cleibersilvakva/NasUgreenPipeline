"""Testes de contagem de estatísticas no RunReport (cobre Bug 1 — double-count de skipped)."""

from __future__ import annotations

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.constants import STATUS_KEPT, STATUS_SKIPPED
from media_repo_pipeline.models import Decision, FileInfo, ProcessingResult
from media_repo_pipeline.reporting import RunReport


def _make_result(action: str, success: bool = True) -> ProcessingResult:
    fi = FileInfo(
        source_path="/entrada/repo/file.jpg",
        rel_input_path="repo/file.jpg",
        repository_name_canonical="repo",
        extension=".jpg",
        media_kind="photo",
        size_bytes=1000,
    )
    dec = Decision(action=action, reason="test")
    return ProcessingResult(success=success, file_info=fi, decision=dec)


class TestRunReport:
    def test_skipped_counted_exactly_once_via_add_result(self, cfg: PipelineConfig):
        """Bug 1: add_result não deve contar duplicado quando increment() já foi chamado.
        Com a correção, increment() manual foi removido — add_result é a única fonte."""
        report = RunReport(1, cfg)

        report.add_result(_make_result(STATUS_SKIPPED))
        report.add_result(_make_result(STATUS_SKIPPED))

        assert report.stats["skipped"] == 2, (
            f"Esperado 2 skipped, obtido {report.stats['skipped']} "
            "(possível double-count se increment() ainda estiver sendo chamado antes)"
        )

    def test_kept_counted_once(self, cfg: PipelineConfig):
        """Arquivos kept são contados exatamente uma vez."""
        report = RunReport(1, cfg)
        report.add_result(_make_result(STATUS_KEPT))
        assert report.stats["kept"] == 1

    def test_increment_seen_and_supported_independent(self, cfg: PipelineConfig):
        """increment() para 'seen' e 'supported' não interfere nos contadores de ação."""
        report = RunReport(1, cfg)
        report.increment("seen", 5)
        report.increment("supported", 3)
        report.add_result(_make_result(STATUS_SKIPPED))
        report.add_result(_make_result(STATUS_KEPT))

        assert report.stats["seen"] == 5
        assert report.stats["supported"] == 3
        assert report.stats["skipped"] == 1
        assert report.stats["kept"] == 1

    def test_no_double_count_skipped_unsupported_extension(self, cfg: PipelineConfig):
        """Bug 1: early return por extensão não suportada deve contar skipped apenas via add_result.

        Simula o que _process_single_file faz após a correção: apenas retorna o
        ProcessingResult com action=skipped, sem chamar report.increment("skipped") antes.
        """
        report = RunReport(1, cfg)

        # Simula o comportamento corrigido de _process_single_file:
        # - increment("seen") e increment("supported") NÃO são chamados para extensão não suportada
        # - apenas add_result é chamado
        result = ProcessingResult(
            success=True,
            file_info=FileInfo(
                source_path="/entrada/repo/doc.pdf",
                rel_input_path="repo/doc.pdf",
                repository_name_canonical="repo",
                extension=".pdf",
                media_kind="other",
                size_bytes=500,
            ),
            decision=Decision(action=STATUS_SKIPPED, reason="Extensão não suportada"),
        )
        report.add_result(result)

        assert report.stats["skipped"] == 1

    def test_mixed_actions_counted_correctly(self, cfg: PipelineConfig):
        """Múltiplas ações diferentes são contadas independentemente."""
        report = RunReport(1, cfg)
        for action in ("kept", "kept", "duplicate", "review", "skipped", "skipped", "skipped"):
            report.add_result(_make_result(action))

        assert report.stats["kept"] == 2
        assert report.stats["duplicate"] == 1
        assert report.stats["review"] == 1
        assert report.stats["skipped"] == 3
