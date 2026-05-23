# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dev dependencies (Python 3.11+)
pip install -e ".[dev]"

# Run all tests
pytest tests/

# Run a single test file
pytest tests/test_dedup_same_repo.py

# Run a specific test
pytest tests/test_dedup_same_repo.py::test_name -v

# Run the web dashboard locally
python3 -m web.server --config config.local.yaml --host 127.0.0.1 --port 9090

# Run the pipeline CLI directly
python3 -m media_repo_pipeline.main --config config.local.yaml --once --dry-run

# Docker build + run
docker compose up -d
```

## Architecture

**NASUgreen** is a media ingestion pipeline with an embedded web dashboard, written entirely in the Python standard library (no web framework). It scans input folders, deduplicates via SHA-256, extracts EXIF/audio metadata natively via Pillow and mutagen, and organizes files into `output/organized/<repo>/<photos|videos>/YYYY/MM-NomeMes/DD/`.

### Pipeline flow (per file)

`scanner.py` → `classifier.py` → `metadata_service.py` → `dedup_service.py` → `organizer.py` → `transaction_service.py`

1. **`scanner.py`** — discovers repositories under `input_root` and lists eligible files
2. **`classifier.py`** — classifies media kind (photo/video/other) and makes the `kept/duplicate/review/corrupted` decision
3. **`metadata_service.py`** — extracts `capture_dt` from EXIF (Pillow for images, mutagen for audio/video); falls back to mtime
4. **`dedup_service.py`** — computes SHA-256 hash; checks `files` table for existing hash within the same repository
5. **`organizer.py`** — builds the absolute destination path: `output/<dir>/<repo>/<subdir>/YYYY/MM-Label/DD/<timestamp>_<original>_<hash6>.ext`
6. **`transaction_service.py`** — performs atomic copy/move using a temp file + rename to avoid partial writes
7. **`main.py`** — drives the cycle loop with `ThreadPoolExecutor`; each worker thread gets its own SQLite connection

### Key modules

| Module | Responsibility |
|---|---|
| `config.py` | Loads config from YAML → env vars (`MRPL_*`) → CLI overrides |
| `db.py` | SQLite WAL with tables: `repositories`, `files`, `source_states`, `runs` |
| `models.py` | Three dataclasses: `FileInfo`, `Decision`, `ProcessingResult` |
| `lock_service.py` | File-based lock (`pipeline.lock`) prevents concurrent pipeline instances |
| `reconciler.py` | Detects DB/disk inconsistencies (files in DB but not on disk) |
| `sidecar_service.py` | Generates `.json` sidecars alongside kept files |
| `reporting.py` | Produces CSV reports and per-run summaries |
| `web/server.py` | stdlib `http.server` dashboard on port 9090 — SSE-based live log streaming |
| `web/static/index.html` | Single-page frontend (vanilla JS) |

### Output directory layout

```
output/
  organized/<repo>/photos|videos/YYYY/MM-NomeMes/DD/
  duplicates/<repo>/YYYY/MM-NomeMes/DD/
  review/<repo>/...
  corrupted/<repo>/...
  db/index.db          ← SQLite database
  logs/                ← rotating log files
  reports/             ← CSV per run
  tmp/                 ← atomic write staging
```

### Configuration

Config is a YAML file (see `config.example.yaml`). Precedence: CLI flags > `MRPL_*` env vars > YAML file. Key fields:

- `mode`: `copy` (safe default) or `move`
- `workers_count`: number of parallel threads (each gets its own DB connection)
- `sidecar_enabled`: write `.json` sidecar alongside each kept file
- `dry_run`: log decisions without touching the filesystem

### Deduplication scope

Duplicates are detected **within the same repository** (canonical name) by SHA-256. Cross-repository files with the same hash are kept independently. RAW+JPG pairs from the same shoot are handled by `classifier.py:check_raw_jpg_conflict`.

### Testing

Tests use `pytest` fixtures from `conftest.py`. The `cfg` fixture sets `stable_check_interval_seconds=0` (instant stability) and `run_once=True`. The `db` fixture provides an in-memory-equivalent SQLite DB under `tmp_path`. Use `conftest.create_fake_image(path, content)` to create test files with specific byte content for hash-based tests.
