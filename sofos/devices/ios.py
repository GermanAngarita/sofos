"""iPhone/iPad por USB usando pymobiledevice3.

Emparejamiento: la primera vez el iPhone muestra "¿Confiar en esta computadora?"
y pide el código de desbloqueo. Después de eso no se pide nada más.
"""

import asyncio
import plistlib
import re
import time
from pathlib import Path

from pymobiledevice3.exceptions import (
    AfcException,
    ConnectionFailedToUsbmuxdError,
    PairingDialogResponsePendingError,
    PairingError,
    PasswordRequiredError,
    PyMobileDevice3Exception,
    UserDeniedPairingError,
)
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.mobilebackup2 import (
    BACKUP_SELECTIONS,
    BackupSelection,
    Mobilebackup2Service,
)
from pymobiledevice3.usbmux import list_devices as usbmux_list

from sofos.config import BACKUPS_DIR, PHOTO_EXTENSIONS, VIDEO_EXTENSIONS, VIEW_CACHE_DIR
from sofos.devices.base import DeviceAdapter, DeviceError
from sofos.messages import apple_db
from sofos.models import Conversation, Device, Message

# sms.db dentro de un respaldo: SHA1("HomeDomain-Library/SMS/sms.db")
SMS_DB_FILE_ID = "3d0d7e5fb2ce288813306e4d4636395e047a3d28"
PAIR_TIMEOUT = 3  # segundos; no bloqueamos la lista esperando que toquen "Confiar"

# Carpetas de fotos en DCIM: 100APPLE, 101APPLE… (hay otras, como ispDump, que no son fotos).
DCIM_FOLDER = re.compile(r"\d{3}[A-Z_]+")
CPL_FOLDER = re.compile(r"group\d+")
# Dónde están las fotos en el iPhone (rutas relativas a la raíz de AFC):
#   DCIM/125APPLE/IMG_5226.JPG             tomadas con este iPhone
#   PhotoData/CPLAssets/group12/<UUID>.JPG  bajadas de iCloud (de otros dispositivos)
# Desde el navegador sólo se aceptan rutas con esta forma: nada más del teléfono es accesible.
DEVICE_PHOTO_PATH = re.compile(
    r"(DCIM/\d{3}[A-Z_]+|PhotoData/CPLAssets/group\d+)/[A-Za-z0-9_\-]+\.[A-Za-z0-9]+"
)
# El iPhone guarda miniaturas propias (~60 KB) aquí, en una carpeta por foto.
THUMBS_ROOT = "/PhotoData/Thumbnails/V2"
TRANSFER_PARALLEL = 4  # pedidos simultáneos; satura el USB sin ahogar al teléfono
LISTING_TTL = 30  # segundos que reutilizamos el listado de fotos


def _pairing_message(exc: Exception) -> str:
    if isinstance(exc, PasswordRequiredError):
        return "Desbloquea el iPhone y vuelve a intentar."
    if isinstance(exc, PairingDialogResponsePendingError):
        return "Toca «Confiar» en el iPhone e ingresa tu código de desbloqueo."
    if isinstance(exc, UserDeniedPairingError):
        return "Se rechazó «Confiar». Desconecta y vuelve a conectar el cable."
    return f"No se pudo emparejar: {exc}"


class IOSAdapter(DeviceAdapter):
    kind = "ios"

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        # Sesión AFC abierta por dispositivo para navegar fotos sin reconectar en cada miniatura.
        self._afc: dict[str, tuple] = {}
        self._afc_locks: dict[str, asyncio.Lock] = {}
        self._listing: dict[str, tuple[float, list[dict]]] = {}

    def _lock(self, udid: str) -> asyncio.Lock:
        return self._locks.setdefault(udid, asyncio.Lock())

    async def _lockdown(self, udid: str):
        try:
            return await create_using_usbmux(
                serial=udid, connection_type="USB", autopair=True, pair_timeout=PAIR_TIMEOUT
            )
        except PairingError as exc:
            raise DeviceError(_pairing_message(exc)) from exc
        except (ConnectionFailedToUsbmuxdError, PyMobileDevice3Exception, OSError) as exc:
            raise DeviceError(f"El iPhone no responde. ¿Está conectado por cable? ({exc})") from exc

    async def list_devices(self) -> list[Device]:
        try:
            mux_devices = await usbmux_list()
        except (ConnectionFailedToUsbmuxdError, OSError):
            return []
        devices = []
        for mux in mux_devices:
            if not mux.is_usb:
                continue
            device = Device(id=mux.serial, kind="ios", name="iPhone")
            try:
                lockdown = await self._lockdown(mux.serial)
            except DeviceError as exc:
                device.status = str(exc)
                devices.append(device)
                continue
            try:
                info = lockdown.short_info
                device.name = info.get("DeviceName") or "iPhone"
                device.model = lockdown.display_name or info.get("ProductType") or ""
                device.os_version = f"iOS {info.get('ProductVersion') or ''}".strip()
                device.capabilities = ["browse", "screen", "messages"]
            finally:
                await lockdown.close()
            devices.append(device)
        return devices

    # ---------- Fotos (se ven directo del teléfono; sólo se copian las que elijas) ----------

    async def _close_afc(self, udid: str) -> None:
        session = self._afc.pop(udid, None)
        if session:
            afc, lockdown = session
            for closer in (afc.aclose, lockdown.close):
                try:
                    await closer()
                except Exception:  # noqa: BLE001 — la conexión ya puede estar muerta
                    pass

    async def _with_afc(self, udid: str, fn):
        """Ejecuta `fn(afc)` sobre una sesión AFC compartida; reconecta una vez si se cayó."""
        async with self._afc_locks.setdefault(udid, asyncio.Lock()):
            for attempt in (1, 2):
                if udid not in self._afc:
                    lockdown = await self._lockdown(udid)
                    try:
                        afc = await AfcService(lockdown).__aenter__()
                    except BaseException:
                        await lockdown.close()
                        raise
                    self._afc[udid] = (afc, lockdown)
                afc = self._afc[udid][0]
                try:
                    return await fn(afc)
                except AfcException as exc:  # el archivo no existe, etc.: no es problema de conexión
                    raise FileNotFoundError(str(exc)) from exc
                except (PyMobileDevice3Exception, OSError, ConnectionError) as exc:
                    await self._close_afc(udid)
                    if attempt == 2:
                        raise DeviceError(f"Se perdió la conexión con el iPhone: {exc}") from exc

    @staticmethod
    def _check_path(path: str) -> str:
        if not DEVICE_PHOTO_PATH.fullmatch(path):
            raise FileNotFoundError(path)
        return path

    @staticmethod
    async def _scan(afc) -> list[dict]:
        folders = [f"DCIM/{d}" for d in await afc.listdir("/DCIM") if DCIM_FOLDER.fullmatch(d)]
        try:
            folders += [
                f"PhotoData/CPLAssets/{g}"
                for g in await afc.listdir("/PhotoData/CPLAssets")
                if CPL_FOLDER.fullmatch(g)
            ]
        except AfcException:
            pass  # sin iCloud Fotos no existe esta carpeta
        candidates = []
        for folder in folders:
            files = [f for f in await afc.listdir(f"/{folder}") if f not in (".", "..")]
            images = {Path(f).stem for f in files if Path(f).suffix.lower() not in VIDEO_EXTENSIONS}
            for name in files:
                ext = Path(name).suffix.lower()
                if ext not in PHOTO_EXTENSIONS:
                    continue
                if ext in VIDEO_EXTENSIONS and Path(name).stem in images:
                    continue  # el video de una Live Photo: lo representa la foto
                candidates.append((f"{folder}/{name}", name, ext in VIDEO_EXTENSIONS))
        # Fecha y tamaño de todas a la vez: AFC atiende pedidos en paralelo (~4000 en <1 s).
        stats = await asyncio.gather(*(afc.stat(f"/{path}") for path, _, _ in candidates))
        items = []
        for (path, name, is_video), st in zip(candidates, stats):
            when = st.get("st_birthtime") or st.get("st_mtime")
            items.append({
                "path": path, "name": name, "is_video": is_video,
                "size": st.get("st_size", 0), "date": when.timestamp() if when else None,
            })
        items.sort(key=lambda p: (p["date"] or 0, p["path"]), reverse=True)
        return items

    async def browse_photos(self, device_id: str) -> list[dict]:
        cached = self._listing.get(device_id)
        if cached and time.time() - cached[0] < LISTING_TTL:
            return cached[1]
        items = await self._with_afc(device_id, self._scan)
        self._listing[device_id] = (time.time(), items)
        return items

    async def photo_thumbnail(self, device_id: str, path: str) -> bytes | None:
        """Miniatura que ya generó el iPhone, o None si no hay."""
        tdir = f"{THUMBS_ROOT}/{self._check_path(path)}"

        async def read(afc):
            try:
                names = sorted(n for n in await afc.listdir(tdir) if n.lower().endswith(".jpg"))
            except AfcException:
                return None
            return await afc.get_file_contents(f"{tdir}/{names[0]}") if names else None

        try:
            return await self._with_afc(device_id, read)
        except FileNotFoundError:
            return None

    async def open_photo(self, device_id: str, path: str) -> Path:
        """Trae sólo esta foto a un caché temporal (se borra al reiniciar Sofos)."""
        local = VIEW_CACHE_DIR / device_id / self._check_path(path)
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            tmp = local.with_name(local.name + ".part")
            await self._with_afc(
                device_id, lambda afc: afc.pull(f"/{path}", str(tmp), progress_bar=False)
            )
            tmp.rename(local)
        return local

    async def transfer_photos(
        self, device_id: str, plan: list[tuple[str, Path]], on_progress
    ) -> int:
        """Copia cada foto (ruta en el teléfono → archivo local), varias a la vez.

        Llama on_progress(bytes) al terminar cada una.
        """
        for path, _ in plan:
            self._check_path(path)

        # Sesión propia: así puedes seguir viendo fotos mientras se copian.
        lockdown = await self._lockdown(device_id)
        sem = asyncio.Semaphore(TRANSFER_PARALLEL)
        try:
            async with AfcService(lockdown) as afc:
                async def copy(path: str, target: Path) -> None:
                    async with sem:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        tmp = target.with_name(target.name + ".part")
                        await afc.pull(f"/{path}", str(tmp), progress_bar=False)
                        tmp.rename(target)
                        on_progress(target.stat().st_size)

                await asyncio.gather(*(copy(p, t) for p, t in plan))
        except AfcException as exc:
            raise DeviceError(f"No se pudo copiar una foto: {exc}") from exc
        except (PyMobileDevice3Exception, OSError) as exc:
            raise DeviceError(f"Se perdió la conexión con el iPhone: {exc}") from exc
        finally:
            await lockdown.close()
        return len(plan)

    # ---------- Mensajes ----------

    def _sms_db(self, udid: str) -> Path:
        return BACKUPS_DIR / udid / SMS_DB_FILE_ID[:2] / SMS_DB_FILE_ID

    async def refresh_messages(self, device_id: str) -> None:
        """Respaldo incremental que sólo guarda sms.db (rápido y liviano)."""
        async with self._lock(device_id):
            lockdown = await self._lockdown(device_id)
            try:
                if await lockdown.get_value("com.apple.mobile.backup", "WillEncrypt"):
                    raise DeviceError(
                        "Los respaldos de este iPhone están cifrados, así que sms.db no se puede leer "
                        "sin la contraseña de respaldo. Desactiva «Cifrar respaldo local» en Finder "
                        "o usa la fuente «Esta Mac» para iMessage."
                    )
                BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
                only_sms = Mobilebackup2Service.selection_filter_callback(
                    BACKUP_SELECTIONS[BackupSelection.SMS]
                )
                async with Mobilebackup2Service(lockdown) as backup:
                    await backup.backup(
                        full=False, backup_directory=BACKUPS_DIR, filter_callback=only_sms
                    )
            except PyMobileDevice3Exception as exc:
                raise DeviceError(f"Falló el respaldo de mensajes: {exc}") from exc
            finally:
                await lockdown.close()

    def _ensure_db(self, device_id: str) -> Path:
        db = self._sms_db(device_id)
        manifest = BACKUPS_DIR / device_id / "Manifest.plist"
        if manifest.exists() and plistlib.loads(manifest.read_bytes()).get("IsEncrypted"):
            raise DeviceError("El respaldo está cifrado; no se pueden leer los mensajes.")
        if not db.exists():
            raise DeviceError("Aún no hay mensajes copiados. Toca «Actualizar mensajes».")
        return db

    async def conversations(self, device_id: str) -> list[Conversation]:
        return await asyncio.to_thread(apple_db.list_conversations, self._ensure_db(device_id))

    async def messages(
        self, device_id: str, conversation_id: str, limit: int = 200, before: float | None = None
    ) -> list[Message]:
        return await asyncio.to_thread(
            apple_db.list_messages, self._ensure_db(device_id), conversation_id, limit, before
        )
