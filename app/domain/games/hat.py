from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

GAME_TYPE = "hat"
MIN_PLAYERS = 3
MAX_PLAYERS = 40
DEFAULT_WORDS_PER_PLAYER = 3
DEFAULT_ROUND_SECONDS = (30, 30, 30)
COLLECTION_SECONDS = 10 * 60
COUNTDOWN_SECONDS = 3
MIN_WORDS_PER_PLAYER = 1
MAX_WORDS_PER_PLAYER = 100
MIN_ROUND_SECONDS = 10
MAX_ROUND_SECONDS = 300
MAX_WORD_LENGTH = 120
MAX_APPEALS_PER_CARD = 2

PHASE_CONFIGURED = "configured"
PHASE_COLLECTING = "collecting"
PHASE_ROUND_BREAK = "round_break"
PHASE_READY = "ready"
PHASE_COUNTDOWN = "countdown"
PHASE_TURN = "turn"
PHASE_APPEAL = "appeal"
PHASE_LAST_WORD_CLAIM = "last_word_claim"
PHASE_FINAL_BREAK = "final_break"
PHASE_FINISHED = "finished"

TURN_READY = "ready"
TURN_COUNTDOWN = "countdown"
TURN_ACTIVE = "active"
TURN_ENDED = "ended"
TURN_CANCELLED = "cancelled"

CARD_AVAILABLE = "available"
CARD_CURRENT = "current"
CARD_SCORED = "scored"
CARD_VIOLATED = "violated"


def normalize_card(value: str) -> str:
    """Normalize a submitted card for room-wide duplicate detection."""
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", normalized).strip()


def clean_card(value: str) -> str:
    """Keep the player's spelling while normalizing surrounding whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


@dataclass(frozen=True)
class StrictVoteResolution:
    is_resolved: bool
    accepted: bool | None = None


def resolve_strict_vote(
    *, eligible_count: int, yes_count: int, cast_count: int
) -> StrictVoteResolution:
    if eligible_count <= 0:
        return StrictVoteResolution(is_resolved=True, accepted=False)
    if yes_count * 2 > eligible_count:
        return StrictVoteResolution(is_resolved=True, accepted=True)
    if cast_count >= eligible_count:
        return StrictVoteResolution(is_resolved=True, accepted=False)
    return StrictVoteResolution(is_resolved=False)


def next_seat_number(seat_numbers: list[int], current: int) -> int:
    """Return the next active clockwise seat, wrapping around the circle."""
    for seat_number in seat_numbers:
        if seat_number > current:
            return seat_number
    return seat_numbers[0]
