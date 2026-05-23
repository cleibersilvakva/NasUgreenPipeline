# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Instalar hatchling para o build
RUN pip install --no-cache-dir hatchling

# Copiar apenas o necessário para instalar o pacote
COPY pyproject.toml ./
COPY media_repo_pipeline/ ./media_repo_pipeline/
COPY web/ ./web/

# Montar o pacote .whl
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Metadados da imagem
LABEL org.opencontainers.image.title="Media Repository Pipeline"
LABEL org.opencontainers.image.version="4.3.2"
LABEL org.opencontainers.image.description="Pipeline de ingestão e organização de mídia para NAS"

# Instala su-exec para troca segura de UID/GID em runtime (suporte PUID/PGID)
RUN apt-get update && apt-get install -y --no-install-recommends su-exec \
    && rm -rf /var/lib/apt/lists/*

# Instalar o pacote a partir do wheel compilado no stage anterior
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels media-repo-pipeline \
    && rm -rf /wheels

# Diretórios de montagem esperados pelos volumes
# /input   → input_root  (origem dos arquivos)
# /output  → output_root (destino organizado, banco, logs, relatórios)
# /config  → pasta com o arquivo config.yaml
# /db      → banco SQLite isolado (evita corrupção WAL em SMB/NFS)
RUN useradd -r -u 1000 -m pipeline \
    && mkdir -p /input /output /config /db \
    && chown pipeline:pipeline /input /output /config /db

VOLUME ["/input", "/output", "/config", "/db"]

# Expor a porta 9090 do Dashboard
EXPOSE 9090

# Entrypoint suporta PUID/PGID para compatibilidade com NAS (Synology, UGREEN)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh", "media-repo-pipeline-web"]
CMD ["--config", "/config/config.yaml"]
