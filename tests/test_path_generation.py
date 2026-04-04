"""Testes de geração de caminhos de destino."""

from datetime import datetime
from pathlib import Path

from media_repo_pipeline.config import PipelineConfig
from media_repo_pipeline.constants import MEDIA_KIND_PHOTO, MEDIA_KIND_VIDEO, STATUS_DUPLICATE, STATUS_KEPT, STATUS_REVIEW
from media_repo_pipeline.models import Decision, FileInfo
from media_repo_pipeline.organizer import build_destination


class TestPathGeneration:
    def test_photo_organized_path(self, cfg: PipelineConfig):
        fi = FileInfo(
            source_path="/entrada/cleiber/foto.jpg",
            repository_name_canonical="cleiber",
            extension=".jpg",
            media_kind=MEDIA_KIND_PHOTO,
            capture_dt="2026-03-29 18:45:01",
            hash_sha256="abcdef1234567890",
            mtime_epoch=1743278701.0,
            size_bytes=1000,
        )
        decision = Decision(action=STATUS_KEPT)
        dest = build_destination(fi, decision, cfg)
        assert "/organized/cleiber/photos/2026/03-Março/29/" in str(dest)

    def test_video_organized_path(self, cfg: PipelineConfig):
        fi = FileInfo(
            source_path="/entrada/melise/video.mp4",
            repository_name_canonical="melise",
            extension=".mp4",
            media_kind=MEDIA_KIND_VIDEO,
            capture_dt="2025-12-25 10:30:00",
            hash_sha256="fedcba0987654321",
            mtime_epoch=1735128600.0,
            size_bytes=50000,
        )
        decision = Decision(action=STATUS_KEPT)
        dest = build_destination(fi, decision, cfg)
        assert "/organized/melise/videos/2025/12-Dezembro/25/" in str(dest)

    def test_duplicate_path(self, cfg: PipelineConfig):
        fi = FileInfo(
            source_path="/entrada/cleiber/dup.jpg",
            repository_name_canonical="cleiber",
            extension=".jpg",
            media_kind=MEDIA_KIND_PHOTO,
            capture_dt="2026-01-15 08:00:00",
            hash_sha256="deadbeef",
            mtime_epoch=1736928000.0,
            size_bytes=500,
        )
        decision = Decision(action=STATUS_DUPLICATE)
        dest = build_destination(fi, decision, cfg)
        assert "/duplicates/cleiber/2026/01-Janeiro/15/" in str(dest)

    def test_review_path(self, cfg: PipelineConfig):
        fi = FileInfo(
            source_path="/entrada/vintage/unclear.heic",
            repository_name_canonical="vintage",
            extension=".heic",
            media_kind=MEDIA_KIND_PHOTO,
            capture_dt="2024-06-10 14:00:00",
            hash_sha256="aabb",
            mtime_epoch=1718020800.0,
            size_bytes=200,
        )
        decision = Decision(action=STATUS_REVIEW)
        dest = build_destination(fi, decision, cfg)
        assert "/review/vintage/2024/06-Junho/10/" in str(dest)

    def test_filename_format(self, cfg: PipelineConfig):
        fi = FileInfo(
            source_path="/entrada/cleiber/IMG_1234.heic",
            repository_name_canonical="cleiber",
            extension=".heic",
            media_kind=MEDIA_KIND_PHOTO,
            capture_dt="2026-03-29 18:45:01",
            hash_sha256="a1b2c3d4e5f6",
            mtime_epoch=1743278701.0,
            size_bytes=1000,
        )
        decision = Decision(action=STATUS_KEPT)
        dest = build_destination(fi, decision, cfg)
        name = dest.name
        # Deve ter formato: YYYY-MM-DD_HHMMSS_nomeoriginal_hashcurto.ext
        assert name.startswith("2026-03-29_184501_")
        assert "IMG_1234" in name
        assert name.endswith(".heic")
        assert "a1b2c3" in name
