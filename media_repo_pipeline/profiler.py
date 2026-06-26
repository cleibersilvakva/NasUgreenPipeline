"""Profiler leve por fase. Custo zero quando desabilitado.

Uso:
    timer = PhaseTimer() if cfg.profile_enabled else NullTimer()
    with timer.phase("hash", bytes_processed=file_size):
        compute_sha256(path)
    ...
    logger.info("\n%s", timer.summary())
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Generator, Protocol


class TimerProtocol(Protocol):
    @contextmanager
    def phase(self, name: str, bytes_processed: int = 0) -> Generator[None, None, None]:
        ...

    def summary(self) -> str:
        ...


class NullTimer:
    """Timer no-op para quando profiling está desabilitado."""

    @contextmanager
    def phase(self, name: str, bytes_processed: int = 0) -> Generator[None, None, None]:
        yield

    def summary(self) -> str:
        return "(profiling desabilitado)"


class PhaseTimer:
    """Acumula timings por fase, thread-safe.

    Cada amostra registra (duração em segundos, bytes processados).
    `bytes_processed` é opcional e permite calcular throughput MB/s
    para fases de I/O (hash, copy, validate).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, list[tuple[float, int]]] = defaultdict(list)

    @contextmanager
    def phase(self, name: str, bytes_processed: int = 0) -> Generator[None, None, None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            with self._lock:
                self._samples[name].append((dt, bytes_processed))

    def summary(self) -> str:
        with self._lock:
            data = {k: list(v) for k, v in self._samples.items()}
        if not data:
            return "(nenhuma amostra de profiling)"

        header = (
            f"{'Fase':<24} {'N':>7} {'total(s)':>10} "
            f"{'média(ms)':>11} {'p50(ms)':>10} {'p95(ms)':>10} {'MB/s':>9}"
        )
        sep = "─" * len(header)
        lines = ["═══ Profiling do ciclo ═══", header, sep]

        # Ordena por tempo total descendente — mostra o vilão no topo
        ordered = sorted(
            data.items(),
            key=lambda kv: sum(d for d, _ in kv[1]),
            reverse=True,
        )
        for name, samples in ordered:
            n = len(samples)
            durations = sorted(d for d, _ in samples)
            total_s = sum(durations)
            mean_ms = (total_s / n) * 1000
            p50_ms = durations[n // 2] * 1000
            p95_idx = min(int(n * 0.95), n - 1)
            p95_ms = durations[p95_idx] * 1000
            total_bytes = sum(b for _, b in samples)
            if total_s > 0 and total_bytes > 0:
                mbps_str = f"{total_bytes / 1024 / 1024 / total_s:.1f}"
            else:
                mbps_str = "—"
            lines.append(
                f"{name:<24} {n:>7} {total_s:>10.2f} "
                f"{mean_ms:>11.2f} {p50_ms:>10.2f} {p95_ms:>10.2f} {mbps_str:>9}"
            )
        return "\n".join(lines)
