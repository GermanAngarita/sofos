"""Ver la pantalla del iPhone en vivo (sólo ver: iOS no permite controlarlo por cable).

Lanza el ayudante nativo `sofos-screen` (Swift, en sofos/native/screen.swift), que emite
frames JPEG por stdout, y los reparte a los navegadores como MJPEG. Un solo proceso por
teléfono aunque haya varias pestañas mirando; se apaga solo cuando nadie mira.
"""

import asyncio
import os
import shutil
import struct
import subprocess
from pathlib import Path

from sofos.config import DATA_DIR
from sofos.devices.base import DeviceError

SOURCE = Path(__file__).parent / "native" / "screen.swift"
BINARY = DATA_DIR / "bin" / "sofos-screen"
MAX_HEIGHT = 1600  # px; suficiente para leer todo sin saturar el navegador
IDLE_SECONDS = 5  # se detiene si nadie lo mira durante este tiempo


def ensure_binary() -> Path:
    """Compila el ayudante la primera vez (o si cambió el código)."""
    if BINARY.exists() and BINARY.stat().st_mtime >= SOURCE.stat().st_mtime:
        return BINARY
    if not shutil.which("swiftc"):
        raise DeviceError("Para ver la pantalla hace falta Swift: ejecuta  xcode-select --install")
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["swiftc", "-O", "-o", str(BINARY), str(SOURCE)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and "license" in (result.stderr + result.stdout).lower():
        # Xcode instalado pero sin aceptar la licencia: las Command Line Tools sirven igual.
        clt = "/Library/Developer/CommandLineTools"
        if Path(clt).exists():
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    env={**os.environ, "DEVELOPER_DIR": clt})
    if result.returncode != 0:
        raise DeviceError(f"No se pudo compilar el visor de pantalla: {result.stderr.strip()[:500]}")
    return BINARY


def _ere_escape(text: str) -> str:
    return "".join("\\" + c if c in ".[]()*+?{}|^$\\" else c for c in text)


def helper_pattern(binary: Path, udid: str) -> str:
    """Regex para pkill -f que sólo coincide con el ayudante de este iPhone."""
    return f"^{_ere_escape(str(binary))} {_ere_escape(udid)}( |$)"


class ScreenStream:
    def __init__(self, udid: str) -> None:
        self.udid = udid
        self.proc: asyncio.subprocess.Process | None = None
        self.frame: bytes | None = None
        self.frame_id = 0
        self.size: str | None = None
        self.state = "idle"  # idle | starting | streaming | error
        self.message = ""
        self.viewers = 0
        # Mientras se ve la pantalla, iOS manda el sonido del iPhone a la Mac. El ayudante vigila
        # ese sonido y, si el iPhone empieza a reproducir algo, se detiene ("paused") para que
        # el sonido vuelva al iPhone. sound_watch: "vigilando" | "sin permiso" | …
        self.sound_watch = ""
        self._new_frame = asyncio.Condition()
        self._lock = asyncio.Lock()  # un solo arranque/parada a la vez (si no, quedan procesos huérfanos)
        self._tasks: list[asyncio.Task] = []
        self._idle_task: asyncio.Task | None = None

    def _running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        async with self._lock:
            if self._running():
                return
            binary = await asyncio.to_thread(ensure_binary)
            # Por si quedó alguno de una sesión anterior: sólo uno puede usar la pantalla del iPhone.
            # El patrón está anclado a la ruta exacta del ayudante: no toca otros procesos que
            # sólo *mencionen* ese texto (una terminal, un editor, un grep…).
            await asyncio.to_thread(
                subprocess.run, ["pkill", "-f", helper_pattern(binary, self.udid)], capture_output=True)
            # El contador vuelve a 0 junto con el frame: si no, un espectador nuevo creería que
            # ya hay imagen y recibiría la de la sesión anterior (o ninguna).
            async with self._new_frame:
                self.frame, self.frame_id = None, 0
            self.state, self.message, self.sound_watch = "starting", "Iniciando…", ""
            self.proc = await asyncio.create_subprocess_exec(
                str(binary), self.udid, str(MAX_HEIGHT), "1",  # 1 = pausar si el iPhone suena
                stdin=asyncio.subprocess.PIPE,  # órdenes; si Sofos muere, el ayudante también
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            self._tasks = [asyncio.create_task(self._read_frames(self.proc)),
                           asyncio.create_task(self._read_status(self.proc))]
        # Si nadie llega a mirar (p. ej. se cerró la pestaña al instante), que se apague solo.
        self._schedule_idle_stop()

    async def stop(self) -> None:
        async with self._lock:
            if self._running():
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), 3)
                except asyncio.TimeoutError:
                    self.proc.kill()
            for t in self._tasks:
                t.cancel()
            if self.state not in ("error", "paused"):
                self.state, self.message = "idle", ""

    async def _read_frames(self, proc: asyncio.subprocess.Process) -> None:
        try:
            while True:
                header = await proc.stdout.readexactly(4)
                data = await proc.stdout.readexactly(struct.unpack(">I", header)[0])
                async with self._new_frame:
                    if self.proc is not proc:
                        return
                    self.frame, self.frame_id = data, self.frame_id + 1
                    self.state = "streaming"
                    self._new_frame.notify_all()
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass

    async def _read_status(self, proc: asyncio.subprocess.Process) -> None:
        async for raw in proc.stderr:
            if self.proc is not proc:
                continue
            line = raw.decode(errors="replace").strip()
            if line.startswith("ERROR "):
                self.state, self.message = "error", line[6:]
            elif line == "STATUS SOUND":
                self.state = "paused"
                self.message = "Pausé la pantalla porque el iPhone empezó a reproducir sonido, para que suene en el iPhone."
            elif line.startswith("STATUS AUDIO "):
                self.sound_watch = line[len("STATUS AUDIO "):]
            elif line.startswith("STATUS STREAMING"):
                self.size, self.message = line.split()[-1], ""
            elif line.startswith("STATUS "):
                self.message = line[7:]
        await proc.wait()
        if self.proc is proc and self.state not in ("error", "idle", "paused") and self.viewers:
            self.state, self.message = "error", "Se cortó la transmisión. ¿Se desconectó el iPhone?"
        async with self._new_frame:
            self._new_frame.notify_all()  # ya hay returncode: los espectadores pueden terminar

    def _schedule_idle_stop(self) -> None:
        async def idle() -> None:
            await asyncio.sleep(IDLE_SECONDS)
            if self.viewers == 0:
                await self.stop()

        if self._idle_task:
            self._idle_task.cancel()
        self._idle_task = asyncio.create_task(idle())

    async def mjpeg(self):
        """Generador para StreamingResponse (multipart/x-mixed-replace)."""
        self.viewers += 1
        try:
            await self.start()
            proc, last = self.proc, 0
            while True:
                async with self._new_frame:
                    await self._new_frame.wait_for(
                        lambda: self.frame_id > last or proc.returncode is not None or self.proc is not proc)
                    frame, frame_id = self.frame, self.frame_id
                if self.proc is not proc or frame is None or frame_id <= last:
                    break  # el proceso terminó o se reinició: el navegador reconecta
                last = frame_id
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
        finally:
            self.viewers -= 1
            if self.viewers == 0:
                self._schedule_idle_stop()

    def info(self) -> dict:
        return {"state": self.state, "message": self.message, "size": self.size,
                "viewers": self.viewers, "sound_watch": self.sound_watch}


class ScreenManager:
    def __init__(self) -> None:
        self.streams: dict[str, ScreenStream] = {}

    def get(self, udid: str) -> ScreenStream:
        return self.streams.setdefault(udid, ScreenStream(udid))

    async def stop_all(self) -> None:
        for s in self.streams.values():
            await s.stop()
