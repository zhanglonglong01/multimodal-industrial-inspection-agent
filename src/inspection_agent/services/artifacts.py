"""Content-validated image artifact storage for inspection uploads."""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..config import Settings
from ..schemas import ImageFixture
from ..web_schemas import ArtifactRecord

ALLOWED_UPLOADS = {
    ".png": {"image/png": "PNG"},
    ".jpg": {"image/jpeg": "JPEG"},
    ".jpeg": {"image/jpeg": "JPEG"},
    ".webp": {"image/webp": "WEBP"},
}
MAX_IMAGE_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class ArtifactValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ImageArtifactService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def save_upload(
        self,
        *,
        asset_id: str,
        content: bytes,
        original_filename: str,
        media_type: str,
    ) -> ArtifactRecord:
        if not content:
            raise ArtifactValidationError("EMPTY_UPLOAD", "Uploaded image is empty.")
        if len(content) > self.settings.max_upload_bytes:
            raise ArtifactValidationError(
                "UPLOAD_TOO_LARGE",
                f"Image exceeds the {self.settings.max_upload_bytes}-byte limit.",
            )
        extension = Path(original_filename or "").suffix.lower()
        if extension not in ALLOWED_UPLOADS:
            raise ArtifactValidationError(
                "UNSUPPORTED_EXTENSION", "Allowed extensions: .png, .jpg, .jpeg, .webp."
            )
        expected_format = ALLOWED_UPLOADS[extension].get(media_type.lower())
        if expected_format is None:
            raise ArtifactValidationError(
                "UNSUPPORTED_MEDIA_TYPE",
                "The declared MIME type does not match an allowed image extension.",
            )
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.load()
                width, height = image.size
                actual_format = image.format
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
            raise ArtifactValidationError(
                "INVALID_IMAGE", "The uploaded file cannot be decoded as an image."
            ) from exc
        if actual_format != expected_format:
            raise ArtifactValidationError(
                "IMAGE_FORMAT_MISMATCH",
                "Decoded image format does not match the declared file type.",
            )
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise ArtifactValidationError(
                "INVALID_IMAGE_DIMENSIONS", "Image dimensions exceed the demo limit."
            )

        content_hash = hashlib.sha256(content).hexdigest()
        generated_name = f"{content_hash}{extension}"
        destination = self._safe_path(Path("runtime") / "uploads" / generated_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(content)
        return ArtifactRecord(
            artifact_id=f"ARTIFACT-{uuid.uuid4().hex.upper()}",
            asset_id=asset_id,
            media_type=media_type.lower(),
            extension=extension,
            relative_path=destination.relative_to(self.settings.data_dir).as_posix(),
            content_hash=content_hash,
            size_bytes=len(content),
            fixture=False,
            created_at=datetime.now(UTC),
        )

    def fixture_record(self, fixture: ImageFixture) -> ArtifactRecord:
        path = self._safe_path(Path(fixture.path))
        if not path.is_file():
            raise ArtifactValidationError("FIXTURE_NOT_FOUND", "Demo fixture image is missing.")
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if content_hash != fixture.sha256:
            raise ArtifactValidationError(
                "FIXTURE_HASH_MISMATCH", "Demo fixture image hash does not match its manifest."
            )
        return ArtifactRecord(
            artifact_id=f"ARTIFACT-{uuid.uuid4().hex.upper()}",
            asset_id=fixture.asset_id,
            media_type=fixture.media_type,
            extension=path.suffix.lower(),
            relative_path=path.relative_to(self.settings.data_dir).as_posix(),
            content_hash=content_hash,
            size_bytes=path.stat().st_size,
            fixture=True,
            created_at=datetime.now(UTC),
        )

    def resolve(self, relative_path: str) -> Path:
        return self._safe_path(Path(relative_path))

    def _safe_path(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactValidationError("UNSAFE_PATH", "Artifact path is not allowed.")
        root = self.settings.data_dir.resolve()
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise ArtifactValidationError("UNSAFE_PATH", "Artifact escaped the data root.")
        return candidate
