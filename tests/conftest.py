import sqlite3
from pathlib import Path

import pytest

APPLE_EPOCH = 978307200


def make_apple_db(path: Path) -> Path:
    """Base mínima con el esquema de Mensajes de Apple."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB,
                              date INTEGER, is_from_me INTEGER, handle_id INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        INSERT INTO handle VALUES (1, '+573001112233'), (2, 'ana@icloud.com');
        INSERT INTO chat VALUES (10, '+573001112233', ''), (20, 'chat123', 'Familia');
        INSERT INTO chat_handle_join VALUES (10, 1), (20, 1), (20, 2);
        """
    )
    ns = lambda unix: int((unix - APPLE_EPOCH) * 1e9)
    # Texto moderno: text NULL y contenido en attributedBody.
    body = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x05Hola!\x86"
    rows = [
        (1, "hola", None, ns(1_700_000_000), 0, 1),
        (2, "qué tal", None, ns(1_700_000_100), 1, 0),
        (3, None, body, ns(1_700_000_200), 0, 1),
        (4, "cena el domingo", None, ns(1_700_100_000), 0, 2),
        (5, "￼", None, ns(1_700_100_050), 1, 0),
    ]
    conn.executemany("INSERT INTO message VALUES (?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO chat_message_join VALUES (?,?)", [(10, 1), (10, 2), (10, 3), (20, 4), (20, 5)]
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def chat_db(tmp_path):
    return make_apple_db(tmp_path / "chat.db")
