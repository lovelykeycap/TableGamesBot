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
class OpenRoomSummary:
    id: int
    game_type: str
    status: str
    join_code: str


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
class VoteOutcome:
    kind: str
    subject_name: str
    subject_role: str | None
    accepted: bool


@dataclass(frozen=True)
class SpyGameView:
    theme_name: str
    location_name: str
    spy_count: int
    spies_know_each_other: bool
    result: str | None


@dataclass(frozen=True)
class HatWordView:
    id: int
    text: str


@dataclass(frozen=True)
class HatAppealableCardView:
    id: int
    text: str
    explainer_name: str
    guesser_name: str
    attempts_remaining: int


@dataclass(frozen=True)
class HatSeatView:
    member_id: int
    display_name: str
    seat_number: int


@dataclass(frozen=True)
class HatScoreView:
    member_id: int
    display_name: str
    score: int
    is_active: bool


@dataclass(frozen=True)
class HatTurnView:
    id: int
    round_number: int
    status: str
    explainer_member_id: int
    guesser_member_id: int
    explainer_name: str
    guesser_name: str
    explainer_ready: bool
    guesser_ready: bool
    seconds_remaining: int | None


@dataclass(frozen=True)
class HatAppealView:
    id: int
    card_id: int
    card_text: str
    explainer_name: str
    guesser_name: str
    eligible_member_ids: tuple[int, ...]
    yes_count: int
    no_count: int
    actor_choice: bool | None


@dataclass(frozen=True)
class HatLastWordClaimView:
    id: int
    card_id: int
    card_text: str
    explainer_name: str
    guesser_name: str
    eligible_member_ids: tuple[int, ...]
    automatic_yes_member_ids: tuple[int, ...]
    yes_count: int
    no_count: int
    actor_choice: bool | None


@dataclass(frozen=True)
class HatRoomView:
    room_id: int
    status: str
    join_code: str
    join_token: str
    host_user_id: int
    is_join_open: bool
    revision: int
    actor_user_id: int
    actor_member_id: int | None
    actor_status: str | None
    phase: str
    words_per_player: int
    round_seconds: tuple[int, int, int]
    collection_seconds_remaining: int | None
    round_number: int
    round_has_cards: bool
    next_explainer_seat: int | None
    members: tuple[MemberView, ...]
    seats: tuple[HatSeatView, ...]
    submission_counts: tuple[tuple[int, int], ...]
    my_words: tuple[HatWordView, ...]
    appealable_cards: tuple[HatAppealableCardView, ...]
    turn: HatTurnView | None
    current_card_text: str | None
    scores: tuple[HatScoreView, ...]
    appeal: HatAppealView | None
    last_word_claim_card_id: int | None
    last_word_claim: HatLastWordClaimView | None
    result: str | None

    @property
    def is_host(self) -> bool:
        return self.actor_user_id == self.host_user_id

    @property
    def active_members(self) -> tuple[MemberView, ...]:
        return tuple(member for member in self.members if member.status == "active")


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
