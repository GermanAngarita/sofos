from dataclasses import asdict
from pathlib import Path

import asyncio
import subprocess
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from sofos import explore, photos, saved
from sofos.config import CAPTURES_DIR

CAPTURE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
from sofos.devices.base import DeviceError
from sofos.manager import DeviceManager
from sofos.screen import ScreenManager

WEB_DIR = Path(__file__).parent / "web"


def create_app(manager: DeviceManager | None = None) -> FastAPI:
    mgr = manager or DeviceManager()
    screens = ScreenManager()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await screens.stop_all()  # no dejar procesos del visor vivos al salir

    app = FastAPI(title="Sofos", lifespan=lifespan)
    app.state.manager = mgr

    @app.exception_handler(DeviceError)
    async def device_error(_request, exc: DeviceError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/devices")
    async def devices():
        return await mgr.list_devices()

    @app.post("/api/devices/{key}/wifi")
    async def set_wifi(key: str, on: bool = Body(..., embed=True)):
        adapter, device_id = mgr.resolve(key)
        await adapter.set_wifi(device_id, on)
        return {"wifi_enabled": on}

    @app.get("/api/devices/{key}/job")
    async def job(key: str):
        return mgr.job_dict(key)

    # ---------- Fotos ----------

    @app.post("/api/devices/{key}/photos/sync")
    async def sync_photos(key: str):
        return mgr.start_job(key, "photos")

    @app.get("/api/devices/{key}/photos")
    async def list_photos(key: str, offset: int = 0, limit: int = Query(200, le=20000)):
        total, items = photos.list_photos(key, offset, limit)
        return {"total": total, "items": items}

    def _photo(key: str, path: str) -> Path:
        try:
            return photos.resolve(key, path)
        except FileNotFoundError:
            raise HTTPException(404, "Foto no encontrada")

    @app.get("/api/devices/{key}/photos/thumb")
    async def thumb(key: str, path: str):
        return FileResponse(photos.thumbnail(_photo(key, path)), media_type="image/jpeg")

    @app.get("/api/devices/{key}/photos/file")
    async def photo_file(key: str, path: str):
        return FileResponse(photos.viewable(_photo(key, path)))

    # ---------- Fotos en el teléfono (sin copiar) ----------

    @app.get("/api/devices/{key}/phone-photos")
    async def phone_photos(key: str, offset: int = 0, limit: int = Query(200, le=20000)):
        adapter, device_id = mgr.resolve(key)
        items = await adapter.browse_photos(device_id)
        keys = await asyncio.to_thread(saved.saved_keys)
        page = [{**i, "saved": saved.is_saved(i, keys)} for i in items[offset : offset + limit]]
        return {"total": len(items), "saved": sum(p["saved"] for p in page), "items": page}

    @app.get("/api/devices/{key}/phone-photos/thumb")
    async def phone_thumb(key: str, path: str):
        adapter, device_id = mgr.resolve(key)
        try:
            data = await adapter.photo_thumbnail(device_id, path)
            if data is None:  # el iPhone no tenía miniatura: la generamos desde el original
                local = await adapter.open_photo(device_id, path)
                data = photos.thumbnail(local).read_bytes()
        except (FileNotFoundError, OSError):
            raise HTTPException(404, "Sin miniatura")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/devices/{key}/phone-photos/file")
    async def phone_file(key: str, path: str, download: bool = False):
        adapter, device_id = mgr.resolve(key)
        try:
            local = await adapter.open_photo(device_id, path)
        except FileNotFoundError:
            raise HTTPException(404, "Foto no encontrada")
        if download:  # el original tal cual, para guardarlo
            return FileResponse(local, filename=local.name)
        return FileResponse(photos.viewable(local))

    @app.post("/api/devices/{key}/phone-photos/transfer")
    async def transfer(key: str, paths: list[str] = Body(..., embed=True)):
        if not paths:
            raise HTTPException(400, "No elegiste fotos.")
        return mgr.start_job(key, "transfer", paths)

    # ---------- Fotos guardadas (sofos/descargas/Fotos) ----------

    def _saved(path: str) -> Path:
        try:
            return saved.resolve(path)
        except FileNotFoundError:
            raise HTTPException(404, "Foto no encontrada")

    @app.get("/api/saved")
    async def list_saved():
        items = await asyncio.to_thread(saved.list_saved)
        return {"total": len(items), "items": items}

    @app.get("/api/saved/thumb")
    async def saved_thumb(path: str):
        try:
            thumb = await asyncio.to_thread(photos.thumbnail, _saved(path))
        except OSError:  # p. ej. videos: no hay miniatura
            raise HTTPException(404, "Sin miniatura")
        return FileResponse(thumb, media_type="image/jpeg")

    @app.get("/api/saved/file")
    async def saved_file(path: str, download: bool = False):
        local = _saved(path)
        if download:
            return FileResponse(local, filename=local.name)
        return FileResponse(await asyncio.to_thread(photos.viewable, local))

    @app.post("/api/saved/open")
    async def open_saved():
        saved.root().mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(saved.root())], check=False)
        return {"ok": True}

    # ---------- Explorador de archivos del teléfono (sólo lectura) ----------

    @app.get("/api/devices/{key}/fs/roots")
    async def fs_roots(key: str):
        adapter, device_id = mgr.resolve(key)
        return await adapter.fs_roots(device_id)

    @app.get("/api/devices/{key}/fs/list")
    async def fs_list(key: str, root: str = "media", path: str = "/"):
        adapter, device_id = mgr.resolve(key)
        try:
            return await adapter.fs_list(device_id, root, path)
        except FileNotFoundError:
            raise HTTPException(404, "Esa carpeta no existe o no se puede abrir.")

    async def _fs_local(key: str, root: str, path: str, sidecars: bool = False) -> Path:
        adapter, device_id = mgr.resolve(key)
        try:
            return await adapter.fs_open(device_id, root, path, with_sidecars=sidecars)
        except FileNotFoundError:
            raise HTTPException(404, "Archivo no encontrado.")

    @app.get("/api/devices/{key}/fs/preview")
    async def fs_preview(key: str, path: str, root: str = "media", size: int = 0):
        if size > explore.PREVIEW_LIMIT:
            return {"kind": "too_big"}
        is_db = explore.kind_for(path) == "sqlite"
        local = await _fs_local(key, root, path, sidecars=is_db)
        try:
            return await asyncio.to_thread(explore.preview, local)
        except Exception as exc:  # noqa: BLE001 — archivo raro o dañado: lo decimos y ya
            return {"kind": "error", "text": f"No pude leer este archivo: {exc}"}

    @app.get("/api/devices/{key}/fs/sqlite")
    async def fs_sqlite(key: str, path: str, table: str, root: str = "media",
                        limit: int = Query(100, le=1000), offset: int = 0):
        local = await _fs_local(key, root, path, sidecars=True)
        try:
            return await asyncio.to_thread(explore.sqlite_rows, local, table, limit, offset)
        except KeyError:
            raise HTTPException(404, "Esa tabla no existe.")

    @app.get("/api/devices/{key}/fs/file")
    async def fs_file(key: str, path: str, root: str = "media", download: bool = False):
        local = await _fs_local(key, root, path)
        if download:
            return FileResponse(local, filename=local.name)
        if explore.kind_for(local.name) == "image":
            return FileResponse(await asyncio.to_thread(photos.viewable, local))  # HEIC → JPEG
        return FileResponse(local)

    # ---------- Pantalla en vivo (sólo iPhone) ----------

    def _screen(key: str):
        adapter, device_id = mgr.resolve(key)
        if adapter.kind != "ios":
            raise DeviceError("Ver la pantalla sólo está disponible para iPhone.")
        return screens.get(device_id)

    @app.get("/api/devices/{key}/screen.mjpg")
    async def screen_stream(key: str):
        stream = _screen(key)
        await stream.start()  # errores (p. ej. sin Swift) se ven como 409 antes de transmitir
        return StreamingResponse(
            stream.mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/devices/{key}/screen/frame")
    async def screen_frame(key: str):
        """Última imagen recibida (para mostrarla congelada cuando la pantalla se pausa)."""
        frame = _screen(key).frame
        if not frame:
            raise HTTPException(404, "Todavía no hay imagen de la pantalla.")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/devices/{key}/screen/status")
    async def screen_status(key: str):
        return _screen(key).info()

    @app.post("/api/devices/{key}/screen/capture")
    async def screen_capture(key: str):
        stream = _screen(key)
        if not stream.frame:
            raise HTTPException(409, "Todavía no hay imagen de la pantalla.")
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        path = CAPTURES_DIR / time.strftime("Pantalla %Y-%m-%d %H.%M.%S.jpg")
        path.write_bytes(stream.frame)
        return {"name": path.name}

    # ---------- Capturas guardadas ----------

    def _capture(name: str) -> Path:
        # Sólo archivos directamente dentro de la carpeta de capturas (nada de ../).
        path = CAPTURES_DIR / name
        if Path(name).name != name or path.suffix.lower() not in CAPTURE_EXTENSIONS or not path.is_file():
            raise HTTPException(404, "Captura no encontrada")
        return path

    @app.get("/api/captures")
    async def list_captures():
        if not CAPTURES_DIR.exists():
            return []
        files = [p for p in CAPTURES_DIR.iterdir()
                 if p.is_file() and p.suffix.lower() in CAPTURE_EXTENSIONS]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "date": p.stat().st_mtime, "size": p.stat().st_size} for p in files]

    @app.get("/api/captures/thumb")
    async def capture_thumb(name: str):
        return FileResponse(photos.thumbnail(_capture(name), 480), media_type="image/jpeg")

    @app.get("/api/captures/file")
    async def capture_file(name: str, download: bool = False):
        path = _capture(name)
        return FileResponse(path, filename=path.name if download else None)

    @app.post("/api/captures/open")
    async def open_captures():
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(CAPTURES_DIR)], check=False)
        return {"ok": True}

    # ---------- Mensajes ----------

    @app.post("/api/devices/{key}/messages/refresh")
    async def refresh_messages(key: str):
        return mgr.start_job(key, "messages")

    @app.get("/api/devices/{key}/conversations")
    async def conversations(key: str):
        adapter, device_id = mgr.resolve(key)
        return [asdict(c) for c in await adapter.conversations(device_id)]

    @app.get("/api/devices/{key}/conversations/{conversation_id}/messages")
    async def messages(
        key: str, conversation_id: str, before: float | None = None, limit: int = Query(200, le=1000)
    ):
        adapter, device_id = mgr.resolve(key)
        return [asdict(m) for m in await adapter.messages(device_id, conversation_id, limit, before)]

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
