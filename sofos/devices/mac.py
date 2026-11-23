"""La app Mensajes de esta Mac (iMessage/SMS sincronizados por iCloud).

No necesita cable. macOS protege chat.db: hay que darle «Acceso total al disco»
a la app que ejecuta Sofos (Terminal, iTerm, VS Code…).
"""

import asyncio

from sofos.config import MAC_CHAT_DB
from sofos.devices.base import DeviceAdapter
from sofos.messages import apple_db
from sofos.models import Conversation, Device, Message

DEVICE_ID = "local"


class MacMessagesAdapter(DeviceAdapter):
    kind = "mac"

    async def list_devices(self) -> list[Device]:
        if not MAC_CHAT_DB.exists():
            return []
        device = Device(id=DEVICE_ID, kind="mac", name="Esta Mac", model="Mensajes (iCloud)")
        try:
            with open(MAC_CHAT_DB, "rb") as f:
                f.read(16)
            device.capabilities = ["messages"]
        except PermissionError:
            device.status = (
                "Ajustes del Sistema → Privacidad y seguridad → Acceso total al disco → "
                "activa la app desde la que corres Sofos (Terminal, VS Code…) y reiníciala."
            )
        return [device]

    async def conversations(self, device_id: str) -> list[Conversation]:
        return await asyncio.to_thread(apple_db.list_conversations, MAC_CHAT_DB)

    async def messages(
        self, device_id: str, conversation_id: str, limit: int = 200, before: float | None = None
    ) -> list[Message]:
        return await asyncio.to_thread(
            apple_db.list_messages, MAC_CHAT_DB, conversation_id, limit, before
        )
