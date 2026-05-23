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
LABEL org.opencontainers.image.version="4.3.0"
LABEL org.opencontainers.image.description="Pipeline de ingestão e organização de mídia para NAS"

# Sem dependências apt — metadata extraído via Pillow e mutagen (Python puro)

# Instalar o pacote a partir do wheel compilado no stage anterior
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels media-repo-pipeline \
    && rm -rf /wheels

# Diretórios de montagem esperados pelos volumes
# /input   → input_root  (origem dos arquivos — recomendado somente-leitura)
# /output  → output_root (destino organizado, banco, logs, relatórios)
# /config  → pasta com o arquivo config.yaml
VOLUME ["/input", "/output", "/config"]

# Expor a porta 9090 do Dashboard
EXPOSE 9090

# Usuário não-root para segurança
RUN useradd -r -u 1000 -m pipeline
USER pipeline

ENTRYPOINT ["media-repo-pipeline-web"]

# Argumento opcional
CMD ["--config", "/config/config.yaml"]
