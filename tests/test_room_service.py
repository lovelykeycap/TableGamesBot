from __future__ import annotations

import pytest

from app.domain.errors import DomainError
from app.domain.games.spy import ROLE_CIVILIAN, VOTE_EXILE
from app.services.room_service import RoomService


async def _started_room(service: RoomService) -> int:
    room_id = await service.create_spy_room(1)
    code = (await service.get_room_view(room_id, 1)).join_code
    for user_id in range(2, 5):
        await service.join_by_code(code, user_id)
    await service.set_exact_spy_count(room_id, 1, 1)
    await service.start_spy_game(room_id, 1)
    return room_id


@pytest.mark.asyncio
async def test_tied_exile_vote_keeps_the_candidate(room_service: RoomService) -> None:
    room_id = await _started_room(room_service)
    before = await room_service.get_room_view(room_id, 1)
    target = next(member for member in before.members if member.user_id == 2)
    await room_service.propose_exile(room_id, 1, target.id)
    vote_id = (await room_service.get_room_view(room_id, 1)).vote.id  # type: ignore[union-attr]

    await room_service.cast_vote(room_id, 3, vote_id, True)
    await room_service.cast_vote(room_id, 4, vote_id, False)

    after = await room_service.get_room_view(room_id, 1)
    assert after.vote is None
    assert next(member for member in after.members if member.id == target.id).status == "active"


@pytest.mark.asyncio
async def test_exiling_last_spy_finishes_with_civilian_win(room_service: RoomService) -> None:
    room_id = await _started_room(room_service)
    view = await room_service.get_room_view(room_id, 1)
    spy = next(member for member in view.members if member.role == "spy")
    civilian = next(member for member in view.members if member.role == "civilian")
    await room_service.propose_exile(room_id, civilian.user_id, spy.id)
    vote_id = (await room_service.get_room_view(room_id, civilian.user_id)).vote.id  # type: ignore[union-attr]

    for user_id in range(1, 5):
        if user_id in {civilian.user_id, spy.user_id}:
            continue
        await room_service.cast_vote(room_id, user_id, vote_id, True)

    final = await room_service.get_room_view(room_id, 1)
    assert final.status == "finished"
    assert final.game is not None
    assert final.game.result == "civilians_win"


@pytest.mark.asyncio
async def test_failed_spy_reveal_exiles_that_spy(room_service: RoomService) -> None:
    room_id = await _started_room(room_service)
    view = await room_service.get_room_view(room_id, 1)
    spy = next(member for member in view.members if member.role == "spy")
    await room_service.begin_reveal(room_id, spy.user_id)
    vote_id = (await room_service.get_room_view(room_id, spy.user_id)).vote.id  # type: ignore[union-attr]

    for user_id in range(1, 5):
        await room_service.cast_vote(room_id, user_id, vote_id, False)

    final = await room_service.get_room_view(room_id, 1)
    assert final.status == "finished"
    assert final.game is not None
    assert final.game.result == "civilians_win"
    assert next(member for member in final.members if member.id == spy.id).status == "exiled"


@pytest.mark.asyncio
async def test_exile_vote_counts_proposer_as_yes_and_candidate_as_no(
    room_service: RoomService,
) -> None:
    room_id = await _started_room(room_service)
    view = await room_service.get_room_view(room_id, 1)
    target = next(member for member in view.members if member.user_id == 2)

    await room_service.propose_exile(room_id, 1, target.id)

    proposer_view = await room_service.get_room_view(room_id, 1)
    candidate_view = await room_service.get_room_view(room_id, 2)
    assert proposer_view.vote is not None
    assert candidate_view.vote is not None
    assert proposer_view.vote.yes_count == 1
    assert proposer_view.vote.no_count == 1
    assert proposer_view.vote.actor_choice is True
    assert candidate_view.vote.actor_choice is False
    with pytest.raises(DomainError, match="Ваш голос уже учтён"):
        await room_service.cast_vote(room_id, 2, proposer_view.vote.id, False)


@pytest.mark.asyncio
async def test_accepted_exile_of_a_civilian_returns_a_notice_outcome(
    room_service: RoomService,
) -> None:
    room_id = await _started_room(room_service)
    view = await room_service.get_room_view(room_id, 1)
    target = next(member for member in view.members if member.role == ROLE_CIVILIAN)
    proposer = next(member for member in view.members if member.id != target.id)
    await room_service.propose_exile(room_id, proposer.user_id, target.id)
    vote_id = (await room_service.get_room_view(room_id, proposer.user_id)).vote.id  # type: ignore[union-attr]

    outcome = None
    for member in view.members:
        if member.id not in {proposer.id, target.id}:
            outcome = await room_service.cast_vote(room_id, member.user_id, vote_id, True)

    assert outcome is not None
    assert outcome.kind == VOTE_EXILE
    assert outcome.accepted is True
    assert outcome.subject_name == target.display_name
    assert outcome.subject_role == ROLE_CIVILIAN
    after = await room_service.get_room_view(room_id, proposer.user_id)
    assert next(member for member in after.members if member.id == target.id).status == "exiled"


@pytest.mark.asyncio
async def test_playing_room_is_found_for_active_players_and_spectators(
    room_service: RoomService,
) -> None:
    room_id = await _started_room(room_service)
    view = await room_service.get_room_view(room_id, 1)
    civilian = next(member for member in view.members if member.role == "civilian")

    assert await room_service.playing_room_for_user(1) == room_id
    lobby_id = await room_service.create_spy_room(1)
    summaries = await room_service.open_rooms_for_user(1)
    assert {(summary.id, summary.game_type, summary.status) for summary in summaries} == {
        (room_id, "spy", "playing"),
        (lobby_id, "spy", "lobby"),
    }
    await room_service.leave_room(room_id, civilian.user_id)

    assert await room_service.playing_room_for_user(civilian.user_id) == room_id
