from __future__ import annotations

import pytest

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

    await room_service.cast_vote(room_id, 1, vote_id, True)
    await room_service.cast_vote(room_id, 2, vote_id, False)
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

    for user_id in (1, 2, 3):
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
