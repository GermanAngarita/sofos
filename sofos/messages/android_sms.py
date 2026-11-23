"""Parseo de la salida de `adb shell content query --uri content://sms`."""

import re
from collections import defaultdict

from sofos.models import Conversation, Message

# Pedimos las columnas en este orden y con `body` al final, porque el cuerpo
# puede contener comas y saltos de línea.
PROJECTION = "_id:thread_id:address:date:type:body"
_ROW = re.compile(
    r"^Row: \d+ _id=(?P<id>\d+), thread_id=(?P<thread>\d+), address=(?P<address>.*?), "
    r"date=(?P<date>\d+), type=(?P<type>\d+), body=(?P<body>.*)$",
    re.DOTALL,
)
SENT_TYPES = {"2", "4", "5", "6"}  # enviado, saliente, fallido, en cola


def parse_rows(output: str) -> list[dict]:
    # Juntamos líneas de continuación (cuerpos con saltos de línea).
    records: list[str] = []
    for line in output.splitlines():
        if line.startswith("Row: "):
            records.append(line)
        elif records:
            records[-1] += "\n" + line
    rows = []
    for rec in records:
        m = _ROW.match(rec)
        if m:
            rows.append(m.groupdict())
    return rows


def to_conversations(rows: list[dict]) -> list[Conversation]:
    threads: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        threads[r["thread"]].append(r)
    convs = []
    for thread_id, items in threads.items():
        last = max(items, key=lambda r: int(r["date"]))
        address = next((r["address"] for r in items if r["address"] not in ("", "NULL")), "")
        convs.append(
            Conversation(
                id=thread_id,
                title=address or f"Hilo {thread_id}",
                last_message=last["body"],
                last_date=int(last["date"]) / 1000,
                message_count=len(items),
            )
        )
    return sorted(convs, key=lambda c: c.last_date, reverse=True)


def to_messages(rows: list[dict], thread_id: str, limit: int, before: float | None) -> list[Message]:
    items = [r for r in rows if r["thread"] == thread_id]
    if before is not None:
        items = [r for r in items if int(r["date"]) / 1000 < before]
    items.sort(key=lambda r: int(r["date"]))
    return [
        Message(
            id=r["id"],
            text=r["body"],
            date=int(r["date"]) / 1000,
            from_me=r["type"] in SENT_TYPES,
            sender="Yo" if r["type"] in SENT_TYPES else r["address"],
        )
        for r in items[-limit:]
    ]
