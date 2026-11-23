"""Lectura de la base de Mensajes de Apple.

El mismo esquema se usa en `sms.db` (dentro de un respaldo de iPhone) y en
`~/Library/Messages/chat.db` (app Mensajes de la Mac).
"""

import sqlite3
from pathlib import Path

from sofos.models import Conversation, Message

APPLE_EPOCH = 978307200  # 2001-01-01 en epoch Unix
ATTACHMENT_CHAR = "￼"


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _date_scale(conn: sqlite3.Connection) -> float:
    """iOS 11+ guarda nanosegundos; versiones viejas, segundos."""
    row = conn.execute("SELECT MAX(date) FROM message").fetchone()
    return 1e9 if row[0] and row[0] > 1e12 else 1.0


def apple_to_unix(value: int | None, scale: float) -> float:
    return (value or 0) / scale + APPLE_EPOCH


def unix_to_apple(value: float, scale: float) -> int:
    return int((value - APPLE_EPOCH) * scale)


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Saca el texto plano de `attributedBody` (NSAttributedString en typedstream).

    Desde macOS Ventura / iOS 16 muchos mensajes tienen `text` NULL y el
    contenido sólo vive aquí.
    """
    if not blob:
        return None
    idx = blob.find(b"NSString")
    if idx == -1:
        return None
    rest = blob[idx + len(b"NSString") + 5 :]
    if not rest:
        return None
    if rest[0] == 0x81:  # largo en 2 bytes little-endian
        length, start = int.from_bytes(rest[1:3], "little"), 3
    elif rest[0] == 0x82:  # largo en 4 bytes little-endian
        length, start = int.from_bytes(rest[1:5], "little"), 5
    else:
        length, start = rest[0], 1
    return rest[start : start + length].decode("utf-8", errors="replace")


def _text(row: sqlite3.Row) -> str:
    text = row["text"] or decode_attributed_body(row["attributedBody"]) or ""
    clean = text.replace(ATTACHMENT_CHAR, "").strip()
    if not clean:
        return "📎 Adjunto"
    return f"📎 {clean}" if ATTACHMENT_CHAR in text else clean


def list_conversations(path: Path) -> list[Conversation]:
    with open_db(path) as conn:
        scale = _date_scale(conn)
        rows = conn.execute(
            """
            WITH stats AS (
                SELECT cmj.chat_id, COUNT(*) AS n, MAX(m.date) AS last_date
                FROM chat_message_join cmj JOIN message m ON m.ROWID = cmj.message_id
                GROUP BY cmj.chat_id
            )
            SELECT c.ROWID AS id, c.chat_identifier, c.display_name, s.n, s.last_date,
                (SELECT GROUP_CONCAT(h.id, ', ') FROM chat_handle_join chj
                    JOIN handle h ON h.ROWID = chj.handle_id
                    WHERE chj.chat_id = c.ROWID) AS participants,
                lm.text, lm.attributedBody
            FROM chat c
            JOIN stats s ON s.chat_id = c.ROWID
            LEFT JOIN message lm ON lm.ROWID = (
                SELECT m2.ROWID FROM chat_message_join j2 JOIN message m2 ON m2.ROWID = j2.message_id
                WHERE j2.chat_id = c.ROWID ORDER BY m2.date DESC LIMIT 1)
            ORDER BY s.last_date DESC
            """
        ).fetchall()
    return [
        Conversation(
            id=str(r["id"]),
            title=r["display_name"] or r["participants"] or r["chat_identifier"] or "Sin nombre",
            last_message=_text(r),
            last_date=apple_to_unix(r["last_date"], scale),
            message_count=r["n"],
        )
        for r in rows
    ]


def list_messages(
    path: Path, conversation_id: str, limit: int = 200, before: float | None = None
) -> list[Message]:
    """Mensajes de una conversación en orden cronológico; `before` pagina hacia atrás."""
    with open_db(path) as conn:
        scale = _date_scale(conn)
        before_raw = unix_to_apple(before, scale) if before is not None else None
        rows = conn.execute(
            """
            SELECT m.ROWID AS id, m.text, m.attributedBody, m.date, m.is_from_me, h.id AS handle
            FROM chat_message_join cmj
            JOIN message m ON m.ROWID = cmj.message_id
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            WHERE cmj.chat_id = ? AND (? IS NULL OR m.date < ?)
            ORDER BY m.date DESC LIMIT ?
            """,
            (int(conversation_id), before_raw, before_raw, limit),
        ).fetchall()
    return [
        Message(
            id=str(r["id"]),
            text=_text(r),
            date=apple_to_unix(r["date"], scale),
            from_me=bool(r["is_from_me"]),
            sender="Yo" if r["is_from_me"] else (r["handle"] or ""),
        )
        for r in reversed(rows)
    ]
