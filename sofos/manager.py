"""Une todas las fuentes y lleva el estado de las tareas largas (copiar fotos, respaldos)."""

import asyncio
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from sofos import photos, saved
from sofos.devices.android import AndroidAdapter
from sofos.devices.base import DeviceAdapter, DeviceError
from sofos.devices.ios import IOSAdapter
from sofos.devices.mac import MacMessagesAdapter


@dataclass
class Job:
    kind: str  # "photos" | "messages" | "transfer"
    running: bool = True
    message: str = ""
    error: bool = False
    finished_at: float | None = None
    # Progreso (sólo "transfer")
    total: int = 0
    done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    started_at: float = 0.0
    result_dir: str | None = None


class DeviceManager:
    def __init__(self, adapters: list[DeviceAdapter] | None = None) -> None:
        adapters = adapters or [IOSAdapter(), AndroidAdapter(), MacMessagesAdapter()]
        self.adapters = {a.kind: a for a in adapters}
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    def resolve(self, key: str) -> tuple[DeviceAdapter, str]:
        """`key` tiene la forma "<tipo>:<id>", ej. "ios:00008110-..."."""
        kind, _, device_id = key.partition(":")
        if kind not in self.adapters or not device_id:
            raise DeviceError(f"Dispositivo desconocido: {key}")
        return self.adapters[kind], device_id

    async def list_devices(self) -> list[dict]:
        results = await asyncio.gather(
            *(a.list_devices() for a in self.adapters.values()), return_exceptions=True
        )
        devices = []
        for adapter, result in zip(self.adapters.values(), results):
            if isinstance(result, Exception):
                continue
            for d in result:
                key = f"{adapter.kind}:{d.id}"
                devices.append({**asdict(d), "key": key, "job": self.job_dict(key)})
        return devices

    def job_dict(self, key: str) -> dict | None:
        job = self.jobs.get(key)
        return asdict(job) if job else None

    def start_job(self, key: str, kind: str, paths: list[str] | None = None) -> dict:
        current = self.jobs.get(key)
        if current and current.running:
            raise DeviceError("Ya hay una tarea en curso para este dispositivo.")
        adapter, device_id = self.resolve(key)
        messages = {"photos": "Copiando fotos…", "messages": "Copiando mensajes…",
                    "transfer": "Guardando fotos…"}
        job = Job(kind=kind, message=messages[kind], started_at=time.time())
        self.jobs[key] = job

        async def run() -> None:
            try:
                if kind == "transfer":
                    await self._transfer(adapter, device_id, paths or [], job)
                elif kind == "photos":
                    n = await adapter.sync_photos(device_id, photos.device_dir(key))
                    job.message = f"Listo: {n} fotos nuevas." if n else "Ya estaba todo al día."
                else:
                    await adapter.refresh_messages(device_id)
                    job.message = "Mensajes actualizados."
            except DeviceError as exc:
                job.error, job.message = True, str(exc)
            except Exception as exc:  # noqa: BLE001 — mostramos cualquier falla en la UI
                job.error, job.message = True, f"Error inesperado: {exc!r}"
            finally:
                job.running, job.finished_at = False, time.time()

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return asdict(job)

    @staticmethod
    async def _transfer(adapter: DeviceAdapter, device_id: str, paths: list[str], job: Job) -> None:
        by_path = {p["path"]: p for p in await adapter.browse_photos(device_id)}
        unknown = [p for p in paths if p not in by_path]
        if unknown:
            raise DeviceError(f"Ya no están en el teléfono: {', '.join(unknown[:3])}")
        keys = saved.saved_keys()
        chosen = [by_path[p] for p in paths]
        pending = [i for i in chosen if not saved.is_saved(i, keys)]
        already = len(chosen) - len(pending)
        job.total, job.bytes_total = len(pending), sum(i.get("size", 0) for i in pending)
        job.result_dir = str(saved.root())

        def on_progress(nbytes: int) -> None:
            job.done += 1
            job.bytes_done += nbytes
            job.message = f"Guardando fotos… {job.done} de {job.total}"

        if pending:
            await adapter.transfer_photos(device_id, saved.plan_targets(pending), on_progress)
        secs = max(time.time() - job.started_at, 0.01)
        parts = []
        if job.done:
            parts.append(f"{job.done} guardadas ({job.bytes_done / 1e6:.0f} MB) en {secs:.0f} s")
        if already:
            parts.append(f"{already} ya estaban guardadas")
        job.message = "Listo: " + " · ".join(parts) + " → descargas/Fotos"
