"""Vista previa de archivos del teléfono para el explorador (todo en sólo lectura)."""

import json
import plistlib
import sqlite3
from datetime import datetime
from pathlib import Path

IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".tif", ".tiff", ".bmp"}
VIDEO = {".mov", ".mp4", ".m4v"}
AUDIO = {".mp3", ".m4a", ".wav", ".aac", ".caf", ".opus", ".ogg", ".amr", ".aif", ".aiff", ".flac"}
SQLITE_EXT = {".sqlite", ".sqlitedb", ".db", ".sqlite3", ".cloudphotodb", ".storedata"}
TEXT_EXT = {".txt", ".json", ".xml", ".log", ".csv", ".html", ".xhtml", ".htm", ".m3u8", ".md", ".js", ".css"}

MAX_TEXT = 200_000  # caracteres que mostramos de un archivo de texto
PREVIEW_LIMIT = 300 * 1024 * 1024  # más grande que esto: sólo descargar


def kind_for(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE: return "image"
    if ext in VIDEO: return "video"
    if ext in AUDIO: return "audio"
    if ext == ".pdf": return "pdf"
    if ext == ".plist": return "plist"
    if ext in SQLITE_EXT: return "sqlite"
    if ext in TEXT_EXT: return "text"
    return "unknown"


def _is_sqlite(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(16) == b"SQLite format 3\x00"


def _json_default(value):
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _text_or_binary(path: Path) -> dict:
    raw = path.read_bytes()[: MAX_TEXT * 2]
    try:
        text = raw.decode("utf-8")
        printable = sum(c.isprintable() or c in "\n\r\t" for c in text[:5000])
        if printable >= 0.95 * min(len(text), 5000):
            return {"kind": "text", "text": text[:MAX_TEXT], "truncated": path.stat().st_size > len(raw)}
    except UnicodeDecodeError:
        pass
    head = raw[:1024]
    lines = [f"{i:08x}  {head[i:i+16].hex(' '):<47}  "
             + "".join(chr(b) if 32 <= b < 127 else "." for b in head[i:i+16])
             for i in range(0, len(head), 16)]
    return {"kind": "binary", "text": "\n".join(lines)}


def preview(path: Path) -> dict:
    """Describe el archivo local para mostrarlo en la interfaz."""
    kind = kind_for(path.name)
    if kind in ("unknown", "sqlite") and _is_sqlite(path):
        kind = "sqlite"
    if kind == "sqlite":
        return {"kind": "sqlite", "tables": sqlite_tables(path)}
    if kind == "plist":
        try:
            data = plistlib.loads(path.read_bytes())
            text = json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
            return {"kind": "text", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}
        except Exception:  # noqa: BLE001 — plist inválido: lo mostramos tal cual
            return _text_or_binary(path)
    if kind in ("image", "video", "audio", "pdf"):
        return {"kind": kind}
    return _text_or_binary(path)


def _connect(path: Path) -> sqlite3.Connection:
    # Es una copia en caché: abrirla en sólo lectura nunca toca el teléfono.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def sqlite_tables(path: Path) -> list[dict]:
    with _connect(path) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        tables = []
        for name in names[:200]:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except sqlite3.DatabaseError:
                count = None
            tables.append({"name": name, "columns": cols, "rows": count})
    return tables


def _cell(value):
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value


def sqlite_rows(path: Path, table: str, limit: int = 100, offset: int = 0) -> dict:
    with _connect(path) as conn:
        valid = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in valid:  # el nombre viene del navegador: sólo tablas que existen
            raise KeyError(table)
        cur = conn.execute(f'SELECT * FROM "{table}" LIMIT ? OFFSET ?', (limit, offset))
        cols = [d[0] for d in cur.description]
        return {"columns": cols, "rows": [[_cell(v) for v in row] for row in cur.fetchall()]}
