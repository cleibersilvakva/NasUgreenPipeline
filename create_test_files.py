#!/usr/bin/env python3
"""Cria arquivos de mídia de teste com EXIF real para testar o pipeline."""

import os
import subprocess
import sys

BASE = os.path.join(os.path.dirname(__file__), "test_data")

REPOS = {
    "cleiber": ["foto_ferias.jpg", "foto_aniversario.jpg", "foto_casamento.jpg"],
    "familia": ["img_001.jpg", "img_002.jpg"],
}

# JPEG 1x1 pixel branco mínimo válido (sem EXIF — vai para review)
MINIMAL_JPEG = bytes([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10,   # SOI + APP0 marker+len
    0x4A, 0x46, 0x49, 0x46, 0x00,          # 'JFIF\0'
    0x01, 0x01,                             # versão 1.1
    0x00, 0x00, 0x01, 0x00, 0x01,          # unidade + densidade 1x1
    0x00, 0x00,                             # sem thumbnail
    0xFF, 0xDB, 0x00, 0x43, 0x00,          # DQT
    *([8] * 64),                            # tabela quantização trivial
    0xFF, 0xC0, 0x00, 0x0B, 0x08,          # SOF0
    0x00, 0x01, 0x00, 0x01,                # 1x1
    0x01, 0x01, 0x11, 0x00,                # 1 componente
    0xFF, 0xC4, 0x00, 0x1F, 0x00,          # DHT
    0x00, 0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x0A, 0x0B,
    0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,    # SOS
    0x00, 0x00, 0x3F, 0x00, 0xF8,
    0xFF, 0xD9,                             # EOI
])


def make_jpeg(path: str, date_str: str | None = None):
    """Cria um JPEG mínimo válido e, se possível, injeta data EXIF via exiftool."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(MINIMAL_JPEG)

    if date_str:
        try:
            subprocess.run(
                [
                    "exiftool",
                    f"-DateTimeOriginal={date_str}",
                    f"-CreateDate={date_str}",
                    "-overwrite_original",
                    path,
                ],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"  aviso: não foi possível injetar EXIF em {path}: {e}")


def main():
    entrada = os.path.join(BASE, "entrada")
    destino = os.path.join(BASE, "destino")
    os.makedirs(destino, exist_ok=True)

    datas = [
        "2023:06:15 10:30:00",
        "2023:12:25 18:00:00",
        "2024:03:01 09:00:00",
        "2022:08:20 14:00:00",
        "2024:01:10 08:00:00",
    ]

    i = 0
    for repo, arquivos in REPOS.items():
        for nome in arquivos:
            caminho = os.path.join(entrada, repo, nome)
            data = datas[i % len(datas)]
            print(f"  criando {caminho} (EXIF: {data})")
            make_jpeg(caminho, date_str=data)
            i += 1

    # Criar um JPEG sem EXIF (deve ir para review)
    sem_exif = os.path.join(entrada, "cleiber", "sem_exif.jpg")
    print(f"  criando {sem_exif} (sem EXIF — vai para review)")
    make_jpeg(sem_exif, date_str=None)

    # Criar uma cópia idêntica para testar deduplicação
    import shutil
    dup_src = os.path.join(entrada, "cleiber", "foto_ferias.jpg")
    dup_dst = os.path.join(entrada, "cleiber", "foto_ferias_copia.jpg")
    shutil.copy2(dup_src, dup_dst)
    print(f"  criando {dup_dst} (cópia idêntica — deve ser detectada como duplicate)")

    print("\nArquivos criados:")
    for root, dirs, files in os.walk(entrada):
        for f in files:
            full = os.path.join(root, f)
            size = os.path.getsize(full)
            print(f"  {full}  ({size} bytes)")

    print("\nPronto! Execute o pipeline com:")
    print(f"  .venv/bin/media-repo-pipeline --config config.local.yaml --once --verbose")


if __name__ == "__main__":
    main()
