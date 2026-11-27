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
    # Cómo está conectado ahora: "usb" | "wifi" (vacío si no aplica, p. ej. la Mac).
    connection: str = ""
    # Si el teléfono acepta conexiones por Wi-Fi desde esta Mac (None = no se sabe / no aplica).
    wifi_enabled: bool | None = None


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
