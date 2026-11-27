"""iPhone/iPad por USB o Wi-Fi usando pymobiledevice3.

Emparejamiento: la primera vez (por cable) el iPhone muestra "¿Confiar en esta
computadora?" y pide el código de desbloqueo. Después de eso no se pide nada más.

Wi-Fi: con el iPhone conectado por cable, `set_wifi(True)` activa lo mismo que la casilla
«Mostrar este iPhone cuando esté en Wi-Fi» de Finder. Desde entonces, en la misma red,
el iPhone aparece también por Wi-Fi (fotos y mensajes; la pantalla sólo va por cable).
Si está por las dos vías, se usa el cable.
"""

import asyncio
import plistlib
import posixpath
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
from pymobiledevice3.services.house_arrest import HouseArrestService
from pymobiledevice3.services.installation_proxy import InstallationProxyService
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
        # Sesiones AFC abiertas por (dispositivo, raíz) para no reconectar en cada pedido.
        self._afc: dict[tuple[str, str], tuple] = {}
        self._afc_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._apps: dict[str, tuple[float, list[dict]]] = {}
        self._listing: dict[str, tuple[float, list[dict]]] = {}

    def _lock(self, udid: str) -> asyncio.Lock:
        return self._locks.setdefault(udid, asyncio.Lock())

    @staticmethod
    async def _connection_type(udid: str) -> str | None:
        """"USB" si está por cable, "Network" si sólo por Wi-Fi, None si no se ve."""
        try:
            found = {m.connection_type for m in await usbmux_list() if m.matches_udid(udid)}
        except (ConnectionFailedToUsbmuxdError, OSError):
            return None
        return "USB" if "USB" in found else ("Network" if "Network" in found else None)

    async def _lockdown(self, udid: str, connection_type: str | None = None):
        connection_type = connection_type or await self._connection_type(udid)
        if connection_type is None:
            raise DeviceError("No encuentro el iPhone. Conéctalo por cable o ponlo en la misma red Wi-Fi.")
        try:
            return await create_using_usbmux(
                serial=udid, connection_type=connection_type,
                # Emparejar («Confiar») sólo se puede por cable.
                autopair=connection_type == "USB", pair_timeout=PAIR_TIMEOUT,
            )
        except PairingError as exc:
            raise DeviceError(_pairing_message(exc)) from exc
        except (ConnectionFailedToUsbmuxdError, PyMobileDevice3Exception, OSError) as exc:
            how = "por cable" if connection_type == "USB" else "por Wi-Fi"
            raise DeviceError(f"El iPhone no responde {how}. ({exc})") from exc

    async def list_devices(self) -> list[Device]:
        try:
            mux_devices = await usbmux_list()
        except (ConnectionFailedToUsbmuxdError, OSError):
            return []
        # Un iPhone por cable y por Wi-Fi aparece dos veces: lo agrupamos y preferimos el cable.
        by_udid: dict[str, set[str]] = {}
        for mux in mux_devices:
            by_udid.setdefault(mux.serial, set()).add(mux.connection_type)
        devices = []
        for udid, types in by_udid.items():
            connection_type = "USB" if "USB" in types else "Network"
            device = Device(id=udid, kind="ios", name="iPhone",
                            connection="usb" if connection_type == "USB" else "wifi")
            try:
                lockdown = await self._lockdown(udid, connection_type)
            except DeviceError as exc:
                device.status = str(exc)
                devices.append(device)
                continue
            try:
                info = lockdown.short_info
                device.name = info.get("DeviceName") or "iPhone"
                device.model = lockdown.display_name or info.get("ProductType") or ""
                device.os_version = f"iOS {info.get('ProductVersion') or ''}".strip()
                device.wifi_enabled = (
                    True if connection_type == "Network" else await lockdown.get_enable_wifi_connections())
                # La pantalla sólo se puede ver por cable (macOS no la recibe por Wi-Fi).
                device.capabilities = ["browse", "files", "messages"] + (
                    ["screen"] if connection_type == "USB" else [])
            except PyMobileDevice3Exception:
                device.capabilities = device.capabilities or ["browse", "files", "messages"]
            finally:
                await lockdown.close()
            devices.append(device)
        return devices

    async def set_wifi(self, device_id: str, on: bool) -> None:
        if await self._connection_type(device_id) != "USB":
            raise DeviceError("Para cambiar esto conecta el iPhone por cable.")
        lockdown = await self._lockdown(device_id, "USB")
        try:
            await lockdown.set_enable_wifi_connections(on)
        except PyMobileDevice3Exception as exc:
            raise DeviceError(f"El iPhone no aceptó el cambio: {exc}") from exc
        finally:
            await lockdown.close()

    # ---------- Fotos (se ven directo del teléfono; sólo se copian las que elijas) ----------

    async def _close_afc(self, udid: str, root: str = "media") -> None:
        session = self._afc.pop((udid, root), None)
        if session:
            afc, lockdown = session
            for closer in (afc.aclose, lockdown.close):
                try:
                    await closer()
                except Exception:  # noqa: BLE001 — la conexión ya puede estar muerta
                    pass

    async def _open_fs(self, udid: str, root: str):
        """Sesión AFC: "media" = zona de medios; "app:<bundle id>" = Documents de esa app."""
        lockdown = await self._lockdown(udid)
        try:
            if root == "media":
                svc = AfcService(lockdown)
            elif root.startswith("app:"):
                # Sólo apps que comparten archivos (UIFileSharingEnabled); el resto lo rechaza iOS.
                svc = await HouseArrestService.create(lockdown, root[4:], documents_only=True)
            else:
                raise FileNotFoundError(root)
            return await svc.__aenter__(), lockdown
        except BaseException:
            await lockdown.close()
            raise

    async def _with_fs(self, udid: str, root: str, fn):
        """Ejecuta `fn(afc)` sobre una sesión compartida de esa raíz; reconecta una vez si se cayó."""
        async with self._afc_locks.setdefault((udid, root), asyncio.Lock()):
            for attempt in (1, 2):
                if (udid, root) not in self._afc:
                    try:
                        self._afc[(udid, root)] = await self._open_fs(udid, root)
                    except PyMobileDevice3Exception as exc:
                        raise DeviceError(f"El iPhone no dejó abrir esa carpeta: {exc}") from exc
                afc = self._afc[(udid, root)][0]
                try:
                    return await fn(afc)
                except AfcException as exc:  # el archivo no existe, etc.: no es problema de conexión
                    raise FileNotFoundError(str(exc)) from exc
                except (PyMobileDevice3Exception, OSError, ConnectionError) as exc:
                    await self._close_afc(udid, root)
                    if attempt == 2:
                        raise DeviceError(f"Se perdió la conexión con el iPhone: {exc}") from exc

    async def _with_afc(self, udid: str, fn):
        return await self._with_fs(udid, "media", fn)

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


    # ---------- Explorador de archivos (sólo lectura) ----------

    async def fs_roots(self, device_id: str) -> list[dict]:
        """Lugares que se pueden explorar: la zona de medios y las apps que comparten archivos."""
        cached = self._apps.get(device_id)
        if not cached or time.time() - cached[0] > 120:
            lockdown = await self._lockdown(device_id)
            try:
                async with InstallationProxyService(lockdown) as ip:
                    apps = await ip.get_apps(application_type="User")
            except PyMobileDevice3Exception as exc:
                raise DeviceError(f"No pude listar las apps: {exc}") from exc
            finally:
                await lockdown.close()
            sharing = [
                {"id": f"app:{bid}", "name": a.get("CFBundleDisplayName") or a.get("CFBundleName") or bid,
                 "kind": "app", "bundle_id": bid}
                for bid, a in apps.items() if a.get("UIFileSharingEnabled")
            ]
            sharing.sort(key=lambda r: r["name"].lower())
            self._apps[device_id] = (time.time(), sharing)
            cached = self._apps[device_id]
        return [{"id": "media", "name": "Medios del iPhone", "kind": "media"}] + cached[1]

    @staticmethod
    def _fs_path(root: str, path: str) -> str:
        """Ruta absoluta normalizada dentro de la raíz (sin ../)."""
        base = "/Documents" if root.startswith("app:") else "/"
        if path in ("", "/"):  # «inicio» de esa raíz
            path = base
        norm = posixpath.normpath(path if path.startswith("/") else posixpath.join(base, path))
        if not (norm == base or norm.startswith(base.rstrip("/") + "/")):
            raise FileNotFoundError(path)
        return norm

    async def fs_list(self, device_id: str, root: str, path: str) -> dict:
        target = self._fs_path(root, path)

        async def listing(afc):
            names = [n for n in await afc.listdir(target) if n not in (".", "..")]

            async def info(name):
                full = posixpath.join(target, name)
                try:
                    st = await afc.stat(full)
                except AfcException:
                    return None
                when = st.get("st_mtime")
                return {"name": name, "path": full, "is_dir": st.get("st_ifmt") == "S_IFDIR",
                        "size": st.get("st_size", 0), "date": when.timestamp() if when else None}

            entries = [e for e in await asyncio.gather(*(info(n) for n in names)) if e]
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
            return entries

        return {"root": root, "path": target, "entries": await self._with_fs(device_id, root, listing)}

    async def fs_open(self, device_id: str, root: str, path: str, with_sidecars: bool = False) -> Path:
        """Trae ese archivo a un caché temporal y devuelve la ruta local.

        `with_sidecars`: para SQLite, trae también -wal y -shm (si no, faltan los datos recientes).
        """
        target = self._fs_path(root, path)
        safe_root = root.replace(":", "_").replace("/", "_")
        local = VIEW_CACHE_DIR / device_id / "fs" / safe_root / target.lstrip("/")

        async def pull(afc):
            stat = await afc.stat(target)
            if stat.get("st_ifmt") == "S_IFDIR":
                raise FileNotFoundError(path)
            names = [target] + ([target + "-wal", target + "-shm"] if with_sidecars else [])
            for remote in names:
                dest = local.with_name(posixpath.basename(remote))
                try:
                    st = stat if remote == target else await afc.stat(remote)
                except AfcException:
                    dest.unlink(missing_ok=True)  # no existe ese -wal/-shm
                    continue
                if dest.exists() and dest.stat().st_size == st.get("st_size"):
                    continue  # ya está en caché
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                await afc.pull(remote, str(tmp), progress_bar=False)
                tmp.rename(dest)

        await self._with_fs(device_id, root, pull)
        return local

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
