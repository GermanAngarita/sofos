from dataclasses import dataclass, field
from typing import Literal

DeviceKind = Literal["ios", "android", "mac"]


@dataclass
class Device:
    id: str
    kind: DeviceKind
    name: str
    model: str = ""
    os_version: str = ""
    # Qué puede hacer esta fuente: "photos", "messages".
    capabilities: list[str] = field(default_factory=list)
    # Estado legible si algo falta (ej. "Desbloquea el teléfono y toca Confiar").
    status: str = "ok"


@dataclass
class Conversation:
    id: str
    title: str
    last_message: str
    last_date: float  # epoch segundos
    message_count: int


@dataclass
class Message:
    id: str
    text: str
    date: float  # epoch segundos
    from_me: bool
    sender: str
