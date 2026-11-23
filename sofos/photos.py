"""Fotos copiadas localmente: listado, miniaturas y conversión de HEIC."""

import hashlib
import io
import shutil
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from sofos.config import PHOTO_EXTENSIONS, PHOTOS_DIR, THUMBS_DIR, VIDEO_EXTENSIONS, VIEW_CACHE_DIR

register_heif_opener()

BROWSER_NATIVE = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov"}


def device_dir(device_key: str) -> Path:
    return PHOTOS_DIR / device_key.replace(":", "_").replace("/", "_")


def list_photos(device_key: str, offset: int = 0, limit: int = 200) -> tuple[int, list[dict]]:
    root = device_dir(device_key)
    if not root.exists():
        return 0, []
    files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    page = files[offset : offset + limit]
    return len(files), [
        {
            "path": str(p.relative_to(root)),
            "name": p.name,
            "date": p.stat().st_mtime,
            "is_video": p.suffix.lower() in VIDEO_EXTENSIONS,
        }
        for p in page
    ]


def resolve(device_key: str, rel_path: str) -> Path:
    """Ruta local segura (evita ../ fuera de la carpeta del dispositivo)."""
    root = device_dir(device_key).resolve()
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise FileNotFoundError(rel_path)
    return path


def _render_jpeg(src: Path, max_size: int, cache_key: str) -> Path:
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{src}:{src.stat().st_mtime}:{cache_key}".encode()).hexdigest()
    out = THUMBS_DIR / f"{digest}.jpg"
    if not out.exists():
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=85)
        out.write_bytes(buf.getvalue())
    return out


def thumbnail(path: Path, size: int = 320) -> Path:
    return _render_jpeg(path, size, f"thumb{size}")


def viewable(path: Path) -> Path:
    """El archivo original si el navegador lo entiende; si es HEIC, un JPEG grande."""
    if path.suffix.lower() in BROWSER_NATIVE:
        return path
    return _render_jpeg(path, 2560, "full")


def clear_view_cache() -> None:
    """Borra las fotos que se abrieron desde el teléfono en la sesión anterior."""
    shutil.rmtree(VIEW_CACHE_DIR, ignore_errors=True)
