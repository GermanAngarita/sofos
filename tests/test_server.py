import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sofos import config, photos
from sofos.devices.base import DeviceAdapter
from sofos.manager import DeviceManager
from sofos.messages import apple_db
from sofos.models import Device


class FakeAdapter(DeviceAdapter):
    kind = "ios"

    def __init__(self, db, tmp):
        self.db = db
        self.tmp = tmp

    async def list_devices(self):
        return [Device(id="abc", kind="ios", name="iPhone de prueba", capabilities=["photos", "messages"])]

    async def sync_photos(self, device_id, dest):
        dest.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 600), "red").save(dest / "IMG_0001.JPG")
        return 1

    async def browse_photos(self, device_id):
        return [
            {"path": "DCIM/100APPLE/IMG_0001.JPG", "name": "IMG_0001.JPG", "date": 1_763_500_000,
             "is_video": False, "size": 10},
            {"path": "DCIM/100APPLE/IMG_0002.JPG", "name": "IMG_0002.JPG", "date": 1_700_000_000,
             "is_video": False, "size": 20},
        ]

    async def transfer_photos(self, device_id, plan, on_progress):
        sizes = {"DCIM/100APPLE/IMG_0001.JPG": 10, "DCIM/100APPLE/IMG_0002.JPG": 20}
        for path, target in plan:
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4)).save(target, "JPEG")
            target.write_bytes(b"x" * sizes[path])  # mismo tamaño que en el teléfono
            on_progress(sizes[path])
        return len(plan)

    async def photo_thumbnail(self, device_id, path):
        return b"\xff\xd8thumb" if path == "DCIM/100APPLE/IMG_0001.JPG" else None

    async def open_photo(self, device_id, path):
        if path != "DCIM/100APPLE/IMG_0001.JPG":
            raise FileNotFoundError(path)
        out = self.tmp / "IMG_0001.JPG"
        Image.new("RGB", (10, 10), "blue").save(out)
        return out

    async def conversations(self, device_id):
        return apple_db.list_conversations(self.db)

    async def messages(self, device_id, conversation_id, limit=200, before=None):
        return apple_db.list_messages(self.db, conversation_id, limit, before)


@pytest.fixture
def client(tmp_path, chat_db, monkeypatch):
    import sofos.server as server_mod

    monkeypatch.setattr(config, "SAVED_PHOTOS_DIR", tmp_path / "descargas" / "Fotos")
    monkeypatch.setattr(server_mod, "CAPTURES_DIR", tmp_path / "descargas" / "Capturas")
    monkeypatch.setattr(photos, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(photos, "THUMBS_DIR", tmp_path / "thumbs")
    from sofos.server import create_app

    with TestClient(create_app(DeviceManager([FakeAdapter(chat_db, tmp_path)]))) as c:
        yield c


def test_full_flow(client):
    devices = client.get("/api/devices").json()
    assert devices[0]["key"] == "ios:abc"

    assert client.post("/api/devices/ios:abc/photos/sync").status_code == 200
    for _ in range(50):
        job = client.get("/api/devices/ios:abc/job").json()
        if not job["running"]:
            break
    assert job["message"] == "Listo: 1 fotos nuevas."

    items = client.get("/api/devices/ios:abc/photos").json()["items"]
    assert items[0]["name"] == "IMG_0001.JPG"
    thumb = client.get("/api/devices/ios:abc/photos/thumb", params={"path": items[0]["path"]})
    assert thumb.headers["content-type"] == "image/jpeg"

    convs = client.get("/api/devices/ios:abc/conversations").json()
    msgs = client.get(f"/api/devices/ios:abc/conversations/{convs[1]['id']}/messages").json()
    assert msgs[-1]["text"] == "Hola!"


def test_path_traversal_blocked(client):
    r = client.get("/api/devices/ios:abc/photos/file", params={"path": "../../etc/passwd"})
    assert r.status_code == 404


def test_unknown_device(client):
    assert client.get("/api/devices/nada:1/conversations").status_code == 409
    assert client.get("/").status_code == 200


def test_phone_photos_without_copying(client, tmp_path):
    data = client.get("/api/devices/ios:abc/phone-photos").json()
    assert data["total"] == 2 and data["saved"] == 0
    path = data["items"][0]["path"]
    assert client.get("/api/devices/ios:abc/phone-photos/thumb", params={"path": path}).content == b"\xff\xd8thumb"
    full = client.get("/api/devices/ios:abc/phone-photos/file", params={"path": path, "download": 1})
    assert full.status_code == 200 and "IMG_0001.JPG" in full.headers["content-disposition"]
    assert client.get("/api/devices/ios:abc/phone-photos/file", params={"path": "x/../y"}).status_code == 404
    assert not (tmp_path / "descargas").exists()  # ver no copia nada


def _transfer(client, paths):
    r = client.post("/api/devices/ios:abc/phone-photos/transfer", json={"paths": paths})
    assert r.status_code == 200, r.text
    for _ in range(100):
        job = client.get("/api/devices/ios:abc/job").json()
        if not job["running"]:
            return job
    raise AssertionError("la transferencia no terminó")


def test_transfer_saves_by_month_and_marks_saved(client, tmp_path):
    assert client.post("/api/devices/ios:abc/phone-photos/transfer", json={"paths": []}).status_code == 400
    job = _transfer(client, ["DCIM/100APPLE/IMG_0001.JPG"])
    assert not job["error"], job["message"]
    assert (job["done"], job["total"]) == (1, 1)

    fotos = tmp_path / "descargas" / "Fotos"
    files = [str(p.relative_to(fotos)) for p in fotos.rglob("*.JPG")]
    assert files == ["2025-11/IMG_0001.JPG"]

    listing = client.get("/api/devices/ios:abc/phone-photos").json()
    assert {i["name"]: i["saved"] for i in listing["items"]} == {"IMG_0001.JPG": True, "IMG_0002.JPG": False}

    # Volver a pedir la misma foto no la duplica; la otra sí se guarda.
    job = _transfer(client, ["DCIM/100APPLE/IMG_0001.JPG", "DCIM/100APPLE/IMG_0002.JPG"])
    assert job["done"] == 1 and "1 ya estaban guardadas" in job["message"]
    assert sorted(str(p.relative_to(fotos)) for p in fotos.rglob("*.JPG")) == [
        "2023-11/IMG_0002.JPG", "2025-11/IMG_0001.JPG"]

    saved = client.get("/api/saved").json()
    assert saved["total"] == 2
    assert client.get("/api/saved/file", params={"path": "2025-11/IMG_0001.JPG"}).status_code == 200
    assert client.get("/api/saved/file", params={"path": "../../etc/passwd"}).status_code == 404


def test_transfer_rejects_paths_not_on_phone(client):
    job = _transfer(client, ["DCIM/100APPLE/NO_EXISTE.JPG"])
    assert job["error"] and "Ya no están" in job["message"]


def test_captures_gallery(client, tmp_path):
    assert client.get("/api/captures").json() == []
    captures = tmp_path / "descargas" / "Capturas"
    captures.mkdir(parents=True)
    Image.new("RGB", (390, 844), "green").save(captures / "Pantalla 2026-11-19 21.07.46.jpg")
    (captures / "notas.txt").write_text("no es captura")
    (tmp_path / "secreto.jpg").write_bytes(b"x")

    listed = client.get("/api/captures").json()
    assert [c["name"] for c in listed] == ["Pantalla 2026-11-19 21.07.46.jpg"]
    name = listed[0]["name"]
    assert client.get("/api/captures/thumb", params={"name": name}).headers["content-type"] == "image/jpeg"
    dl = client.get("/api/captures/file", params={"name": name, "download": 1})
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    for bad in ["../../secreto.jpg", "notas.txt", "no-existe.jpg"]:
        assert client.get("/api/captures/file", params={"name": bad}).status_code == 404
