"""Constantes globais do pipeline."""

# ── Extensões suportadas ──────────────────────────────────────────────

PHOTO_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".tif",
    ".bmp", ".webp", ".gif", ".avif",
    # RAW
    ".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf",
    ".rw2", ".srw", ".pef", ".raw",
})

VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts",
    ".3gp", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
})

RAW_EXTENSIONS: frozenset[str] = frozenset({
    ".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf",
    ".rw2", ".srw", ".pef", ".raw",
})

JPG_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg"})

SUPPORTED_EXTENSIONS: frozenset[str] = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

DEFAULT_IGNORED_EXTENSIONS: frozenset[str] = frozenset({
    ".xmp", ".aae", ".thm", ".db", ".ini", ".json", ".txt", ".xml",
})

# ── Tipos de mídia ────────────────────────────────────────────────────

MEDIA_KIND_PHOTO = "photo"
MEDIA_KIND_VIDEO = "video"
MEDIA_KIND_OTHER = "other"

# ── Status de arquivo ─────────────────────────────────────────────────

STATUS_KEPT = "kept"
STATUS_DUPLICATE = "duplicate"
STATUS_REVIEW = "review"
STATUS_CORRUPTED = "corrupted"
STATUS_SKIPPED = "skipped"
STATUS_ERROR = "error"
STATUS_PENDING = "pending"

# ── Status de repositório ─────────────────────────────────────────────

REPO_STATUS_ACTIVE = "active"
REPO_STATUS_INACTIVE = "inactive_source_missing"

# ── Diretórios padrão do destino ──────────────────────────────────────

DIR_ORGANIZED = "organized"
DIR_DUPLICATES = "duplicates"
DIR_REVIEW = "review"
DIR_CORRUPTED = "corrupted"
DIR_SIDECARS = "sidecars"
DIR_REPORTS = "reports"
DIR_LOGS = "logs"
DIR_DB = "db"
DIR_TMP = "tmp"

DESTINATION_DIRS = (
    DIR_ORGANIZED,
    DIR_DUPLICATES,
    DIR_REVIEW,
    DIR_CORRUPTED,
    DIR_SIDECARS,
    DIR_REPORTS,
    DIR_LOGS,
    DIR_DB,
    DIR_TMP,
)

# ── Sub-diretórios dentro de organized ────────────────────────────────

SUBDIR_PHOTOS = "photos"
SUBDIR_VIDEOS = "videos"

# ── Confiança da data ─────────────────────────────────────────────────

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

# ── Hash ──────────────────────────────────────────────────────────────

HASH_ALGORITHM = "sha256"
HASH_SHORT_LENGTH = 6

# ── Formatos de data ──────────────────────────────────────────────────

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
FILENAME_DATETIME_FORMAT = "%Y-%m-%d_%H%M%S"

# ── Prioridade de campos EXIF para data ───────────────────────────────

EXIF_DATE_FIELDS_PRIORITY = (
    "DateTimeOriginal",
    "CreateDate",
    "MediaCreateDate",
    "TrackCreateDate",
    "CreationDate",
    "FileModifyDate",
)
