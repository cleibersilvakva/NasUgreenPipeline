"""Exceções específicas do pipeline."""


class PipelineError(Exception):
    """Erro base do pipeline."""


class HealthCheckError(PipelineError):
    """Falha no healthcheck de inicialização."""


class LockAcquisitionError(PipelineError):
    """Não foi possível adquirir o lock de instância."""


class FileStabilityError(PipelineError):
    """O arquivo ainda não está estável para processamento."""


class MetadataExtractionError(PipelineError):
    """Falha ao extrair metadata de um arquivo."""


class HashComputationError(PipelineError):
    """Falha ao calcular hash do arquivo."""


class TransactionError(PipelineError):
    """Falha durante a transação lógica de processamento."""


class CopyError(TransactionError):
    """Falha durante a cópia do arquivo."""


class ValidationError(TransactionError):
    """Falha na validação pós-cópia."""


class SidecarError(TransactionError):
    """Falha ao gerar ou escrever sidecar."""


class DatabaseError(PipelineError):
    """Falha ao operar no banco SQLite."""


class ReconciliationError(PipelineError):
    """Falha durante reconciliação."""
