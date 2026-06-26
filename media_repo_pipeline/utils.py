"""Funções utilitárias gerais."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_HASH_BUF_SIZE = 1024 * 1024  # 1 MiB


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(_HASH_BUF_SIZE)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def canonicalize_repo_name(raw_name: str) -> str:
    """Deriva o nome canônico de um repositório a partir do nome bruto da subpasta.

    Regras:
    - trim
    - lowercase
    - normalização unicode (NFKD → strip combining → NFC)
    - caracteres não alfanuméricos (exceto hífen e underscore) removidos
    - espaços e sequências de separadores colapsados em underscore
    """
    name = raw_name.strip().lower()
    # Normaliza unicode — decompõe e remove diacríticos opcionais
    name = unicodedata.normalize("NFKD", name)
    # Remove combining characters (acentos)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = unicodedata.normalize("NFC", name)
    # Substitui separadores comuns por underscore
    name = re.sub(r"[\s\-\.]+", "_", name)
    # Remove caracteres proibidos
    name = re.sub(r"[^a-z0-9_]", "", name)
    # Colapsa underscores consecutivos
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name


def sanitize_filename(name: str) -> str:
    """Sanitiza o nome de um arquivo removendo caracteres problemáticos."""
    name = unicodedata.normalize("NFC", name)
    # Remove caracteres de controle e separadores de caminho
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = name.strip(". ")
    return name


def truncate_filename(name: str, max_length: int, ext: str) -> str:
    """Trunca nome do arquivo mantendo a extensão, se necessário."""
    if len(name) + len(ext) <= max_length:
        return name + ext
    allowed = max_length - len(ext)
    if allowed < 1:
        allowed = 1
    return name[:allowed] + ext


def safe_destination_path(dest: Path, hash_sha256: str | None = None) -> Path:
    """Garante que o caminho de destino não sobrescreva um arquivo existente.

    Se ``hash_sha256`` for fornecido e o arquivo existente tiver o mesmo hash,
    retorna ``dest`` sem modificação — o arquivo já está corretamente no destino
    e não precisa de cópia duplicada. Caso contrário adiciona sufixo ``_N``.
    """
    if not dest.exists():
        return dest
    if hash_sha256:
        try:
            if _sha256_of_file(dest) == hash_sha256:
                return dest
        except OSError:
            pass
    stem = dest.stem
    ext = dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1
