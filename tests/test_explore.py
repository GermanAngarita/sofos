import plistlib
import sqlite3

import pytest

from sofos import explore


def test_binary_plist_becomes_readable_json(tmp_path):
    p = tmp_path / "info.plist"
    p.write_bytes(plistlib.dumps({"Nombre": "iPhone", "Datos": b"\x00\x01", "N": 3}, fmt=plistlib.FMT_BINARY))
    pv = explore.preview(p)
    assert pv["kind"] == "text"
    assert '"Nombre": "iPhone"' in pv["text"] and "<2 bytes>" in pv["text"]


def test_text_and_binary_detection(tmp_path):
    t = tmp_path / "notas"
    t.write_text("hola\nmundo")
    assert explore.preview(t) == {"kind": "text", "text": "hola\nmundo", "truncated": False}
    b = tmp_path / "raro.bin"
    b.write_bytes(bytes(range(256)))
    pv = explore.preview(b)
    assert pv["kind"] == "binary" and pv["text"].startswith("00000000  00 01 02")


def test_media_kinds(tmp_path):
    for name, kind in [("a.HEIC", "image"), ("b.mov", "video"), ("c.opus", "audio"), ("d.pdf", "pdf")]:
        f = tmp_path / name
        f.write_bytes(b"x")
        assert explore.preview(f)["kind"] == kind


def test_sqlite_tables_and_rows_with_wal(tmp_path):
    db = tmp_path / "Chat.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE mensajes (id INTEGER, texto TEXT, adjunto BLOB)")
    conn.executemany("INSERT INTO mensajes VALUES (?,?,?)", [(1, "hola", b"\x00" * 10), (2, "x" * 400, None)])
    conn.commit()  # los datos quedan en el -wal hasta un checkpoint
    pv = explore.preview(db)
    assert pv == {"kind": "sqlite", "tables": [{"name": "mensajes", "columns": ["id", "texto", "adjunto"], "rows": 2}]}
    rows = explore.sqlite_rows(db, "mensajes")
    assert rows["rows"][0] == [1, "hola", "<10 bytes>"]
    assert rows["rows"][1][1].endswith("…") and len(rows["rows"][1][1]) == 301
    with pytest.raises(KeyError):
        explore.sqlite_rows(db, 'mensajes"; DROP TABLE mensajes; --')
    conn.close()


def test_sqlite_detected_without_extension(tmp_path):
    db = tmp_path / "sin_extension"
    sqlite3.connect(db).execute("CREATE TABLE t (a)").connection.commit()
    assert explore.preview(db)["kind"] == "sqlite"
