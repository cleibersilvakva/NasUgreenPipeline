# Media Repository Pipeline v4 — Guia Completo de Instalação e Uso

## Índice

1. [O que é este software](#1-o-que-é-este-software)
2. [Como funciona](#2-como-funciona)
3. [Requisitos](#3-requisitos)
4. [Instalação](#4-instalação)
5. [Configuração](#5-configuração)
6. [Estrutura de pastas gerada](#6-estrutura-de-pastas-gerada)
7. [Executando o pipeline](#7-executando-o-pipeline)
8. [Todos os argumentos da CLI](#8-todos-os-argumentos-da-cli)
9. [Variáveis de ambiente](#9-variáveis-de-ambiente)
10. [Modos de operação](#10-modos-de-operação)
11. [Como os arquivos são nomeados no destino](#11-como-os-arquivos-são-nomeados-no-destino)
12. [Deduplicação](#12-deduplicação)
13. [Sidecars](#13-sidecars)
14. [Relatórios CSV](#14-relatórios-csv)
15. [Reconciliação](#15-reconciliação)
16. [Extensões suportadas](#16-extensões-suportadas)
17. [Logs](#17-logs)
18. [Execução como serviço (systemd)](#18-execução-como-serviço-systemd)
19. [Execução como serviço (launchd — macOS)](#19-execução-como-serviço-launchd--macos)
20. [Solução de problemas](#20-solução-de-problemas)
21. [Rodando os testes](#21-rodando-os-testes)
22. [Publicando no Docker Hub e instalando no NAS](#22-publicando-no-docker-hub-e-instalando-no-nas)
23. [Dashboard web (frontend local)](#23-dashboard-web-frontend-local)

---

## 1. O que é este software

O **Media Repository Pipeline** é uma ferramenta de linha de comando que **ingere, classifica, deduplica e organiza automaticamente arquivos de mídia** (fotos e vídeos) de múltiplos repositórios de origem para um destino estruturado — ideal para uso em NAS (Network Attached Storage).

Em resumo, o pipeline faz o seguinte para cada arquivo encontrado:

```
Origem (entrada/)
    └── cleiber/
    └── familia/
    └── backup2023/

    ↓  Scan → Estabilidade → Metadata → Hash → Dedup → Decisão → Cópia/Mover

Destino (destino/)
    └── organized/    ← arquivos aceitos, organizados por repo/tipo/ano/mês/dia
    └── duplicates/   ← arquivos cujo hash já existe no mesmo repositório
    └── review/       ← arquivos com problemas de metadata ou conflito RAW/JPG
    └── corrupted/    ← arquivos ilegíveis ou corrompidos
    └── sidecars/     ← metadados JSON paralelos para cada arquivo aceito
    └── reports/      ← relatórios CSV de cada execução
    └── logs/         ← logs de execução
    └── db/           ← banco SQLite de rastreamento
```

---

## 2. Como funciona

O pipeline opera em **ciclos**. A cada ciclo:

1. **Descobre repositórios**: varre cada subpasta de primeiro nível da pasta de entrada (ex: `entrada/cleiber`, `entrada/familia`). Cada subpasta é um *repositório* independente.
2. **Escaneia arquivos**: percorre recursivamente cada repositório buscando arquivos com extensões suportadas.
3. **Verifica estabilidade**: aguarda que o arquivo pare de mudar de tamanho antes de processá-lo (evita processar arquivos que ainda estão sendo copiados para o NAS).
4. **Extrai metadados**: usa `exiftool` (para fotos/RAW) e `ffprobe` (para vídeos) para obter data de captura, câmera, etc.
5. **Calcula hash SHA-256**: garante integridade e permite deduplicação exata.
6. **Decide destino**:
   - **kept** → arquivo aceito e organizado
   - **duplicate** → hash já existe no mesmo repositório
   - **review** → conflito RAW/JPG ou data de captura ausente
   - **corrupted** → arquivo ilegível
   - **skipped** → arquivo já processado sem alterações
7. **Copia ou move** o arquivo para o caminho de destino estruturado.
8. **Gera sidecar JSON** com todos os metadados coletados.
9. **Registra no SQLite** o resultado completo.
10. **Gera relatório CSV**.

Repositórios que desaparecem da origem são marcados como `inactive_source_missing` no banco. Se a pasta reaparecer, o repositório é reativado com o mesmo ID.

---

## 3. Requisitos

### Sistema operacional
- Linux, macOS ou qualquer sistema Unix-like
- Python **3.11 ou superior**

### Ferramentas externas obrigatórias

> O pipeline **verifica a presença dessas ferramentas no `PATH` antes de iniciar** (healthcheck). Se alguma não for encontrada, o pipeline recusará a iniciar.

| Ferramenta | Finalidade | Instalação |
|---|---|---|
| `exiftool` | Leitura de metadados EXIF de fotos e arquivos RAW | `brew install exiftool` / `apt install libimage-exiftool-perl` |
| `ffprobe` | Leitura de metadados de vídeos (parte do FFmpeg) | `brew install ffmpeg` / `apt install ffmpeg` |

### Verificação rápida

```bash
exiftool -ver
ffprobe -version
```

Ambos devem responder sem erros.

---

## 4. Instalação

### 4.1 Clonar o repositório

```bash
git clone <url-do-repositorio> NASUgreen
cd NASUgreen
```

### 4.2 Criar ambiente virtual Python

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

> No Windows (PowerShell): `.venv\Scripts\Activate.ps1`

### 4.3 Instalar o pacote

```bash
# Modo desenvolvimento (recomendado para uso local)
pip install -e .

# Ou instalar dependências de produção apenas
pip install pyyaml
```

### 4.4 Verificar instalação

```bash
media-repo-pipeline --help
```

Deve exibir o help da CLI sem erros.

---

## 5. Configuração

Toda a configuração é feita via **arquivo YAML**. Use o arquivo de exemplo como ponto de partida:

```bash
cp config.example.yaml config.yaml
```

Edite `config.yaml` com seus caminhos reais:

```yaml
# config.yaml

# Pasta raiz de ENTRADA — cada subpasta é um repositório independente
input_root: /mnt/tank/entrada

# Pasta raiz de SAÍDA — toda a estrutura organizada será criada aqui
output_root: /mnt/tank/destino

# Caminho do banco SQLite (opcional — padrão: output_root/db/index.db)
sqlite_db_path: /mnt/tank/destino/db/index.db

# Modo de operação: copy (copia os arquivos) ou move (move os arquivos)
mode: copy

# Intervalo em segundos entre ciclos de scan no modo contínuo
scan_interval_seconds: 60

# Quantas vezes verificar estabilidade do arquivo antes de processá-lo
stable_check_count: 2

# Intervalo em segundos entre as verificações de estabilidade
stable_check_interval_seconds: 5

# Quantas vezes tentar reprocessar um arquivo com erro antes de desistir
max_retries_processing: 3

# Fuso horário usado como fallback quando não há data EXIF
default_timezone: America/Sao_Paulo

# Gerar arquivos sidecar JSON para cada arquivo aceito
sidecar_enabled: true

# Espaço mínimo livre em bytes (10 GiB = 10737418240). Pipeline para se for abaixo.
low_disk_space_threshold_bytes: 10737418240

# Extensões a IGNORAR completamente (nem logar, nem processar)
ignored_extensions:
  - .xmp
  - .aae
  - .thm
  - .db
  - .ini
  - .json
  - .txt
  - .xml

# Subpastas relativas a EXCLUIR do scan (dentro de cada repositório)
excluded_relative_paths:
  - .staging
  - tmp
  - cache
```

### Parâmetros opcionais avançados

| Parâmetro | Padrão | Descrição |
|---|---|---|
| `max_filename_length` | `180` | Comprimento máximo do nome de arquivo gerado |
| `external_tool_timeout_seconds` | `30` | Timeout para chamadas ao `exiftool`/`ffprobe` |
| `workers_count` | `1` | Número de workers (atualmente processamento é serial) |
| `pipeline_version` | `v4` | Identificador de versão gravado nos registros |
| `policy_version` | `v4-policy-001` | Identificador de política gravado nos registros |

---

## 6. Estrutura de pastas gerada

Após a primeira execução, a pasta `output_root` terá esta estrutura:

```
destino/
├── organized/
│   ├── cleiber/
│   │   ├── photos/
│   │   │   └── 2024/
│   │   │       └── 03-Março/
│   │   │           └── 15/
│   │   │               └── 2024-03-15_143022_IMG_0042_a1b2c3.jpg
│   │   └── videos/
│   │       └── 2024/
│   │           └── 03-Março/
│   │               └── 15/
│   │                   └── 2024-03-15_180000_VID_0001_d4e5f6.mp4
│   └── familia/
│       └── photos/
│           └── ...
├── duplicates/
│   └── cleiber/
│       └── 2024/03-Março/15/
│           └── 2024-03-15_143022_IMG_0042_a1b2c3.jpg
├── review/
│   └── (arquivos com problemas de metadata ou conflito RAW/JPG)
├── corrupted/
│   └── (arquivos ilegíveis)
├── sidecars/
│   └── cleiber/photos/2024/03-Março/15/
│       └── 2024-03-15_143022_IMG_0042_a1b2c3.jpg.json
├── reports/
│   └── run_1_20240315_143100.csv
├── logs/
│   └── pipeline.log
├── db/
│   └── index.db
└── pipeline.lock
```

---

## 7. Executando o pipeline

### Modo mais simples — execução única com arquivo de config

```bash
source .venv/bin/activate
media-repo-pipeline --config config.yaml --once
```

### Modo contínuo (loop) — ideal para serviços

```bash
media-repo-pipeline --config config.yaml
```

O pipeline escaneia, aguarda `scan_interval_seconds`, escaneia de novo, indefinidamente. Para encerrar com segurança: `Ctrl+C` ou `kill -TERM <pid>`. O pipeline concluirá o ciclo atual antes de parar.

### Dry-run — simula sem mover/copiar nada

```bash
media-repo-pipeline --config config.yaml --once --dry-run
```

Exibe o que seria feito sem alterar nenhum arquivo. Ideal para validar antes da primeira execução real.

### Modo verbose — exibe todos os detalhes no terminal

```bash
media-repo-pipeline --config config.yaml --once --verbose
```

---

## 8. Todos os argumentos da CLI

```
media-repo-pipeline [opções]
```

| Argumento | Tipo | Descrição |
|---|---|---|
| `--config CAMINHO` | string | Caminho para o arquivo YAML de configuração |
| `--input-root CAMINHO` | string | Sobrescreve `input_root` do config |
| `--output-root CAMINHO` | string | Sobrescreve `output_root` do config |
| `--db-path CAMINHO` | string | Sobrescreve `sqlite_db_path` do config |
| `--mode MODE` | `copy` ou `move` | Sobrescreve `mode` do config |
| `--once` | flag | Executa apenas um ciclo e encerra |
| `--scan-interval N` | inteiro | Sobrescreve `scan_interval_seconds` |
| `--verbose` | flag | Ativa logging detalhado |
| `--dry-run` | flag | Simula sem alterar arquivos |
| `--reconcile-only` | flag | Só executa reconciliação, sem processar arquivos |

**Prioridade**: argumentos CLI sobrescrevem variáveis de ambiente, que sobrescrevem o arquivo YAML.

---

## 9. Variáveis de ambiente

Todas as variáveis usam o prefixo `MRPL_`:

| Variável | Configuração equivalente |
|---|---|
| `MRPL_INPUT_ROOT` | `input_root` |
| `MRPL_OUTPUT_ROOT` | `output_root` |
| `MRPL_DB_PATH` | `sqlite_db_path` |
| `MRPL_MODE` | `mode` |
| `MRPL_SCAN_INTERVAL` | `scan_interval_seconds` |
| `MRPL_WORKERS` | `workers_count` |
| `MRPL_TIMEZONE` | `default_timezone` |
| `MRPL_VERBOSE` | `verbose` |

Exemplo de uso sem arquivo de config:

```bash
MRPL_INPUT_ROOT=/mnt/tank/entrada \
MRPL_OUTPUT_ROOT=/mnt/tank/destino \
MRPL_MODE=copy \
media-repo-pipeline --once
```

---

## 10. Modos de operação

### `copy` (padrão)
Copia os arquivos da origem para o destino. Os originais permanecem intactos. Recomendado para uso inicial e validação.

### `move`
Move os arquivos da origem para o destino. Os originais são removidos após cópia bem-sucedida. Use somente quando tiver certeza que o destino está correto e com backup.

---

## 11. Como os arquivos são nomeados no destino

O pipeline gera um nome padronizado para cada arquivo:

```
YYYY-MM-DD_HHMMSS_nomesanitizado_xxxxxx.ext
```

| Parte | Exemplo | Descrição |
|---|---|---|
| `YYYY-MM-DD` | `2024-03-15` | Data de captura (ou mtime como fallback) |
| `HHMMSS` | `143022` | Hora de captura |
| `nomesanitizado` | `img_0042` | Nome original do arquivo, sanitizado |
| `xxxxxx` | `a1b2c3` | 6 primeiros caracteres do hash SHA-256 |
| `.ext` | `.jpg` | Extensão original em minúsculas |

Se já existir um arquivo com o mesmo nome no destino, um sufixo numérico é adicionado automaticamente: `_1`, `_2`, etc.

---

## 12. Deduplicação

A deduplicação é feita **por repositório** via hash SHA-256:

- Se o mesmo arquivo (mesmo hash) aparecer duas vezes **no mesmo repositório**, a segunda ocorrência vai para `duplicates/`.
- Se o mesmo arquivo aparecer em **repositórios diferentes**, ambos são aceitos em `organized/` — cada repositório é tratado de forma independente.

O histórico de hashes é armazenado no banco SQLite. Isso significa que, mesmo que um arquivo original seja removido, o hash permanece registrado e futuras cópias ainda serão detectadas como duplicatas.

---

## 13. Sidecars

Para cada arquivo aceito em `organized/`, é gerado um arquivo sidecar JSON em `sidecars/` com a mesma estrutura de pastas. Exemplo:

```
organized/cleiber/photos/2024/03-Março/15/2024-03-15_143022_img_0042_a1b2c3.jpg
sidecars/cleiber/photos/2024/03-Março/15/2024-03-15_143022_img_0042_a1b2c3.jpg.json
```

Conteúdo do sidecar:

```json
{
  "source_path": "/mnt/tank/entrada/cleiber/DCIM/IMG_0042.JPG",
  "rel_input_path": "DCIM/IMG_0042.JPG",
  "repository_name_canonical": "cleiber",
  "organized_path": "/mnt/tank/destino/organized/cleiber/photos/2024/03-Março/15/...",
  "hash_sha256": "a1b2c3d4e5f6...",
  "media_kind": "photo",
  "capture_dt": "2024-03-15 14:30:22",
  "capture_dt_source": "DateTimeOriginal",
  "capture_dt_confidence": "high",
  "device_make": "Apple",
  "device_model": "iPhone 15 Pro",
  "size_bytes": 4823012,
  "extension": ".jpg",
  "processing_timestamp": "2024-03-15T17:00:00Z",
  "pipeline_version": "v4",
  "policy_version": "v4-policy-001",
  "metadata": { ... }
}
```

Para desativar a geração de sidecars: `sidecar_enabled: false` no config.

---

## 14. Relatórios CSV

Após cada ciclo, um arquivo CSV é gerado em `destino/reports/`:

```
run_1_20240315_170000.csv
```

Colunas:

| Coluna | Descrição |
|---|---|
| `source_path` | Caminho absoluto do arquivo de origem |
| `repository_name_canonical` | Nome canônico do repositório |
| `rel_input_path` | Caminho relativo dentro do repositório |
| `media_kind` | `photo` ou `video` |
| `capture_dt` | Data/hora de captura detectada |
| `capture_dt_source` | Campo EXIF que originou a data |
| `hash_sha256` | Hash SHA-256 do arquivo |
| `action` | `kept`, `duplicate`, `review`, `corrupted`, `skipped`, `error` |
| `reason` | Motivo da decisão tomada |
| `destination_path` | Caminho de destino final |
| `duplicate_of_hash` | Hash do arquivo original (se duplicata) |
| `pipeline_version` | Versão do pipeline |
| `policy_version` | Versão de política |
| `error` | Mensagem de erro (se houver) |

---

## 15. Reconciliação

O modo reconciliação verifica a consistência entre o banco de dados e os arquivos em disco, detectando:

- Registros no banco sem arquivo correspondente em `organized/`
- Arquivos em `organized/` sem registro no banco
- Arquivos em `organized/` sem sidecar correspondente
- Sidecars sem arquivo organizado correspondente
- Arquivos orphãos em `tmp/`

Para executar isoladamente:

```bash
media-repo-pipeline --config config.yaml --reconcile-only
```

As inconsistências são registradas no banco (tabela `inconsistencies`) e exibidas no log como warnings.

---

## 16. Extensões suportadas

### Fotos
`.jpg` `.jpeg` `.png` `.heic` `.heif` `.tiff` `.tif` `.bmp` `.webp` `.gif` `.avif`

### Vídeos
`.mp4` `.mov` `.avi` `.mkv` `.mts` `.m2ts` `.3gp` `.wmv` `.flv` `.webm` `.m4v` `.mpg` `.mpeg`

### RAW
`.arw` `.cr2` `.cr3` `.dng` `.nef` `.orf` `.raf` `.rw2` `.srw` `.pef` `.raw`

Para restringir a um subconjunto, use a chave `supported_extensions` no config:

```yaml
supported_extensions:
  - .jpg
  - .jpeg
  - .heic
  - .mp4
  - .mov
```

---

## 17. Logs

Os logs são gravados em `destino/logs/pipeline.log` e também no terminal.

Níveis:
- **INFO**: progresso normal (arquivos processados, ciclos iniciados/encerrados)
- **WARNING**: situações recuperáveis (arquivo instável, falha de metadata)
- **ERROR**: erros sérios (falha de hash, healthcheck)

Para ver mais detalhes, ative `--verbose` ou `verbose: true` no config (ativa nível DEBUG).

---

## 18. Execução como serviço (systemd)

Ideal para rodar continuamente em um servidor Linux ou NAS com systemd.

Crie o arquivo `/etc/systemd/system/media-pipeline.service`:

```ini
[Unit]
Description=Media Repository Pipeline v4
After=network.target

[Service]
Type=simple
User=cleiber
WorkingDirectory=/opt/NASUgreen
ExecStart=/opt/NASUgreen/.venv/bin/media-repo-pipeline --config /opt/NASUgreen/config.yaml
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Ativar e iniciar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable media-pipeline
sudo systemctl start media-pipeline

# Acompanhar logs em tempo real
sudo journalctl -u media-pipeline -f
```

Encerrar com segurança (aguarda o ciclo atual terminar):

```bash
sudo systemctl stop media-pipeline
```

---

## 19. Execução como serviço (launchd — macOS)

Para rodar automaticamente no macOS.

Crie o arquivo `~/Library/LaunchAgents/com.cleiber.media-pipeline.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cleiber.media-pipeline</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/cleibersilva/Dev/NASUgreen/.venv/bin/media-repo-pipeline</string>
        <string>--config</string>
        <string>/Users/cleibersilva/Dev/NASUgreen/config.yaml</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/media-pipeline.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/media-pipeline.err</string>
</dict>
</plist>
```

Ativar:

```bash
launchctl load ~/Library/LaunchAgents/com.cleiber.media-pipeline.plist
```

Encerrar:

```bash
launchctl unload ~/Library/LaunchAgents/com.cleiber.media-pipeline.plist
```

---

## 20. Solução de problemas

### "exiftool não encontrado no PATH"
```bash
# macOS
brew install exiftool

# Debian/Ubuntu
sudo apt install libimage-exiftool-perl
```

### "ffprobe não encontrado no PATH"
```bash
# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg
```

### "Espaço livre insuficiente"
O pipeline requer ao menos 10 GiB livres (configurável via `low_disk_space_threshold_bytes`). Libere espaço ou reduza o threshold.

### "Pipeline já está em execução" / erro de lock
O arquivo `destino/pipeline.lock` previne execuções paralelas. Se o pipeline travou ou foi encerrado abruptamente, remova o lock manualmente:

```bash
rm /mnt/tank/destino/pipeline.lock
```

### Arquivo processado novamente quando não deveria
O pipeline rastreia arquivos pelo par `(caminho, tamanho, mtime)`. Se o mtime mudar (por exemplo, ao copiar arquivos de outro sistema), o arquivo será reprocessado. Isso é seguro — o hash prevenirá duplicatas.

### Arquivo vai para `review/` sem motivo claro
Causas comuns:
- Data EXIF ausente ou inválida
- Conflito entre arquivo RAW e JPG do mesmo disparo (camera que grava ambos)
- Nome de arquivo não identifiable

Verifique o CSV de relatório para a coluna `reason`.

---

## 21. Rodando os testes

### Instalar dependências de desenvolvimento

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

### Rodar todos os testes

```bash
python -m pytest tests/ -v
```

### Rodar com relatório de cobertura

```bash
python -m pytest tests/ -v --cov=media_repo_pipeline --cov-report=term-missing
```

### Rodar um teste específico

```bash
python -m pytest tests/test_dedup_same_repo.py -v
```

A suíte contém 42 testes cobrindo:

| Arquivo de teste | O que testa |
|---|---|
| `test_dedup_same_repo.py` | Deduplicação dentro do mesmo repositório |
| `test_dedup_different_repo.py` | Comportamento de dedup entre repositórios distintos |
| `test_failure_recovery.py` | Recuperação de falhas (arquivo sumiu, destino já existe) |
| `test_file_stability.py` | Detecção de arquivos ainda em cópia |
| `test_month_map.py` | Mapeamento de número do mês para nome em português |
| `test_path_generation.py` | Geração de caminhos de destino organizados |
| `test_reactivation.py` | Reativação de repositório inativo com preservação de ID |
| `test_reconciliation.py` | Detecção de inconsistências banco vs. filesystem |
| `test_repository_identity.py` | Canonicalização de nomes de repositórios |
| `test_sidecar_generation.py` | Geração e validação de sidecars JSON |
| `test_sqlite_integrity.py` | Integridade das operações no banco SQLite |
| `test_reporting.py` | Contagem de estatísticas no relatório de execução |

---

## 22. Publicando no Docker Hub e instalando no NAS

Esta seção cobre o ciclo completo: **build local → publicação no Docker Hub → instalação no NAS UGREEN**.

### Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado na sua máquina de desenvolvimento
- Conta gratuita no [Docker Hub](https://hub.docker.com)
- Docker (ou Portainer) disponível no NAS UGREEN

---

### Passo 1 — Build da imagem

No terminal, dentro da raiz do projeto:

```bash
docker build -t SEU_USUARIO/media-repo-pipeline:4.0.0 .
docker tag SEU_USUARIO/media-repo-pipeline:4.0.0 SEU_USUARIO/media-repo-pipeline:latest
```

> Substitua `SEU_USUARIO` pelo seu username do Docker Hub (ex: `cleibersilva`).

Para verificar se a imagem foi gerada corretamente:

```bash
docker images | grep media-repo-pipeline
```

---

### Passo 2 — Login e push para o Docker Hub

```bash
docker login
docker push SEU_USUARIO/media-repo-pipeline:4.0.0
docker push SEU_USUARIO/media-repo-pipeline:latest
```

Após o push, a imagem estará disponível publicamente em:
`https://hub.docker.com/r/SEU_USUARIO/media-repo-pipeline`

---

### Passo 3 — Configurar o `docker-compose.yml` no NAS

O projeto já inclui um `docker-compose.yml` pronto. Você só precisa alterar **uma linha**: substituir o valor de `image:` pelo nome da imagem que você publicou no Hub.

Abra o `docker-compose.yml` na raiz do projeto e troque:

```yaml
image: media-repo-pipeline:4.0.0
```

por:

```yaml
image: SEU_USUARIO/media-repo-pipeline:4.0.0
```

O arquivo completo ficará assim:

```yaml
services:
  media-pipeline:
    image: SEU_USUARIO/media-repo-pipeline:4.0.0  # ← imagem do Docker Hub
    container_name: media-pipeline
    restart: unless-stopped

    volumes:
      # Ajuste os caminhos à esquerda para os volumes reais do NAS
      - /volume1/entrada:/input:ro
      - /volume1/destino:/output
      - ./config.docker.yaml:/config/config.yaml:ro

    environment:
      - TZ=America/Sao_Paulo

    healthcheck:
      test: ["CMD", "pgrep", "-f", "media-repo-pipeline"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 15s

    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

---

### Passo 4 — Copiar arquivos para o NAS e subir

Copie para o NAS os arquivos necessários:

```
docker-compose.yml
config.docker.yaml   ← renomeie para config.yaml se preferir
```

No NAS (via SSH ou terminal do Portainer):

```bash
# Baixa a imagem e sobe o container em background
docker compose up -d

# Acompanhar logs em tempo real
docker compose logs -f

# Verificar status
docker compose ps
```

---

### Passo 5 — Atualizar para uma nova versão

Quando uma nova versão for lançada, no computador de desenvolvimento:

```bash
# 1. Build e push da nova versão
docker build -t SEU_USUARIO/media-repo-pipeline:4.1.0 .
docker tag SEU_USUARIO/media-repo-pipeline:4.1.0 SEU_USUARIO/media-repo-pipeline:latest
docker push SEU_USUARIO/media-repo-pipeline:4.1.0
docker push SEU_USUARIO/media-repo-pipeline:latest
```

No NAS:

```bash
# 2. Atualizar a tag no docker-compose.yml, depois:
docker compose pull
docker compose up -d
```

---

### Dica — Automatizar com GitHub Actions

Se o código estiver em um repositório GitHub, crie `.github/workflows/docker.yml` para fazer build e push automaticamente a cada nova tag `v*.*.*`:

```yaml
name: Publish Docker Image

on:
  push:
    tags:
      - 'v*.*.*'

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            ${{ secrets.DOCKERHUB_USERNAME }}/media-repo-pipeline:${{ github.ref_name }}
            ${{ secrets.DOCKERHUB_USERNAME }}/media-repo-pipeline:latest
```

Adicionando os secrets `DOCKERHUB_USERNAME` e `DOCKERHUB_TOKEN` nas configurações do repositório GitHub, cada `git push --tags` dispara o build e push automaticamente.

---

## 23. Dashboard web (frontend local)

O NASUgreen inclui um **dashboard web leve** para acompanhar execuções em tempo real, navegar nos arquivos das pastas de entrada/saída e acionar o pipeline manualmente — tudo pelo navegador, sem dependências externas além do Python já instalado.

### O que o dashboard oferece

- **Log em tempo real** (Server-Sent Events) enquanto o pipeline processa arquivos
- **Barra de estatísticas** atualizada ao vivo: vistos / aceitos / duplicatas / revisão / ignorados / erros
- **Botões Executar / Parar** para acionar o pipeline sem abrir o terminal
- **Navegador de arquivos** das pastas de entrada e saída, com alternância entre visão em grade e lista
- **Thumbnails lazy-load** para imagens (requer Pillow opcional)
- **Último resultado** do banco SQLite exibido no rodapé

### Requisitos

O servidor usa apenas a **biblioteca padrão do Python 3.11** — nenhuma instalação adicional é necessária para funcionar.

Pillow é **opcional**: se estiver instalado, os thumbnails são gerados; caso contrário, o frontend exibe um ícone no lugar.

```bash
# Opcional — habilitar thumbnails de imagem
pip install pillow

# Opcional — suporte a HEIC/HEIF nos thumbnails
pip install pillow-heif
```

### Instalação

O dashboard já vem junto com o projeto. Não há passo extra de instalação além do projeto principal.

Verifique se o entry point está disponível:

```bash
source .venv/bin/activate
media-repo-pipeline-web --help
```

Se o comando não for encontrado, reinstale o pacote em modo desenvolvimento:

```bash
pip install -e .
```

### Iniciando o dashboard

#### Forma básica

```bash
source .venv/bin/activate
media-repo-pipeline-web --config config.yaml
```

Acesse em: `http://127.0.0.1:8765`

#### Alterando porta e host

```bash
# Porta personalizada
media-repo-pipeline-web --config config.yaml --port 9000

# Acessível na rede local (NAS ou outro computador)
media-repo-pipeline-web --config config.yaml --host 0.0.0.0 --port 9000
```

> **Atenção**: usar `--host 0.0.0.0` expõe o dashboard para todos os dispositivos na rede. Faça isso somente em redes confiáveis. O servidor não implementa autenticação.

#### Todos os argumentos

| Argumento | Padrão | Descrição |
|---|---|---|
| `--config CAMINHO` | auto-detectado | Arquivo YAML de configuração do pipeline |
| `--host HOST` | `127.0.0.1` | Interface de rede para escutar |
| `--port PORTA` | `8765` | Porta TCP |

Quando `--config` é omitido, o servidor tenta encontrar automaticamente um dos seguintes arquivos no diretório atual: `config.yaml`, `config.docker.yaml`, `config.example.yaml`.

### Rodando em segundo plano (macOS — launchd)

Crie `~/Library/LaunchAgents/com.cleiber.nasugreen-web.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cleiber.nasugreen-web</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/cleibersilva/Dev/NASUgreen/.venv/bin/media-repo-pipeline-web</string>
        <string>--config</string>
        <string>/Users/cleibersilva/Dev/NASUgreen/config.yaml</string>
        <string>--port</string>
        <string>9000</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/nasugreen-web.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/nasugreen-web.err</string>
</dict>
</plist>
```

Ativar:

```bash
launchctl load ~/Library/LaunchAgents/com.cleiber.nasugreen-web.plist
```

Desativar:

```bash
launchctl unload ~/Library/LaunchAgents/com.cleiber.nasugreen-web.plist
```

### Rodando em segundo plano (Linux — systemd)

Crie `/etc/systemd/system/nasugreen-web.service`:

```ini
[Unit]
Description=NASUgreen Dashboard Web
After=network.target

[Service]
Type=simple
User=cleiber
WorkingDirectory=/opt/NASUgreen
ExecStart=/opt/NASUgreen/.venv/bin/media-repo-pipeline-web --config /opt/NASUgreen/config.yaml --host 0.0.0.0 --port 9000
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Ativar e iniciar:

```bash
sudo systemctl daemon-reload
sudo systemctl enable nasugreen-web
sudo systemctl start nasugreen-web

# Verificar status
sudo systemctl status nasugreen-web

# Acompanhar logs
sudo journalctl -u nasugreen-web -f
```

### Rodando com Docker

O `docker-compose.yml` do projeto não inclui o dashboard por padrão. Para adicioná-lo, acrescente o serviço abaixo ao seu `docker-compose.yml`:

```yaml
  web:
    build: .
    command: media-repo-pipeline-web --config /app/config.yaml --host 0.0.0.0 --port 9000
    ports:
      - "9000:9000"
    volumes:
      - ./config.docker.yaml:/app/config.yaml:ro
      - /mnt/tank/entrada:/mnt/tank/entrada
      - /mnt/tank/destino:/mnt/tank/destino
    restart: unless-stopped
```

Depois:

```bash
docker compose up -d web
```

Acesse em: `http://<ip-do-nas>:9000`

### Solução de problemas do dashboard

**Porta já em uso**
```bash
# Verificar qual processo está usando a porta
lsof -i :9000
# Trocar para outra porta
media-repo-pipeline-web --config config.yaml --port 9001
```

**Dashboard abre mas o pipeline não inicia ao clicar em Executar**
- Confirme que o arquivo de configuração passado em `--config` existe e está correto
- O dashboard usa o mesmo `config.yaml` do pipeline; verifique se `input_root` e `output_root` são caminhos válidos e acessíveis pelo processo

**Thumbnails não aparecem (ícone no lugar da imagem)**
- Pillow não está instalado: `pip install pillow`
- Para HEIC/HEIF: `pip install pillow-heif`
- Em ambientes com proxy corporativo bloqueando PyPI, isso é esperado — o dashboard funciona normalmente, apenas sem preview de imagem

**Logs param de atualizar**
- O navegador mantém uma conexão SSE aberta. Se o pipeline encerrar e o botão Executar for pressionado novamente, os logs voltam a fluir automaticamente
- Recarregar a página (`F5`) reconecta o SSE se necessário
