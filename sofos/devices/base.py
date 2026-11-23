from abc import ABC, abstractmethod
from pathlib import Path

from sofos.models import Conversation, Device, Message


class DeviceError(Exception):
    """Error con un mensaje para mostrarle al usuario tal cual."""


class DeviceAdapter(ABC):
    """Una fuente de fotos/mensajes: iPhone, Android o la propia Mac."""

    kind: str

    @abstractmethod
    async def list_devices(self) -> list[Device]: ...

    async def sync_photos(self, device_id: str, dest: Path) -> int:
        """Copia a `dest` las fotos que aún no estén. Devuelve cuántas copió."""
        raise DeviceError("Esta fuente no tiene fotos.")

    async def browse_photos(self, device_id: str) -> list[dict]:
        """Lista las fotos que están en el teléfono, sin copiarlas."""
        raise DeviceError("Esta fuente no permite ver fotos sin copiarlas.")

    async def photo_thumbnail(self, device_id: str, path: str) -> bytes | None:
        """Miniatura JPEG desde el teléfono, o None si no hay una lista."""
        return None

    async def open_photo(self, device_id: str, path: str) -> Path:
        """Trae una sola foto del teléfono a un caché temporal y devuelve su ruta local."""
        raise DeviceError("Esta fuente no permite ver fotos sin copiarlas.")

    async def transfer_photos(self, device_id: str, plan: list[tuple[str, Path]], on_progress) -> int:
        """Copia cada foto (ruta en el teléfono → archivo local). Llama on_progress(bytes) por cada una."""
        raise DeviceError("Esta fuente no permite elegir fotos para copiar.")

    async def refresh_messages(self, device_id: str) -> None:
        """Trae una copia fresca de los mensajes del dispositivo (si aplica)."""

    async def conversations(self, device_id: str) -> list[Conversation]:
        raise DeviceError("Esta fuente no tiene mensajes.")

    async def messages(
        self, device_id: str, conversation_id: str, limit: int = 200, before: float | None = None
    ) -> list[Message]:
        raise DeviceError("Esta fuente no tiene mensajes.")
