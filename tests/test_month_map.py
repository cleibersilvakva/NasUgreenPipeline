"""Testes do mapeamento de meses."""

import pytest

from media_repo_pipeline.month_map import MONTH_MAP, month_label


class TestMonthMap:
    def test_all_12_months_present(self):
        assert len(MONTH_MAP) == 12
        for i in range(1, 13):
            assert i in MONTH_MAP

    def test_format_pattern(self):
        for month_num, label in MONTH_MAP.items():
            # Formato: MM-NomeDoMes
            parts = label.split("-", 1)
            assert len(parts) == 2
            assert parts[0] == f"{month_num:02d}"
            assert len(parts[1]) > 0

    def test_specific_months(self):
        assert month_label(1) == "01-Janeiro"
        assert month_label(3) == "03-Março"
        assert month_label(6) == "06-Junho"
        assert month_label(12) == "12-Dezembro"

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            month_label(0)
        with pytest.raises(ValueError):
            month_label(13)
        with pytest.raises(ValueError):
            month_label(-1)
