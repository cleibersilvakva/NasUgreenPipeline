"""Mapeamento de número do mês para formato MM-NomeDoMes (pt-BR).

Nunca depende do locale do sistema.
"""

MONTH_MAP: dict[int, str] = {
    1: "01-Janeiro",
    2: "02-Fevereiro",
    3: "03-Março",
    4: "04-Abril",
    5: "05-Maio",
    6: "06-Junho",
    7: "07-Julho",
    8: "08-Agosto",
    9: "09-Setembro",
    10: "10-Outubro",
    11: "11-Novembro",
    12: "12-Dezembro",
}


def month_label(month: int) -> str:
    """Retorna o label do mês no formato ``MM-NomeDoMes``.

    Raises:
        ValueError: se *month* não estiver entre 1 e 12.
    """
    if month not in MONTH_MAP:
        raise ValueError(f"Mês inválido: {month}. Deve estar entre 1 e 12.")
    return MONTH_MAP[month]
