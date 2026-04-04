"""Lock de instância única via lockfile + PID."""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

from .errors import LockAcquisitionError

logger = logging.getLogger("media_repo_pipeline.lock")


class LockService:
    """Impede múltiplas instâncias simultâneas do pipeline."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path

    def acquire(self) -> None:
        """Adquire o lock. Lança LockAcquisitionError se já existir instância ativa."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_path.exists():
            existing_pid = self._read_pid()
            if existing_pid is not None and self._is_pid_alive(existing_pid):
                raise LockAcquisitionError(
                    f"Outra instância ativa (PID {existing_pid}). Lockfile: {self._lock_path}"
                )
            logger.warning("Lockfile órfão encontrado (PID %s morto). Removendo.", existing_pid)
            self._lock_path.unlink(missing_ok=True)

        self._lock_path.write_text(str(os.getpid()), encoding="utf-8")
        logger.info("Lock adquirido (PID %d).", os.getpid())

    def release(self) -> None:
        """Libera o lock."""
        if self._lock_path.exists():
            stored_pid = self._read_pid()
            if stored_pid == os.getpid():
                self._lock_path.unlink(missing_ok=True)
                logger.info("Lock liberado.")
            else:
                logger.warning(
                    "Lock pertence a PID %s, não ao atual %d. Não removido.",
                    stored_pid, os.getpid(),
                )

    def _read_pid(self) -> int | None:
        try:
            return int(self._lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Verifica se o PID está vivo E se é realmente o pipeline.

        Em ambiente Docker, o PID 1 sempre existe (é o entrypoint do container),
        então validamos também via /proc que o cmdline pertence ao pipeline.
        Se /proc não estiver disponível (não-Linux), cai para o kill() simples.
        """
        try:
            os.kill(pid, 0)  # lança se PID não existe
        except ProcessLookupError:
            return False
        except PermissionError:
            pass  # processo existe mas não temos permissão de sinalizar

        # Validação extra via /proc (Linux/containers)
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            try:
                cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode(errors="replace")
                return "media-repo-pipeline" in cmdline or "media_repo_pipeline" in cmdline
            except OSError:
                pass

        return True  # não conseguiu verificar via /proc, confia no kill()

