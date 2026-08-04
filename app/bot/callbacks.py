from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoomCallback:
    room_id: int
    revision: int
    action: str
    argument: str = ""


def pack_room_callback(room_id: int, revision: int, action: str, argument: str = "") -> str:
    value = f"r:{room_id}:{revision}:{action}:{argument}"
    if len(value.encode()) > 64:
        raise ValueError("Telegram callback payload is too long")
    return value


def unpack_room_callback(value: str) -> RoomCallback | None:
    parts = value.split(":", 4)
    if len(parts) != 5 or parts[0] != "r":
        return None
    try:
        room_id = int(parts[1])
        revision = int(parts[2])
    except ValueError:
        return None
    if not parts[3]:
        return None
    return RoomCallback(room_id=room_id, revision=revision, action=parts[3], argument=parts[4])
