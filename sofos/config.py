import os
from pathlib import Path

# Todo lo que se copia del teléfono queda en esta carpeta (fuera del proyecto).
DATA_DIR = Path(os.environ.get("SOFOS_DATA_DIR", Path.home() / ".sofos")).expanduser()
PHOTOS_DIR = DATA_DIR / "photos"
BACKUPS_DIR = DATA_DIR / "backups"
THUMBS_DIR = DATA_DIR / "thumbs"
# Fotos abiertas desde el teléfono sin copiar: caché temporal que se vacía al iniciar.
VIEW_CACHE_DIR = DATA_DIR / "view-cache"

# Todo lo que guardas desde el teléfono queda dentro del proyecto, en sofos/descargas/:
#   Fotos/AAAA-MM/   fotos y videos, por mes en que se tomaron
#   Capturas/        capturas de la pantalla del iPhone
DOWNLOADS_DIR = Path(
    os.environ.get("SOFOS_DOWNLOADS_DIR", Path(__file__).resolve().parent.parent / "descargas")
).expanduser()
SAVED_PHOTOS_DIR = DOWNLOADS_DIR / "Fotos"
CAPTURES_DIR = DOWNLOADS_DIR / "Capturas"

# Base de mensajes de la app Mensajes de macOS (iMessage sincronizado por iCloud).
MAC_CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".mov", ".mp4"}
VIDEO_EXTENSIONS = {".mov", ".mp4"}

HOST = os.environ.get("SOFOS_HOST", "127.0.0.1")
PORT = int(os.environ.get("SOFOS_PORT", "8765"))
