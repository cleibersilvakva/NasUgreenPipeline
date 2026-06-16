"""Configuração do pipeline — carrega de YAML, env vars ou CLI."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PipelineConfig:
    """Objeto de configuração tipado do pipeline."""

    input_root: Path = Path("entrada")
    output_root: Path = Path("destino")
    sqlite_db_path: Path | None = None  # default: output_root/db/index.db
    mode: str = "copy"  # copy | move
    scan_interval_seconds: int = 60
    stable_check_interval_seconds: int = 5
    stable_check_count: int = 2
    max_retries_processing: int = 3
    default_timezone: str = "America/Sao_Paulo"
    sidecar_enabled: bool = True
    workers_count: int = 1
    max_filename_length: int = 180
    external_tool_timeout_seconds: int = 30
    low_disk_space_threshold_bytes: int = 10_737_418_240  # 10 GiB
    pipeline_version: str = "v4"
    policy_version: str = "v4-policy-001"
    supported_extensions: frozenset[str] = frozenset()
    ignored_extensions: frozenset[str] = frozenset()
    excluded_relative_paths: list[str] = field(default_factory=list)
    dry_run: bool = False
    run_once: bool = False
    verbose: bool = False
    reconcile_only: bool = False
    profile_enabled: bool = False

    def __post_init__(self) -> None:
        self.input_root = Path(self.input_root)
        self.output_root = Path(self.output_root)
        if self.sqlite_db_path is None:
            self.sqlite_db_path = self.output_root / "db" / "index.db"
        else:
            self.sqlite_db_path = Path(self.sqlite_db_path)
        if self.mode not in ("copy", "move"):
            raise ValueError(f"mode deve ser 'copy' ou 'move', recebido: {self.mode}")

    @property
    def tmp_dir(self) -> Path:
        return self.output_root / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.output_root / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.output_root / "reports"

    @property
    def db_dir(self) -> Path:
        return self.output_root / "db"


def _coerce_path_set(raw: Any) -> frozenset[str]:
    if not raw:
        return frozenset()
    if isinstance(raw, (list, tuple)):
        return frozenset(s.lower() if isinstance(s, str) else s for s in raw)
    return frozenset()


def load_config(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> PipelineConfig:
    """Carrega configuração de YAML, variáveis de ambiente e overrides CLI."""
    data: dict[str, Any] = {}

    # 1. Arquivo YAML
    if config_path and Path(config_path).is_file():
        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    # 2. Variáveis de ambiente (prefixo MRPL_)
    env_map = {
        "MRPL_INPUT_ROOT": "input_root",
        "MRPL_OUTPUT_ROOT": "output_root",
        "MRPL_DB_PATH": "sqlite_db_path",
        "MRPL_MODE": "mode",
        "MRPL_SCAN_INTERVAL": "scan_interval_seconds",
        "MRPL_WORKERS": "workers_count",
        "MRPL_TIMEZONE": "default_timezone",
        "MRPL_VERBOSE": "verbose",
        "MRPL_PROFILE_ENABLED": "profile_enabled",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            data[cfg_key] = val

    # 3. CLI overrides
    if cli_overrides:
        data.update({k: v for k, v in cli_overrides.items() if v is not None})

    # Coerce types
    int_fields = (
        "scan_interval_seconds",
        "stable_check_interval_seconds",
        "stable_check_count",
        "max_retries_processing",
        "workers_count",
        "max_filename_length",
        "external_tool_timeout_seconds",
        "low_disk_space_threshold_bytes",
    )
    for f in int_fields:
        if f in data and not isinstance(data[f], int):
            data[f] = int(data[f])

    bool_fields = ("sidecar_enabled", "dry_run", "run_once", "verbose", "reconcile_only", "profile_enabled")
    for f in bool_fields:
        if f in data and not isinstance(data[f], bool):
            data[f] = str(data[f]).lower() in ("true", "1", "yes")

    data["ignored_extensions"] = _coerce_path_set(data.get("ignored_extensions"))
    data["supported_extensions"] = _coerce_path_set(data.get("supported_extensions"))
    if not isinstance(data.get("excluded_relative_paths"), list):
        data["excluded_relative_paths"] = list(data.get("excluded_relative_paths") or [])

    return PipelineConfig(**{k: v for k, v in data.items() if k in PipelineConfig.__dataclass_fields__})
