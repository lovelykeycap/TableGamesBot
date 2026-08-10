from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.domain.errors import DomainError
from app.domain.games.hat import PHASE_READY, PHASE_ROUND_BREAK, PHASE_TURN
from app.services.hat_service import HatService
from app.services.room_service import RoomService
from app.storage.models import HatRoundCard, HatTurn


async def _collect_three_cards(service: RoomService) -> tuple[HatService, int]:
    hats = HatService(service)
    room_id = await hats.create_room(1)
    code = (await hats.get_room_view(room_id, 1)).join_code
    for user_id in (2, 3):
        await service.join_by_code(code, user_id)
    await hats.randomize_seats(room_id, 1)
    await hats.set_words_per_player(room_id, 1, 1)
    await hats.start_collection(room_id, 1)
    for user_id, card in ((1, "Алиса"), (2, "Боб"), (3, "Кэрол")):
        await hats.add_word(room_id, user_id, card)
    return hats, room_id


async def _start_active_turn(hats: HatService, service: RoomService, room_id: int) -> int:
    ready = await hats.get_room_view(room_id, 1)
    assert ready.turn is not None
    explainer_user_id = next(
        member.user_id for member in ready.members if member.id == ready.turn.explainer_member_id
    )
    guesser_user_id = next(
        member.user_id for member in ready.members if member.id == ready.turn.guesser_member_id
    )
    await hats.set_ready(room_id, explainer_user_id, True)
    await hats.set_ready(room_id, guesser_user_id, True)
    async with service._database.session() as session, session.begin():  # noqa: SLF001
        turn = await session.scalar(
            select(HatTurn).where(HatTurn.room_id == room_id, HatTurn.status == "countdown")
        )
        assert turn is not None
        turn.countdown_deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await hats.process_due(room_id)
    return explainer_user_id


async def _timeout_active_turn(hats: HatService, service: RoomService, room_id: int) -> None:
    async with service._database.session() as session, session.begin():  # noqa: SLF001
        turn = await session.scalar(
            select(HatTurn).where(HatTurn.room_id == room_id, HatTurn.status == "active")
        )
        assert turn is not None
        turn.turn_deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert await hats.process_due(room_id)


@pytest.mark.asyncio
async def test_hat_rejects_normalized_duplicate_cards(room_service: RoomService) -> None:
    hats = HatService(room_service)
    room_id = await hats.create_room(1)
    code = (await hats.get_room_view(room_id, 1)).join_code
    for user_id in (2, 3):
        await room_service.join_by_code(code, user_id)
    await hats.randomize_seats(room_id, 1)
    await hats.start_collection(room_id, 1)
    await hats.add_word(room_id, 1, "Ёжик")

    with pytest.raises(DomainError, match="уже внёс"):
        await hats.add_word(room_id, 2, "  ежик ")


@pytest.mark.asyncio
async def test_hat_round_scores_and_auto_resolves_single_voter_appeal(
    room_service: RoomService,
) -> None:
    hats, room_id = await _collect_three_cards(room_service)
    collected = await hats.get_room_view(room_id, 1)
    assert collected.phase == PHASE_ROUND_BREAK
    assert collected.round_number == 1

    await hats.start_round(room_id, 1)
    explainer_user_id = await _start_active_turn(hats, room_service, room_id)
    assert room_id in await hats.active_turn_room_ids()

    active = await hats.get_room_view(room_id, 1)
    assert active.phase == PHASE_TURN
    assert active.turn is not None
    guesser_member_id = active.turn.guesser_member_id
    for _ in range(3):
        await hats.mark_correct(room_id, explainer_user_id)

    after_round = await hats.get_room_view(room_id, 1)
    assert after_round.phase == PHASE_ROUND_BREAK
    assert after_round.round_number == 1
    assert after_round.round_has_cards
    proposer = next(
        member
        for member in after_round.active_members
        if member.id != guesser_member_id and member.id != active.turn.explainer_member_id
    )
    proposer_view = await hats.get_room_view(room_id, proposer.user_id)
    appeal_card = proposer_view.appealable_cards[0]

    outcome = await hats.start_appeal(room_id, proposer.user_id, appeal_card.id)
    assert outcome is not None
    assert outcome.accepted
    final = await hats.get_room_view(room_id, 1)
    assert final.phase == PHASE_ROUND_BREAK
    assert final.scores[0].score == 2
    await hats.start_round(room_id, 1)
    next_round = await hats.get_room_view(room_id, 1)
    assert next_round.round_number == 2
    assert next_round.turn is not None


@pytest.mark.asyncio
async def test_hat_card_is_visible_only_to_explainer_and_appeals_are_scoped_to_last_turn(
    room_service: RoomService,
) -> None:
    hats = HatService(room_service)
    room_id = await hats.create_room(1)
    code = (await hats.get_room_view(room_id, 1)).join_code
    for user_id in (2, 3):
        await room_service.join_by_code(code, user_id)
    await hats.randomize_seats(room_id, 1)
    await hats.set_words_per_player(room_id, 1, 2)
    await hats.start_collection(room_id, 1)
    for user_id, cards in ((1, ("Алиса", "Анна")), (2, ("Боб", "Борис")), (3, ("Кэрол", "Кира"))):
        for card in cards:
            await hats.add_word(room_id, user_id, card)
    await hats.start_round(room_id, 1)

    first_explainer_user_id = await _start_active_turn(hats, room_service, room_id)
    first_active = await hats.get_room_view(room_id, first_explainer_user_id)
    assert first_active.phase == PHASE_TURN
    assert first_active.turn is not None
    assert first_active.current_card_text is not None
    observer_user_id = next(
        member.user_id
        for member in first_active.members
        if member.id
        not in {first_active.turn.explainer_member_id, first_active.turn.guesser_member_id}
    )
    assert (await hats.get_room_view(room_id, observer_user_id)).current_card_text is None

    await hats.mark_correct(room_id, first_explainer_user_id)
    async with room_service._database.session() as session:
        first_card = await session.scalar(
            select(HatRoundCard)
            .where(HatRoundCard.room_id == room_id, HatRoundCard.state == "scored")
            .order_by(HatRoundCard.id)
        )
        assert first_card is not None
        first_card_id = first_card.id
    with pytest.raises(DomainError, match="между ходами"):
        await hats.start_appeal(room_id, observer_user_id, first_card_id)

    await _timeout_active_turn(hats, room_service, room_id)
    between_first_and_second = await hats.get_room_view(room_id, observer_user_id)
    assert between_first_and_second.phase == PHASE_READY
    assert [card.id for card in between_first_and_second.appealable_cards] == [first_card_id]

    second_explainer_user_id = await _start_active_turn(hats, room_service, room_id)
    second_active = await hats.get_room_view(room_id, second_explainer_user_id)
    assert second_active.turn is not None
    second_pair = {
        second_active.turn.explainer_member_id,
        second_active.turn.guesser_member_id,
    }
    await hats.mark_correct(room_id, second_explainer_user_id)
    await _timeout_active_turn(hats, room_service, room_id)
    after_second_turn = await hats.get_room_view(room_id, 1)
    assert after_second_turn.phase == PHASE_READY
    latest_turn = next(
        member for member in after_second_turn.active_members if member.id not in second_pair
    )
    latest_view = await hats.get_room_view(room_id, latest_turn.user_id)
    assert len(latest_view.appealable_cards) == 1
    assert latest_view.appealable_cards[0].id != first_card_id
    with pytest.raises(DomainError, match="последнего хода"):
        await hats.start_appeal(room_id, latest_turn.user_id, first_card_id)
