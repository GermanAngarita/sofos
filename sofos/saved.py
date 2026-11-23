"""Fotos ya guardadas en sofos/descargas/Fotos: qué hay, y qué del teléfono ya está guardado.

No usamos una base aparte: una foto del teléfono cuenta como guardada si en la carpeta hay
un archivo con el mismo nombre y el mismo tamaño. Así sigue funcionando aunque muevas
archivos entre carpetas de mes o borres alguno a mano.
"""

import re
from datetime import datetime
from pathlib import Path

from sofos import config
from sofos.config import PHOTO_EXTENSIONS, VIDEO_EXTENSIONS

_DUP_SUFFIX = re.compile(r" \(\d+\)$")  # "IMG_0001 (2).JPG" → "IMG_0001.JPG"


def root() -> Path:
    return config.SAVED_PHOTOS_DIR


def _files() -> list[Path]:
    base = root()
    if not base.exists():
        return []
    return [p for p in base.rglob("*")
            if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS and not p.name.startswith(".")]


def _key(name: str, size: int) -> tuple[str, int]:
    stem, ext = Path(name).stem, Path(name).suffix
    return (_DUP_SUFFIX.sub("", stem) + ext).lower(), size


def saved_keys() -> set[tuple[str, int]]:
    return {_key(p.name, p.stat().st_size) for p in _files()}


def is_saved(item: dict, keys: set[tuple[str, int]]) -> bool:
    return _key(item["name"], item.get("size", 0)) in keys


def list_saved() -> list[dict]:
    base = root()
    items = []
    for p in _files():
        st = p.stat()
        items.append({
            "path": str(p.relative_to(base)), "name": p.name, "size": st.st_size,
            "date": st.st_mtime, "is_video": p.suffix.lower() in VIDEO_EXTENSIONS,
        })
    items.sort(key=lambda i: (i["date"], i["name"]), reverse=True)
    return items


def resolve(rel_path: str) -> Path:
    """Ruta local segura dentro de Fotos/ (evita ../)."""
    base = root().resolve()
    path = (base / rel_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise FileNotFoundError(rel_path)
    return path


def plan_targets(items: list[dict]) -> list[tuple[str, Path]]:
    """Destino de cada foto: Fotos/AAAA-MM/<nombre>, sin pisar archivos existentes."""
    taken: set[Path] = set()
    plan = []
    for item in items:
        month = datetime.fromtimestamp(item["date"]).strftime("%Y-%m") if item.get("date") else "Sin fecha"
        folder = root() / month
        name = Path(item["name"])
        target, n = folder / name.name, 2
        while target in taken or target.exists():
            target, n = folder / f"{name.stem} ({n}){name.suffix}", n + 1
        taken.add(target)
        plan.append((item["path"], target))
    return plan
