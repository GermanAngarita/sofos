import asyncio
import sys
from pathlib import Path

from sofos import screen

FAKE_HELPER = r'''#!{python}
import struct, sys, time
sys.stderr.write("STATUS Buscando la pantalla del iPhone…\n"); sys.stderr.flush()
for i in range(3):
    data = b"\xff\xd8frame%d" % i
    sys.stdout.buffer.write(struct.pack(">I", len(data)) + data); sys.stdout.buffer.flush()
    if i == 0:
        sys.stderr.write("STATUS STREAMING 750x1624\n"); sys.stderr.flush()
    time.sleep(0.05)
if "{mode}" == "error":
    sys.stderr.write("ERROR No apareció la pantalla del iPhone.\n"); sys.stderr.flush()
    sys.exit(2)
time.sleep(30)
'''


def fake_binary(tmp_path, monkeypatch, mode="ok"):
    path = tmp_path / "sofos-screen"
    path.write_text(FAKE_HELPER.replace("{python}", sys.executable).replace("{mode}", mode))
    path.chmod(0o755)
    monkeypatch.setattr(screen, "ensure_binary", lambda: path)


def test_stream_yields_mjpeg_frames(tmp_path, monkeypatch):
    fake_binary(tmp_path, monkeypatch)

    async def run():
        s = screen.ScreenStream("abc")
        chunks = []
        gen = s.mjpeg()
        async for chunk in gen:
            chunks.append(chunk)
            if len(chunks) == 3:
                break
        await gen.aclose()
        info = s.info()
        await s.stop()
        return chunks, info

    chunks, info = asyncio.run(run())
    assert chunks[0].startswith(b"--frame\r\nContent-Type: image/jpeg")
    assert chunks[2].endswith(b"frame2\r\n")
    assert info["state"] == "streaming" and info["size"] == "750x1624"


def test_helper_error_is_reported(tmp_path, monkeypatch):
    fake_binary(tmp_path, monkeypatch, mode="error")

    async def run():
        s = screen.ScreenStream("abc")
        frames = [c async for c in s.mjpeg()]  # termina sola cuando el ayudante sale
        await asyncio.sleep(0.1)
        return frames, s.info()

    frames, info = asyncio.run(run())
    assert len(frames) == 3
    assert info["state"] == "error"
    assert "No apareció" in info["message"]


async def _take(stream, n):
    frames = []
    gen = stream.mjpeg()
    async for chunk in gen:
        frames.append(chunk)
        if len(frames) == n:
            break
    await gen.aclose()
    return frames


def test_restart_after_stop_streams_again(tmp_path, monkeypatch):
    """Regresión: tras detener y volver a ver, el segundo espectador no recibía nada."""
    fake_binary(tmp_path, monkeypatch)

    async def run():
        s = screen.ScreenStream("abc")
        first = await _take(s, 2)
        await s.stop()
        second = await _take(s, 2)
        await s.stop()
        return first, second

    first, second = asyncio.run(run())
    assert len(first) == 2 and len(second) == 2
    assert all(b"\xff\xd8frame" in c for c in first + second)


def test_concurrent_viewers_share_one_helper(tmp_path, monkeypatch):
    """Regresión: dos pedidos a la vez lanzaban dos procesos y uno quedaba huérfano."""
    fake_binary(tmp_path, monkeypatch)

    async def run():
        s = screen.ScreenStream("abc")
        spawned = []
        original = asyncio.create_subprocess_exec

        async def counting(*args, **kwargs):
            proc = await original(*args, **kwargs)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", counting)
        a, b = await asyncio.gather(_take(s, 2), _take(s, 2))
        await s.stop()
        return a, b, spawned

    a, b, spawned = asyncio.run(run())
    assert len(a) == 2 and len(b) == 2
    assert len(spawned) == 1


def test_sound_on_iphone_pauses_stream(tmp_path, monkeypatch):
    """Si el iPhone empieza a sonar, el ayudante sale y la pantalla queda en pausa (no error)."""
    helper = tmp_path / "sofos-screen"
    helper.write_text(f"""#!{sys.executable}
import struct, sys, time
sys.stderr.write("STATUS AUDIO vigilando\\n"); sys.stderr.flush()
for i in range(2):
    sys.stdout.buffer.write(struct.pack(">I", 6) + b"\\xff\\xd8img%d" % i); sys.stdout.buffer.flush()
    time.sleep(0.05)
sys.stderr.write("STATUS SOUND\\n"); sys.stderr.flush()
""")
    helper.chmod(0o755)
    monkeypatch.setattr(screen, "ensure_binary", lambda: helper)

    async def run():
        s = screen.ScreenStream("abc")
        frames = [c async for c in s.mjpeg()]  # termina sola al pausarse
        await asyncio.sleep(0.2)
        return frames, s.info(), s.frame

    frames, info, last = asyncio.run(run())
    assert len(frames) == 2
    assert info["state"] == "paused" and "sonido" in info["message"]
    assert last == b"\xff\xd8img1"  # la última imagen queda para mostrarla congelada


def test_orphan_cleanup_only_matches_the_helper():
    import re
    pat = re.compile(screen.helper_pattern(Path("/Users/x/.sofos/bin/sofos-screen"), "0000-AB"))
    assert pat.search("/Users/x/.sofos/bin/sofos-screen 0000-AB 1600 1")
    assert not pat.search("/bin/zsh -c pkill -f 'sofos-screen 0000-AB'")
    assert not pat.search("vim /Users/x/.sofos/bin/sofos-screen 0000-AB")
    assert not pat.search("/Users/x/.sofos/bin/sofos-screen 0000-ABC 1600")
