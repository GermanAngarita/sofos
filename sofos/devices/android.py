"""Android por USB usando adb (depuración USB).

La primera vez el teléfono pregunta «¿Permitir depuración USB?»; marca
«Permitir siempre desde esta computadora» y no vuelve a preguntar.
"""

import asyncio
import shutil
from pathlib import Path

from sofos.config import PHOTO_EXTENSIONS
from sofos.devices.base import DeviceAdapter, DeviceError
from sofos.messages import android_sms
from sofos.models import Conversation, Device, Message

ADB_CANDIDATES = [
    Path.home() / "Library/Android/sdk/platform-tools/adb",
    Path("/opt/homebrew/bin/adb"),
    Path("/usr/local/bin/adb"),
]
PHOTO_ROOTS = ["/sdcard/DCIM", "/sdcard/Pictures"]
STATE_MESSAGES = {
    "unauthorized": "Acepta «Permitir depuración USB» en el teléfono (marca «Permitir siempre»).",
    "offline": "El teléfono aparece desconectado. Desconecta y vuelve a conectar el cable.",
}


def find_adb() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    return next((str(p) for p in ADB_CANDIDATES if p.exists()), None)


class AndroidAdapter(DeviceAdapter):
    kind = "android"

    def __init__(self) -> None:
        self.adb = find_adb()
        self._sms: dict[str, list[dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _run(self, *args: str, serial: str | None = None, timeout: float = 120) -> str:
        if not self.adb:
            raise DeviceError("No encontré adb. Instálalo con: brew install android-platform-tools")
        cmd = [self.adb] + (["-s", serial] if serial else []) + list(args)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise DeviceError(f"adb tardó demasiado: {' '.join(args)}")
        if proc.returncode != 0:
            raise DeviceError((err or out).decode(errors="replace").strip() or "adb falló")
        return out.decode(errors="replace")

    async def list_devices(self) -> list[Device]:
        if not self.adb:
            return []
        try:
            output = await self._run("devices", "-l", timeout=15)
        except DeviceError:
            return []
        devices = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            props = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            device = Device(
                id=serial,
                kind="android",
                name=props.get("model", "Android").replace("_", " "),
                model=props.get("device", ""),
            )
            if state != "device":
                device.status = STATE_MESSAGES.get(state, state)
            else:
                version = await self._run(
                    "shell", "getprop", "ro.build.version.release", serial=serial, timeout=15
                )
                device.os_version = f"Android {version.strip()}"
                device.capabilities = ["photos", "messages"]
            devices.append(device)
        return devices

    async def sync_photos(self, device_id: str, dest: Path) -> int:
        async with self._locks.setdefault(device_id, asyncio.Lock()):
            # "; true": find falla si alguna carpeta no existe, pero igual lista las demás.
            listing = await self._run(
                "shell", f"find {' '.join(PHOTO_ROOTS)} -type f 2>/dev/null; true", serial=device_id
            )
            copied = 0
            for src in listing.splitlines():
                src = src.strip()
                if "/." in src or Path(src).suffix.lower() not in PHOTO_EXTENSIONS:
                    continue  # ignora .thumbnails, .trashed, etc.
                target = dest / src.removeprefix("/sdcard/")
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".part")
                await self._run("pull", "-a", src, str(tmp), serial=device_id, timeout=600)
                tmp.rename(target)
                copied += 1
            return copied

    async def refresh_messages(self, device_id: str) -> None:
        try:
            output = await self._run(
                "shell", "content", "query", "--uri", "content://sms",
                "--projection", android_sms.PROJECTION, serial=device_id,
            )
        except DeviceError as exc:
            raise DeviceError(
                "Android no permitió leer los SMS por adb en este teléfono. "
                f"Habrá que usar una app compañera (ver README). Detalle: {exc}"
            ) from exc
        self._sms[device_id] = android_sms.parse_rows(output)

    async def _rows(self, device_id: str) -> list[dict]:
        if device_id not in self._sms:
            await self.refresh_messages(device_id)
        return self._sms[device_id]

    async def conversations(self, device_id: str) -> list[Conversation]:
        return android_sms.to_conversations(await self._rows(device_id))

    async def messages(
        self, device_id: str, conversation_id: str, limit: int = 200, before: float | None = None
    ) -> list[Message]:
        return android_sms.to_messages(await self._rows(device_id), conversation_id, limit, before)
