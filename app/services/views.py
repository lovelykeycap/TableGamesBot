from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ThemeOption:
    id: int
    name: str
    is_selected: bool
    has_active_locations: bool


@dataclass(frozen=True)
class MemberView:
    id: int
    user_id: int
    display_name: str
    username: str | None
    status: str
    role: str | None
    joined_at: datetime


@dataclass(frozen=True)
class VoteView:
    id: int
    kind: str
    subject_member_id: int
    eligible_member_ids: tuple[int, ...]
    yes_count: int
    no_count: int
    actor_choice: bool | None


@dataclass(frozen=True)
class SpyGameView:
    theme_name: str
    location_name: str
    spy_count: int
    spies_know_each_other: bool
    result: str | None


@dataclass(frozen=True)
class RoomView:
    room_id: int
    status: str
    game_type: str
    join_code: str
    join_token: str
    host_user_id: int
    is_join_open: bool
    settings: dict[str, object]
    revision: int
    actor_user_id: int
    actor_member_id: int | None
    actor_status: str | None
    actor_role: str | None
    members: tuple[MemberView, ...]
    themes: tuple[ThemeOption, ...]
    game: SpyGameView | None
    vote: VoteView | None
    known_spy_names: tuple[str, ...]

    @property
    def is_host(self) -> bool:
        return self.host_user_id == self.actor_user_id

    @property
    def active_members(self) -> tuple[MemberView, ...]:
        return tuple(member for member in self.members if member.status == "active")
