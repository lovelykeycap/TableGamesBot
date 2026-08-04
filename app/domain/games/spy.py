from __future__ import annotations

from dataclasses import dataclass

GAME_TYPE = "spy"
MIN_PLAYERS = 3
MAX_PLAYERS = 40
ROLE_CIVILIAN = "civilian"
ROLE_SPY = "spy"

VOTE_EXILE = "exile"
VOTE_REVEAL = "reveal"


@dataclass(frozen=True)
class VoteResolution:
    is_resolved: bool
    accepted: bool | None = None


def resolve_vote(*, eligible_count: int, yes_count: int, cast_count: int) -> VoteResolution:
    """Return the outcome implied by the agreed strict-majority rules.

    A strict yes majority is final immediately. Otherwise, the vote only closes
    after every current eligible player has voted; a tie and any no-majority
    result then mean rejection.
    """
    if eligible_count <= 0:
        return VoteResolution(is_resolved=True, accepted=False)
    if yes_count * 2 > eligible_count:
        return VoteResolution(is_resolved=True, accepted=True)
    if cast_count >= eligible_count:
        return VoteResolution(is_resolved=True, accepted=False)
    return VoteResolution(is_resolved=False)
