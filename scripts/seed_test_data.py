#!/usr/bin/env python3
"""
Cria dados fictícios para teste do NASUgreen Dashboard.

  entrada/  →  vários diretórios com imagens JPEG coloridas
  destino/  →  imagens espelhadas (para o browser de destino funcionar)

O banco SQLite NÃO é criado aqui — o próprio pipeline cria com o schema correto
ao rodar pela primeira vez via botão Executar.

Uso:
    python3 scripts/seed_test_data.py
"""
from __future__ import annotations
import io
import random
import shutil
from pathlib import Path

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("aviso: Pillow nao encontrado — imagens serao JPEG minimos validos")

ROOT    = Path(__file__).parent.parent
ENTRADA = ROOT / "test_data" / "entrada"
DESTINO = ROOT / "test_data" / "destino"

PALETTES = [
    (220,  50,  47),
    ( 38, 139, 210),
    ( 42, 161, 152),
    (133, 153,   0),
    (211,  54, 130),
    (181, 137,   0),
    (  0, 158, 115),
    (108, 113, 196),
    (203,  75,  22),
    ( 88, 110, 117),
]

ALBUMS = [
    ("2 - Cleiber", "2011-05-14"),
    ("2 - Cleiber", "2012-08-20"),
    ("Familia",     "2015-12-25"),
    ("Viagem",      "2019-07-04"),
    ("Random",      "2023-01-01"),
]


def _min_jpeg() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n"
        b"\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d"
        b"\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
        b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01"
        b"\x00\x00?\x00\xfb\xd0\x00\x00\x00\x00\xff\xd9"
    )


def make_jpeg(color=(88, 166, 255), label: str = "") -> bytes:
    if not HAS_PIL:
        return _min_jpeg()
    img  = Image.new("RGB", (600, 400), color=color)
    draw = ImageDraw.Draw(img)
    for i in range(0, 400, 5):
        fade = tuple(min(255, c + i // 4) for c in color)
        draw.rectangle([(0, i), (600, i + 5)], fill=fade)  # type: ignore[arg-type]
    draw.rectangle([(30, 160), (570, 240)], fill=(0, 0, 0))
    draw.text((300, 200), label, fill=(255, 255, 255), anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def reset_dirs() -> None:
    shutil.rmtree(ENTRADA, ignore_errors=True)
    # Apaga banco antigo com schema incompativel
    old_db = DESTINO / "db" / "index.db"
    if old_db.exists():
        old_db.unlink()
        print(f"   banco antigo removido: {old_db}")
    for d in (ENTRADA, DESTINO):
        d.mkdir(parents=True, exist_ok=True)
    (DESTINO / "organized").mkdir(exist_ok=True)


def seed_files() -> tuple[int, int]:
    rng   = random.Random(42)
    seq   = 100
    n_src = 0
    n_dst = 0

    for album, date_str in ALBUMS:
        src_dir = ENTRADA / album / date_str / ".picasaoriginals"
        src_dir.mkdir(parents=True, exist_ok=True)

        year, month = date_str[:4], date_str[5:7]
        dst_dir = DESTINO / "organized" / "cleiber" / "photos" / year / f"{month}-mes"
        dst_dir.mkdir(parents=True, exist_ok=True)

        for _ in range(rng.randint(5, 10)):
            seq  += 1
            color = rng.choice(PALETTES)
            data  = make_jpeg(color, f"{album}\n{date_str} #{seq}")

            (src_dir / f"{seq}.JPG").write_bytes(data)
            n_src += 1

            if rng.random() < 0.70:
                dst = dst_dir / f"{date_str.replace('-','')}_{seq}_abcdef.jpg"
                dst.write_bytes(data)
                n_dst += 1

    return n_src, n_dst


if __name__ == "__main__":
    print("Resetando pastas…")
    reset_dirs()
    print("Gerando imagens JPEG…")
    nsrc, ndst = seed_files()
    print(f"Pronto!")
    print(f"  Entrada: {ENTRADA}  ({nsrc} imagens)")
    print(f"  Destino: {DESTINO}  ({ndst} imagens espelhadas)")
    print(f"  Banco:   sera criado pelo pipeline na primeira execucao")
