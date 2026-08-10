from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from random import SystemRandom

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.errors import DomainError
from app.domain.games.hat import (
    CARD_AVAILABLE,
    CARD_CURRENT,
    CARD_SCORED,
    CARD_VIOLATED,
    COLLECTION_SECONDS,
    COUNTDOWN_SECONDS,
    GAME_TYPE,
    MAX_APPEALS_PER_CARD,
    MAX_ROUND_SECONDS,
    MAX_WORD_LENGTH,
    MAX_WORDS_PER_PLAYER,
    MIN_PLAYERS,
    MIN_ROUND_SECONDS,
    MIN_WORDS_PER_PLAYER,
    PHASE_APPEAL,
    PHASE_COLLECTING,
    PHASE_CONFIGURED,
    PHASE_COUNTDOWN,
    PHASE_FINAL_BREAK,
    PHASE_FINISHED,
    PHASE_LAST_WORD_CLAIM,
    PHASE_READY,
    PHASE_ROUND_BREAK,
    PHASE_TURN,
    TURN_ACTIVE,
    TURN_CANCELLED,
    TURN_COUNTDOWN,
    TURN_ENDED,
    TURN_READY,
    clean_card,
    next_seat_number,
    normalize_card,
    resolve_strict_vote,
)
from app.services.room_service import RoomService
from app.services.views import (
    HatAppealableCardView,
    HatAppealView,
    HatLastWordClaimView,
    HatRoomView,
    HatScoreView,
    HatSeatView,
    HatTurnView,
    HatWordView,
    MemberView,
)
from app.storage.models import (
    HatAppeal,
    HatAppealVote,
    HatGame,
    HatLastWordClaim,
    HatLastWordClaimVote,
    HatRoundCard,
    HatSeat,
    HatTurn,
    HatWord,
    Room,
    RoomMember,
    User,
)


@dataclass(frozen=True)
class HatAppealOutcome:
    accepted: bool
    card_text: str
    explainer_name: str
    guesser_name: str


@dataclass(frozen=True)
class HatLastWordClaimOutcome:
    accepted: bool
    card_text: str
    explainer_name: str
    guesser_name: str


class HatService:
    def __init__(self, rooms: RoomService) -> None:
        self._rooms = rooms
        self._random = SystemRandom()

    async def create_room(self, host_user_id: int) -> int:
        return await self._rooms.create_hat_room(host_user_id)

    async def set_join_open(self, room_id: int, user_id: int, is_open: bool) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_configuring_hat(session, room, user_id)
            room.is_join_open = is_open
            self._bump(room)

    async def set_words_per_player(self, room_id: int, user_id: int, value: int) -> None:
        if not MIN_WORDS_PER_PLAYER <= value <= MAX_WORDS_PER_PLAYER:
            raise DomainError(
                "Карточек от игрока должно быть от "
                f"{MIN_WORDS_PER_PLAYER} до {MAX_WORDS_PER_PLAYER}."
            )
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_configuring_hat(session, room, user_id)
            game.words_per_player = value
            self._bump(room)

    async def set_round_seconds(
        self, room_id: int, user_id: int, round_number: int, value: int
    ) -> None:
        if round_number not in {1, 2, 3}:
            raise DomainError("У раунда должен быть номер от 1 до 3.")
        if not MIN_ROUND_SECONDS <= value <= MAX_ROUND_SECONDS:
            raise DomainError(
                f"Время раунда должно быть от {MIN_ROUND_SECONDS} до {MAX_ROUND_SECONDS} секунд."
            )
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_configuring_hat(session, room, user_id)
            values = list(game.round_seconds)
            values[round_number - 1] = value
            game.round_seconds = values
            self._bump(room)

    async def select_manual_seat(self, room_id: int, user_id: int, member_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_configuring_hat(session, room, user_id)
            target = await session.get(RoomMember, member_id)
            if target is None or target.room_id != room.id or target.status != "active":
                raise DomainError("Этого игрока нельзя поставить в круг.")
            if await session.scalar(
                select(HatSeat.id).where(HatSeat.room_id == room.id, HatSeat.member_id == target.id)
            ):
                raise DomainError("Этот игрок уже выбран в порядке.")
            selected = list(
                await session.scalars(
                    select(HatSeat).where(HatSeat.room_id == room.id).order_by(HatSeat.seat_number)
                )
            )
            session.add(
                HatSeat(room_id=room.id, member_id=target.id, seat_number=len(selected) + 1)
            )
            self._bump(room)

    async def undo_manual_seat(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_configuring_hat(session, room, user_id)
            seat = await session.scalar(
                select(HatSeat)
                .where(HatSeat.room_id == room.id)
                .order_by(HatSeat.seat_number.desc())
                .limit(1)
            )
            if seat is None:
                raise DomainError("В порядке пока нет игроков.")
            await session.delete(seat)
            self._bump(room)

    async def randomize_seats(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_configuring_hat(session, room, user_id)
            members = await self._active_members(session, room.id)
            if len(members) < MIN_PLAYERS:
                raise DomainError("Для игры нужно как минимум три игрока.")
            await session.execute(delete(HatSeat).where(HatSeat.room_id == room.id))
            for seat_number, member in enumerate(
                self._random.sample(members, len(members)), start=1
            ):
                session.add(HatSeat(room_id=room.id, member_id=member.id, seat_number=seat_number))
            self._bump(room)

    async def confirm_seats(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_configuring_hat(session, room, user_id)
            await self._require_complete_seats(session, room.id)
            self._bump(room)

    async def start_collection(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_configuring_hat(session, room, user_id)
            await self._require_complete_seats(session, room.id)
            room.is_join_open = False
            room.status = "playing"
            room.started_at = self._now()
            game.phase = PHASE_COLLECTING
            game.collection_deadline_at = self._now() + timedelta(seconds=COLLECTION_SECONDS)
            self._bump(room)

    async def add_word(self, room_id: int, user_id: int, value: str) -> None:
        text, normalized = self._validate_word(value)
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_collecting_hat(session, room)
            member = await self._require_active_member(session, room.id, user_id)
            submitted = await session.scalar(
                select(func.count(HatWord.id)).where(
                    HatWord.room_id == room.id, HatWord.author_member_id == member.id
                )
            )
            if int(submitted or 0) >= game.words_per_player:
                raise DomainError("Вы уже внесли нужное число карточек.")
            duplicate = await session.scalar(
                select(HatWord.id).where(
                    HatWord.room_id == room.id, HatWord.normalized_text == normalized
                )
            )
            if duplicate is not None:
                raise DomainError("Кто-то уже внёс такую карточку.")
            session.add(
                HatWord(
                    room_id=room.id,
                    author_member_id=member.id,
                    text=text,
                    normalized_text=normalized,
                )
            )
            await session.flush()
            await self._complete_collection_if_ready(session, room, game)
            self._bump(room)

    async def edit_word(self, room_id: int, user_id: int, word_id: int, value: str) -> None:
        text, normalized = self._validate_word(value)
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_collecting_hat(session, room)
            member = await self._require_active_member(session, room.id, user_id)
            word = await session.get(HatWord, word_id)
            if word is None or word.room_id != room.id or word.author_member_id != member.id:
                raise DomainError("Эту карточку можно изменить только её автору.")
            duplicate = await session.scalar(
                select(HatWord.id).where(
                    HatWord.room_id == room.id,
                    HatWord.normalized_text == normalized,
                    HatWord.id != word.id,
                )
            )
            if duplicate is not None:
                raise DomainError("Кто-то уже внёс такую карточку.")
            word.text = text
            word.normalized_text = normalized
            self._bump(room)

    async def delete_word(self, room_id: int, user_id: int, word_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            await self._require_collecting_hat(session, room)
            member = await self._require_active_member(session, room.id, user_id)
            word = await session.get(HatWord, word_id)
            if word is None or word.room_id != room.id or word.author_member_id != member.id:
                raise DomainError("Эту карточку можно удалить только её автору.")
            await session.delete(word)
            self._bump(room)

    async def start_round(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if room.host_user_id != user_id:
                raise DomainError("Начать раунд может только хост.")
            if game.phase != PHASE_ROUND_BREAK:
                raise DomainError("Сейчас нельзя начинать новый раунд.")
            if game.round_number not in {1, 2, 3}:
                raise DomainError("Номер раунда не определён.")
            await self._require_minimum_active(session, room)
            exists = await session.scalar(
                select(HatRoundCard.id).where(
                    HatRoundCard.room_id == room.id, HatRoundCard.round_number == game.round_number
                )
            )
            if exists is not None:
                game.round_number += 1
                if game.round_number > 3:
                    raise RuntimeError("Attempted to start a fourth Hat round")
                exists = None
            if exists is None:
                words = list(
                    await session.scalars(
                        select(HatWord).where(HatWord.room_id == room.id).order_by(HatWord.id)
                    )
                )
                if not words:
                    raise DomainError("В банке нет карточек.")
                for word in words:
                    session.add(
                        HatRoundCard(
                            room_id=room.id,
                            word_id=word.id,
                            round_number=game.round_number,
                            state=CARD_AVAILABLE,
                        )
                    )
                await session.flush()
            await self._create_ready_turn(session, room, game)
            self._bump(room)

    async def set_ready(self, room_id: int, user_id: int, ready: bool) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if game.phase != PHASE_READY:
                raise DomainError("Сейчас нельзя менять готовность.")
            turn = await self._current_turn(session, room.id)
            if turn is None or turn.status != TURN_READY:
                raise DomainError("Текущий ход уже изменился.")
            member = await self._require_active_member(session, room.id, user_id)
            if member.id == turn.explainer_member_id:
                turn.explainer_ready = ready
            elif member.id == turn.guesser_member_id:
                turn.guesser_ready = ready
            else:
                raise DomainError("Готовность подтверждает только текущая пара.")
            if turn.explainer_ready and turn.guesser_ready:
                turn.status = TURN_COUNTDOWN
                turn.countdown_deadline_at = self._now() + timedelta(seconds=COUNTDOWN_SECONDS)
                game.phase = PHASE_COUNTDOWN
            self._bump(room)

    async def mark_correct(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game, turn, card = await self._require_active_turn(session, room, user_id)
            if turn.explainer_member_id != card.explainer_member_id:
                raise RuntimeError("Current Hat card lost its explainer")
            card.state = CARD_SCORED
            card.score_counted = True
            await self._draw_or_finish_round(session, room, game, turn)
            self._bump(room)

    async def confirm_violation(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game, _turn, card = await self._require_active_turn(session, room, user_id)
            card.state = CARD_VIOLATED
            card.score_counted = False
            await self._draw_or_finish_round(session, room, game, _turn)
            self._bump(room)

    async def start_appeal(
        self, room_id: int, user_id: int, card_id: int
    ) -> HatAppealOutcome | None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if game.phase not in {PHASE_READY, PHASE_ROUND_BREAK, PHASE_FINAL_BREAK}:
                raise DomainError("Апелляция доступна только между ходами.")
            if await self._open_appeal(session, room.id) is not None:
                raise DomainError("Сначала завершите текущую апелляцию.")
            appeal_turn = await self._latest_appealable_turn(session, room.id, game.round_number)
            if appeal_turn is None:
                raise DomainError("После этого хода нет карточек для апелляции.")
            proposer = await self._require_active_member(session, room.id, user_id)
            card = await session.get(HatRoundCard, card_id)
            if (
                card is None
                or card.room_id != room.id
                or card.round_number != game.round_number
                or card.turn_id != appeal_turn.id
                or card.state != CARD_SCORED
                or not card.score_counted
            ):
                raise DomainError("Можно обжаловать только карточки последнего хода.")
            if card.appeal_attempts >= MAX_APPEALS_PER_CARD:
                raise DomainError("Для этой карточки исчерпаны попытки апелляции.")
            if proposer.id in {card.explainer_member_id, card.guesser_member_id}:
                raise DomainError("Игравшая пара не может обжаловать свою карточку.")
            eligible = [
                member.id
                for member in await self._active_members(session, room.id)
                if member.id not in {card.explainer_member_id, card.guesser_member_id}
            ]
            if proposer.id not in eligible:
                raise DomainError("Вы не участвуете в этой апелляции.")
            appeal = HatAppeal(
                room_id=room.id,
                round_card_id=card.id,
                proposer_member_id=proposer.id,
                eligible_member_ids=eligible,
                status="open",
            )
            session.add(appeal)
            card.appeal_attempts += 1
            await session.flush()
            session.add(HatAppealVote(appeal_id=appeal.id, member_id=proposer.id, choice=True))
            await session.flush()
            game.phase = PHASE_APPEAL
            outcome = await self._resolve_appeal_if_ready(session, room, game, appeal)
            self._bump(room)
            return outcome

    async def cast_appeal_vote(
        self, room_id: int, user_id: int, appeal_id: int, choice: bool
    ) -> HatAppealOutcome | None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if game.phase != PHASE_APPEAL:
                raise DomainError("Сейчас нет открытой апелляции.")
            appeal = await session.get(HatAppeal, appeal_id)
            if appeal is None or appeal.room_id != room.id or appeal.status != "open":
                raise DomainError("Эта апелляция уже закрыта.")
            voter = await self._require_active_member(session, room.id, user_id)
            eligible = await self._current_appeal_eligible(
                session, room.id, appeal.eligible_member_ids
            )
            appeal.eligible_member_ids = [member.id for member in eligible]
            if voter.id not in appeal.eligible_member_ids:
                raise DomainError("Вы не голосуете в этой апелляции.")
            prior = await session.scalar(
                select(HatAppealVote).where(
                    HatAppealVote.appeal_id == appeal.id, HatAppealVote.member_id == voter.id
                )
            )
            if prior is not None:
                raise DomainError("Ваш голос уже учтён.")
            session.add(HatAppealVote(appeal_id=appeal.id, member_id=voter.id, choice=choice))
            await session.flush()
            outcome = await self._resolve_appeal_if_ready(session, room, game, appeal)
            self._bump(room)
            return outcome

    async def start_last_word_claim(
        self, room_id: int, user_id: int
    ) -> HatLastWordClaimOutcome | None:
        """Open the former explainer's one-time vote for a word at the deadline."""
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if game.phase != PHASE_READY:
                raise DomainError("Последнее слово можно заявить до начала отсчёта следующей пары.")
            requester = await self._require_active_member(session, room.id, user_id)
            candidate = await self._last_word_claim_candidate(session, room.id, game.round_number)
            if candidate is None:
                raise DomainError("Для последнего слова уже нет доступной попытки.")
            previous_turn, card = candidate
            if requester.id != previous_turn.explainer_member_id:
                raise DomainError("Последнее слово может заявить только прошлый объясняющий.")
            automatic_yes = [
                previous_turn.explainer_member_id,
                previous_turn.guesser_member_id,
            ]
            active_ids = [member.id for member in await self._active_members(session, room.id)]
            eligible = list(dict.fromkeys([*active_ids, *automatic_yes]))
            claim = HatLastWordClaim(
                room_id=room.id,
                previous_turn_id=previous_turn.id,
                round_card_id=card.id,
                requester_member_id=requester.id,
                eligible_member_ids=eligible,
                automatic_yes_member_ids=automatic_yes,
                status="open",
            )
            session.add(claim)
            await session.flush()
            session.add_all(
                HatLastWordClaimVote(claim_id=claim.id, member_id=member_id, choice=True)
                for member_id in automatic_yes
            )
            await session.flush()
            game.phase = PHASE_LAST_WORD_CLAIM
            outcome = await self._resolve_last_word_claim_if_ready(session, room, game, claim)
            self._bump(room)
            return outcome

    async def cast_last_word_claim_vote(
        self, room_id: int, user_id: int, claim_id: int, choice: bool
    ) -> HatLastWordClaimOutcome | None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if game.phase != PHASE_LAST_WORD_CLAIM:
                raise DomainError("Сейчас нет голосования за последнее слово.")
            claim = await session.get(HatLastWordClaim, claim_id)
            if claim is None or claim.room_id != room.id or claim.status != "open":
                raise DomainError("Это голосование уже закрыто.")
            voter = await self._require_active_member(session, room.id, user_id)
            eligible = await self._current_last_word_claim_eligible(session, room.id, claim)
            claim.eligible_member_ids = eligible
            if voter.id in claim.automatic_yes_member_ids:
                raise DomainError("Голос прошлой пары уже автоматически учтён.")
            if voter.id not in eligible:
                raise DomainError("Вы не голосуете за последнее слово.")
            prior = await session.scalar(
                select(HatLastWordClaimVote).where(
                    HatLastWordClaimVote.claim_id == claim.id,
                    HatLastWordClaimVote.member_id == voter.id,
                )
            )
            if prior is not None:
                raise DomainError("Ваш голос уже учтён.")
            session.add(HatLastWordClaimVote(claim_id=claim.id, member_id=voter.id, choice=choice))
            await session.flush()
            outcome = await self._resolve_last_word_claim_if_ready(session, room, game, claim)
            self._bump(room)
            return outcome

    async def finish_game(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if room.host_user_id != user_id:
                raise DomainError("Завершить игру может только хост.")
            if game.phase != PHASE_FINAL_BREAK:
                raise DomainError("Игра ещё не дошла до финального перерыва.")
            await self._finish(session, room, game, "completed")
            self._bump(room)

    async def emergency_cancel(self, room_id: int, user_id: int) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            if room.host_user_id != user_id or game.phase in {PHASE_CONFIGURED, PHASE_FINISHED}:
                raise DomainError("Аварийно завершить игру может только текущий хост.")
            await self._finish(session, room, game, "cancelled")
            self._bump(room)

    async def leave_room(self, room_id: int, user_id: int, *, technical: bool = False) -> None:
        async with self._rooms.room_transaction(room_id) as (session, room):
            game = await self._require_hat_game(session, room)
            member = await self._require_active_member(session, room.id, user_id)
            member.status = "left"
            member.left_at = self._now()
            if technical:
                member.is_reachable = False
            await self._transfer_host_if_needed(session, room)
            if game.phase == PHASE_COLLECTING:
                await session.execute(delete(HatWord).where(HatWord.author_member_id == member.id))
                if len(await self._active_members(session, room.id)) < MIN_PLAYERS:
                    await self._finish(session, room, game, "insufficient_players")
                else:
                    await self._complete_collection_if_ready(session, room, game)
            elif room.status == "playing":
                await self._handle_departure(session, room, game, member.id)
            elif room.status == "lobby" and not await self._active_members(session, room.id):
                room.status = "cancelled"
            self._bump(room)

    async def mark_delivery_failure(self, room_id: int, user_id: int) -> None:
        try:
            await self.leave_room(room_id, user_id, technical=True)
        except DomainError:
            return

    async def due_room_ids(self) -> list[int]:
        now = self._now()
        async with self._rooms._database.session() as session:  # noqa: SLF001
            collection = list(
                await session.scalars(
                    select(HatGame.room_id).where(
                        HatGame.phase == PHASE_COLLECTING,
                        HatGame.collection_deadline_at.is_not(None),
                        HatGame.collection_deadline_at <= now,
                    )
                )
            )
            countdown = list(
                await session.scalars(
                    select(HatTurn.room_id).where(
                        HatTurn.status == TURN_COUNTDOWN,
                        HatTurn.countdown_deadline_at.is_not(None),
                        HatTurn.countdown_deadline_at <= now,
                    )
                )
            )
            turns = list(
                await session.scalars(
                    select(HatTurn.room_id).where(
                        HatTurn.status == TURN_ACTIVE,
                        HatTurn.turn_deadline_at.is_not(None),
                        HatTurn.turn_deadline_at <= now,
                    )
                )
            )
        return sorted(set(collection + countdown + turns))

    async def process_due(self, room_id: int) -> bool:
        """Apply one overdue deadline. Returns True when the room changed."""
        async with self._rooms.room_transaction(room_id) as (session, room):
            if room.game_type != GAME_TYPE:
                return False
            game = await self._require_hat_game(session, room)
            changed = False
            if (
                game.phase == PHASE_COLLECTING
                and game.collection_deadline_at is not None
                and self._is_due(game.collection_deadline_at)
            ):
                await self._expire_collection(session, room, game)
                changed = True
            else:
                turn = await self._current_turn(session, room.id)
                if (
                    game.phase == PHASE_COUNTDOWN
                    and turn is not None
                    and turn.countdown_deadline_at is not None
                    and self._is_due(turn.countdown_deadline_at)
                ):
                    await self._activate_turn(session, room, game, turn)
                    changed = True
                elif (
                    game.phase == PHASE_TURN
                    and turn is not None
                    and turn.turn_deadline_at is not None
                    and self._is_due(turn.turn_deadline_at)
                ):
                    await self._timeout_turn(session, room, game, turn)
                    changed = True
            if changed:
                self._bump(room)
            return changed

    async def turn_user_ids(self, room_id: int) -> tuple[int, ...]:
        async with self._rooms._database.session() as session:  # noqa: SLF001
            turn = await self._current_turn(session, room_id)
            if turn is None or turn.status not in {TURN_COUNTDOWN, TURN_ACTIVE}:
                return ()
            members = list(
                await session.scalars(
                    select(RoomMember).where(
                        RoomMember.id.in_([turn.explainer_member_id, turn.guesser_member_id])
                    )
                )
            )
            return tuple(member.user_id for member in members if member.is_reachable)

    async def active_turn_room_ids(self) -> list[int]:
        async with self._rooms._database.session() as session:  # noqa: SLF001
            return sorted(
                set(
                    await session.scalars(
                        select(HatTurn.room_id).where(
                            HatTurn.status.in_([TURN_COUNTDOWN, TURN_ACTIVE])
                        )
                    )
                )
            )

    async def get_room_view(self, room_id: int, actor_user_id: int) -> HatRoomView:
        async with self._rooms._database.session() as session:  # noqa: SLF001
            room = await session.get(Room, room_id)
            if room is None or room.game_type != GAME_TYPE:
                raise DomainError("Комната «Шляпы» не найдена.")
            game = await self._require_hat_game(session, room)
            members = await self._members_with_users(session, room.id)
            member_by_id = {member.id: member for member in members}
            actor = next((member for member in members if member.user_id == actor_user_id), None)
            seats = list(
                await session.scalars(
                    select(HatSeat).where(HatSeat.room_id == room.id).order_by(HatSeat.seat_number)
                )
            )
            seat_views = tuple(
                HatSeatView(
                    member_id=seat.member_id,
                    display_name=(
                        member_by_id[seat.member_id].display_name
                        if seat.member_id in member_by_id
                        else "Игрок"
                    ),
                    seat_number=seat.seat_number,
                )
                for seat in seats
            )
            counts = await session.execute(
                select(HatWord.author_member_id, func.count(HatWord.id))
                .where(HatWord.room_id == room.id)
                .group_by(HatWord.author_member_id)
            )
            submission_counts = tuple((int(member_id), int(count)) for member_id, count in counts)
            my_words: tuple[HatWordView, ...] = ()
            if actor is not None and game.phase == PHASE_COLLECTING:
                words = list(
                    await session.scalars(
                        select(HatWord)
                        .where(HatWord.room_id == room.id, HatWord.author_member_id == actor.id)
                        .order_by(HatWord.id)
                    )
                )
                my_words = tuple(HatWordView(id=word.id, text=word.text) for word in words)
            appealable_cards: tuple[HatAppealableCardView, ...] = ()
            appeal_turn = (
                await self._latest_appealable_turn(session, room.id, game.round_number)
                if game.phase in {PHASE_READY, PHASE_ROUND_BREAK, PHASE_FINAL_BREAK}
                else None
            )
            if appeal_turn is not None:
                rows = await session.execute(
                    select(HatRoundCard, HatWord)
                    .join(HatWord, HatWord.id == HatRoundCard.word_id)
                    .where(
                        HatRoundCard.room_id == room.id,
                        HatRoundCard.round_number == game.round_number,
                        HatRoundCard.turn_id == appeal_turn.id,
                        HatRoundCard.state == CARD_SCORED,
                        HatRoundCard.score_counted.is_(True),
                        HatRoundCard.appeal_attempts < MAX_APPEALS_PER_CARD,
                    )
                    .order_by(HatRoundCard.id)
                )
                appealable_cards = tuple(
                    HatAppealableCardView(
                        id=card.id,
                        text=word.text,
                        explainer_name=(
                            member_by_id[card.explainer_member_id].display_name
                            if card.explainer_member_id in member_by_id
                            else "Игрок"
                        ),
                        guesser_name=(
                            member_by_id[card.guesser_member_id].display_name
                            if card.guesser_member_id in member_by_id
                            else "Игрок"
                        ),
                        attempts_remaining=MAX_APPEALS_PER_CARD - card.appeal_attempts,
                    )
                    for card, word in rows
                    if actor is not None
                    and actor.status == "active"
                    and actor.id not in {card.explainer_member_id, card.guesser_member_id}
                )
            turn = await self._current_turn(session, room.id)
            turn_view = self._turn_view(turn, member_by_id)
            current_text = (
                await self._current_card_text(session, room.id)
                if actor is not None and turn is not None and actor.id == turn.explainer_member_id
                else None
            )
            appeal_view = await self._appeal_view(
                session, room.id, actor.id if actor else None, member_by_id
            )
            last_word_claim_card_id: int | None = None
            if actor is not None and actor.status == "active" and game.phase == PHASE_READY:
                candidate = await self._last_word_claim_candidate(
                    session, room.id, game.round_number
                )
                if candidate is not None and candidate[0].explainer_member_id == actor.id:
                    last_word_claim_card_id = candidate[1].id
            last_word_claim = await self._last_word_claim_view(
                session, room.id, actor.id if actor else None, member_by_id
            )
            scores = await self._scores(session, room.id, members)
            collection_remaining = self._seconds_remaining(game.collection_deadline_at)
            round_seconds = tuple(int(value) for value in game.round_seconds)
            round_has_cards = (
                await session.scalar(
                    select(HatRoundCard.id)
                    .where(
                        HatRoundCard.room_id == room.id,
                        HatRoundCard.round_number == game.round_number,
                    )
                    .limit(1)
                )
            ) is not None
            return HatRoomView(
                room_id=room.id,
                status=room.status,
                join_code=room.join_code,
                join_token=room.join_token,
                host_user_id=room.host_user_id,
                is_join_open=room.is_join_open,
                revision=room.revision,
                actor_user_id=actor_user_id,
                actor_member_id=actor.id if actor else None,
                actor_status=actor.status if actor else None,
                phase=game.phase,
                words_per_player=game.words_per_player,
                round_seconds=(round_seconds[0], round_seconds[1], round_seconds[2]),
                collection_seconds_remaining=collection_remaining,
                round_number=game.round_number,
                round_has_cards=round_has_cards,
                next_explainer_seat=game.next_explainer_seat,
                members=tuple(members),
                seats=seat_views,
                submission_counts=submission_counts,
                my_words=my_words,
                appealable_cards=appealable_cards,
                turn=turn_view,
                current_card_text=current_text,
                scores=scores,
                appeal=appeal_view,
                last_word_claim_card_id=last_word_claim_card_id,
                last_word_claim=last_word_claim,
                result=game.result,
            )

    async def _require_hat_game(self, session: AsyncSession, room: Room) -> HatGame:
        if room.game_type != GAME_TYPE:
            raise DomainError("Эта комната не относится к игре «Шляпа».")
        game = await session.get(HatGame, room.id)
        if game is None:
            raise RuntimeError("Hat game state is missing")
        return game

    async def _require_configuring_hat(
        self, session: AsyncSession, room: Room, user_id: int
    ) -> HatGame:
        game = await self._require_hat_game(session, room)
        if room.host_user_id != user_id:
            raise DomainError("Эта настройка доступна только хосту.")
        if room.status != "lobby" or game.phase != PHASE_CONFIGURED:
            raise DomainError("Настройки и порядок можно менять только до сбора карточек.")
        return game

    async def _require_collecting_hat(self, session: AsyncSession, room: Room) -> HatGame:
        game = await self._require_hat_game(session, room)
        if game.phase != PHASE_COLLECTING:
            raise DomainError("Сбор карточек сейчас не идёт.")
        if game.collection_deadline_at and self._is_due(game.collection_deadline_at):
            raise DomainError("Срок сбора карточек уже истёк.")
        return game

    async def _member_for_user(
        self, session: AsyncSession, room_id: int, user_id: int
    ) -> RoomMember | None:
        return await session.scalar(
            select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
        )

    async def _require_active_member(
        self, session: AsyncSession, room_id: int, user_id: int
    ) -> RoomMember:
        member = await self._member_for_user(session, room_id, user_id)
        if member is None or member.status != "active":
            raise DomainError("Зрители и вышедшие игроки не могут управлять партией.")
        return member

    async def _active_members(self, session: AsyncSession, room_id: int) -> list[RoomMember]:
        return list(
            await session.scalars(
                select(RoomMember)
                .where(RoomMember.room_id == room_id, RoomMember.status == "active")
                .order_by(RoomMember.joined_at, RoomMember.id)
            )
        )

    async def _active_seats(self, session: AsyncSession, room_id: int) -> list[HatSeat]:
        return list(
            await session.scalars(
                select(HatSeat)
                .join(RoomMember, RoomMember.id == HatSeat.member_id)
                .where(HatSeat.room_id == room_id, RoomMember.status == "active")
                .order_by(HatSeat.seat_number)
            )
        )

    async def _require_complete_seats(self, session: AsyncSession, room_id: int) -> None:
        members = await self._active_members(session, room_id)
        if len(members) < MIN_PLAYERS:
            raise DomainError("Для игры нужно как минимум три игрока.")
        seats = list(
            await session.scalars(
                select(HatSeat).where(HatSeat.room_id == room_id).order_by(HatSeat.seat_number)
            )
        )
        if {seat.member_id for seat in seats} != {member.id for member in members}:
            raise DomainError("Составьте полный порядок игроков в круге.")
        if [seat.seat_number for seat in seats] != list(range(1, len(seats) + 1)):
            raise DomainError("Порядок игроков заполнен некорректно.")

    async def _require_minimum_active(self, session: AsyncSession, room: Room) -> None:
        if len(await self._active_members(session, room.id)) < MIN_PLAYERS:
            raise DomainError("Для продолжения нужно как минимум три активных игрока.")

    def _validate_word(self, value: str) -> tuple[str, str]:
        text = clean_card(value)
        if not text:
            raise DomainError("Карточка не может быть пустой.")
        if len(text) > MAX_WORD_LENGTH:
            raise DomainError(f"Карточка не должна быть длиннее {MAX_WORD_LENGTH} символов.")
        return text, normalize_card(text)

    async def _complete_collection_if_ready(
        self, session: AsyncSession, room: Room, game: HatGame
    ) -> bool:
        members = await self._active_members(session, room.id)
        if len(members) < MIN_PLAYERS:
            return False
        counts = dict(
            (
                await session.execute(
                    select(HatWord.author_member_id, func.count(HatWord.id))
                    .where(HatWord.room_id == room.id)
                    .group_by(HatWord.author_member_id)
                )
            ).all()
        )
        if any(int(counts.get(member.id, 0)) < game.words_per_player for member in members):
            return False
        game.phase = PHASE_ROUND_BREAK
        game.collection_deadline_at = None
        game.round_number = 1
        game.next_explainer_seat = None
        room.status = "playing"
        room.started_at = room.started_at or self._now()
        return True

    async def _create_ready_turn(
        self, session: AsyncSession, room: Room, game: HatGame
    ) -> HatTurn | None:
        seats = await self._active_seats(session, room.id)
        if len(seats) < MIN_PLAYERS:
            await self._finish(session, room, game, "insufficient_players")
            return None
        by_number = {seat.seat_number: seat for seat in seats}
        seat_numbers = list(by_number)
        if game.next_explainer_seat is None:
            explainer_number = seat_numbers[0]
        elif game.next_explainer_seat in by_number:
            explainer_number = game.next_explainer_seat
        else:
            explainer_number = next_seat_number(seat_numbers, game.next_explainer_seat)
        guesser_number = next_seat_number(seat_numbers, explainer_number)
        turn = HatTurn(
            room_id=room.id,
            round_number=game.round_number,
            explainer_member_id=by_number[explainer_number].member_id,
            guesser_member_id=by_number[guesser_number].member_id,
            status=TURN_READY,
        )
        session.add(turn)
        game.next_explainer_seat = guesser_number
        game.phase = PHASE_READY
        return turn

    async def _current_turn(self, session: AsyncSession, room_id: int) -> HatTurn | None:
        return await session.scalar(
            select(HatTurn)
            .where(
                HatTurn.room_id == room_id,
                HatTurn.status.in_([TURN_READY, TURN_COUNTDOWN, TURN_ACTIVE]),
            )
            .order_by(HatTurn.id.desc())
            .limit(1)
        )

    async def _require_active_turn(
        self, session: AsyncSession, room: Room, user_id: int
    ) -> tuple[HatGame, HatTurn, HatRoundCard]:
        game = await self._require_hat_game(session, room)
        if game.phase != PHASE_TURN:
            raise DomainError("Сейчас нет активного хода.")
        turn = await self._current_turn(session, room.id)
        if turn is None or turn.status != TURN_ACTIVE:
            raise DomainError("Текущий ход уже завершён.")
        actor = await self._require_active_member(session, room.id, user_id)
        if actor.id != turn.explainer_member_id:
            raise DomainError("Эта кнопка доступна только объясняющему игроку.")
        card = await session.scalar(
            select(HatRoundCard).where(
                HatRoundCard.room_id == room.id,
                HatRoundCard.round_number == game.round_number,
                HatRoundCard.state == CARD_CURRENT,
                HatRoundCard.turn_id == turn.id,
            )
        )
        if card is None:
            raise RuntimeError("Active Hat turn has no card")
        return game, turn, card

    async def _activate_turn(
        self, session: AsyncSession, room: Room, game: HatGame, turn: HatTurn
    ) -> None:
        if len(await self._active_members(session, room.id)) < MIN_PLAYERS:
            await self._finish(session, room, game, "insufficient_players")
            return
        turn.status = TURN_ACTIVE
        turn.countdown_deadline_at = None
        turn.turn_deadline_at = self._now() + timedelta(
            seconds=game.round_seconds[game.round_number - 1]
        )
        game.phase = PHASE_TURN
        await self._draw_or_finish_round(session, room, game, turn)

    async def _draw_or_finish_round(
        self, session: AsyncSession, room: Room, game: HatGame, turn: HatTurn
    ) -> None:
        cards = list(
            await session.scalars(
                select(HatRoundCard).where(
                    HatRoundCard.room_id == room.id,
                    HatRoundCard.round_number == game.round_number,
                    HatRoundCard.state == CARD_AVAILABLE,
                )
            )
        )
        if cards:
            card = self._random.choice(cards)
            card.state = CARD_CURRENT
            card.turn_id = turn.id
            card.explainer_member_id = turn.explainer_member_id
            card.guesser_member_id = turn.guesser_member_id
            return
        turn.status = TURN_ENDED
        turn.ended_at = self._now()
        turn.end_reason = "deck_empty"
        if game.round_number >= 3:
            game.phase = PHASE_FINAL_BREAK
        else:
            game.phase = PHASE_ROUND_BREAK

    async def _timeout_turn(
        self, session: AsyncSession, room: Room, game: HatGame, turn: HatTurn
    ) -> None:
        card = await session.scalar(
            select(HatRoundCard).where(
                HatRoundCard.room_id == room.id,
                HatRoundCard.round_number == game.round_number,
                HatRoundCard.state == CARD_CURRENT,
                HatRoundCard.turn_id == turn.id,
            )
        )
        if card is not None:
            card.state = CARD_AVAILABLE
        turn.status = TURN_ENDED
        turn.ended_at = self._now()
        turn.end_reason = "timeout"
        await self._create_ready_turn(session, room, game)

    async def _open_appeal(self, session: AsyncSession, room_id: int) -> HatAppeal | None:
        return await session.scalar(
            select(HatAppeal)
            .where(HatAppeal.room_id == room_id, HatAppeal.status == "open")
            .order_by(HatAppeal.id.desc())
            .limit(1)
        )

    async def _open_last_word_claim(
        self, session: AsyncSession, room_id: int
    ) -> HatLastWordClaim | None:
        return await session.scalar(
            select(HatLastWordClaim)
            .where(HatLastWordClaim.room_id == room_id, HatLastWordClaim.status == "open")
            .order_by(HatLastWordClaim.id.desc())
            .limit(1)
        )

    async def _last_word_claim_candidate(
        self, session: AsyncSession, room_id: int, round_number: int
    ) -> tuple[HatTurn, HatRoundCard] | None:
        """Return the unguessed card returned by the immediately prior timed-out turn."""
        turn = await session.scalar(
            select(HatTurn)
            .where(
                HatTurn.room_id == room_id,
                HatTurn.round_number == round_number,
                HatTurn.status == TURN_ENDED,
                HatTurn.end_reason == "timeout",
            )
            .order_by(HatTurn.id.desc())
            .limit(1)
        )
        if turn is None:
            return None
        card = await session.scalar(
            select(HatRoundCard).where(
                HatRoundCard.room_id == room_id,
                HatRoundCard.round_number == round_number,
                HatRoundCard.turn_id == turn.id,
                HatRoundCard.state == CARD_AVAILABLE,
            )
        )
        if card is None:
            return None
        claimed = await session.scalar(
            select(HatLastWordClaim.id).where(HatLastWordClaim.round_card_id == card.id)
        )
        if claimed is not None:
            return None
        return turn, card

    async def _latest_appealable_turn(
        self, session: AsyncSession, room_id: int, round_number: int
    ) -> HatTurn | None:
        """The immediately preceding completed turn; older turns are no longer appealable."""
        return await session.scalar(
            select(HatTurn)
            .where(
                HatTurn.room_id == room_id,
                HatTurn.round_number == round_number,
                HatTurn.status == TURN_ENDED,
                HatTurn.end_reason.in_(("timeout", "deck_empty")),
            )
            .order_by(HatTurn.id.desc())
            .limit(1)
        )

    async def _current_appeal_eligible(
        self, session: AsyncSession, room_id: int, member_ids: list[int]
    ) -> list[RoomMember]:
        if not member_ids:
            return []
        return list(
            await session.scalars(
                select(RoomMember)
                .where(
                    RoomMember.room_id == room_id,
                    RoomMember.id.in_(member_ids),
                    RoomMember.status == "active",
                )
                .order_by(RoomMember.id)
            )
        )

    async def _current_last_word_claim_eligible(
        self, session: AsyncSession, room_id: int, claim: HatLastWordClaim
    ) -> list[int]:
        """Keep automatic votes, while departed ordinary voters no longer block the vote."""
        active_ids = {member.id for member in await self._active_members(session, room_id)}
        automatic_ids = set(claim.automatic_yes_member_ids)
        return [
            int(member_id)
            for member_id in claim.eligible_member_ids
            if member_id in active_ids or member_id in automatic_ids
        ]

    async def _resolve_appeal_if_ready(
        self, session: AsyncSession, room: Room, game: HatGame, appeal: HatAppeal
    ) -> HatAppealOutcome | None:
        eligible = await self._current_appeal_eligible(session, room.id, appeal.eligible_member_ids)
        appeal.eligible_member_ids = [member.id for member in eligible]
        eligible_ids = set(appeal.eligible_member_ids)
        votes = list(
            await session.scalars(select(HatAppealVote).where(HatAppealVote.appeal_id == appeal.id))
        )
        yes_count = sum(vote.choice for vote in votes if vote.member_id in eligible_ids)
        cast_count = sum(1 for vote in votes if vote.member_id in eligible_ids)
        resolution = resolve_strict_vote(
            eligible_count=len(eligible_ids), yes_count=yes_count, cast_count=cast_count
        )
        if not resolution.is_resolved:
            return None
        card = await session.get(HatRoundCard, appeal.round_card_id)
        if card is None:
            raise RuntimeError("Appeal card is missing")
        word = await session.get(HatWord, card.word_id)
        members = {member.id: member for member in await self._members_with_users(session, room.id)}
        appeal.status = "closed"
        appeal.accepted = bool(resolution.accepted)
        appeal.closed_at = self._now()
        if resolution.accepted:
            card.score_counted = False
        appeal_turn = await session.get(HatTurn, card.turn_id)
        if appeal_turn is None:
            raise RuntimeError("Appeal turn is missing")
        if appeal_turn.end_reason == "deck_empty":
            game.phase = PHASE_FINAL_BREAK if game.round_number >= 3 else PHASE_ROUND_BREAK
        else:
            game.phase = PHASE_READY
        return HatAppealOutcome(
            accepted=bool(resolution.accepted),
            card_text=word.text if word else "карточка",
            explainer_name=(
                members[card.explainer_member_id].display_name
                if card.explainer_member_id in members
                else "Игрок"
            ),
            guesser_name=(
                members[card.guesser_member_id].display_name
                if card.guesser_member_id in members
                else "Игрок"
            ),
        )

    async def _resolve_last_word_claim_if_ready(
        self,
        session: AsyncSession,
        room: Room,
        game: HatGame,
        claim: HatLastWordClaim,
    ) -> HatLastWordClaimOutcome | None:
        eligible = await self._current_last_word_claim_eligible(session, room.id, claim)
        claim.eligible_member_ids = eligible
        eligible_ids = set(eligible)
        votes = list(
            await session.scalars(
                select(HatLastWordClaimVote).where(HatLastWordClaimVote.claim_id == claim.id)
            )
        )
        yes_count = sum(vote.choice for vote in votes if vote.member_id in eligible_ids)
        cast_count = sum(1 for vote in votes if vote.member_id in eligible_ids)
        resolution = resolve_strict_vote(
            eligible_count=len(eligible_ids), yes_count=yes_count, cast_count=cast_count
        )
        if not resolution.is_resolved:
            return None
        card = await session.get(HatRoundCard, claim.round_card_id)
        if card is None:
            raise RuntimeError("Last-word card is missing")
        word = await session.get(HatWord, card.word_id)
        members = {member.id: member for member in await self._members_with_users(session, room.id)}
        claim.status = "closed"
        claim.accepted = bool(resolution.accepted)
        claim.closed_at = self._now()
        if resolution.accepted:
            card.state = CARD_SCORED
            card.score_counted = True
        game.phase = PHASE_READY
        return HatLastWordClaimOutcome(
            accepted=bool(resolution.accepted),
            card_text=word.text if word else "карточка",
            explainer_name=(
                members[card.explainer_member_id].display_name
                if card.explainer_member_id in members
                else "Игрок"
            ),
            guesser_name=(
                members[card.guesser_member_id].display_name
                if card.guesser_member_id in members
                else "Игрок"
            ),
        )

    async def _handle_departure(
        self, session: AsyncSession, room: Room, game: HatGame, member_id: int
    ) -> None:
        if len(await self._active_members(session, room.id)) < MIN_PLAYERS:
            await self._finish(session, room, game, "insufficient_players")
            return
        appeal = await self._open_appeal(session, room.id)
        if appeal is not None:
            await self._resolve_appeal_if_ready(session, room, game, appeal)
            if appeal.status == "open":
                return
        claim = await self._open_last_word_claim(session, room.id)
        if claim is not None:
            await self._resolve_last_word_claim_if_ready(session, room, game, claim)
            if claim.status == "open":
                return
        turn = await self._current_turn(session, room.id)
        if turn is None or member_id not in {turn.explainer_member_id, turn.guesser_member_id}:
            return
        card = await session.scalar(
            select(HatRoundCard).where(
                HatRoundCard.room_id == room.id,
                HatRoundCard.state == CARD_CURRENT,
                HatRoundCard.turn_id == turn.id,
            )
        )
        if card is not None:
            card.state = CARD_AVAILABLE
            card.turn_id = None
            card.explainer_member_id = None
            card.guesser_member_id = None
        turn.status = TURN_CANCELLED
        turn.ended_at = self._now()
        turn.end_reason = "player_left"
        await self._create_ready_turn(session, room, game)

    async def _expire_collection(self, session: AsyncSession, room: Room, game: HatGame) -> None:
        members = await self._active_members(session, room.id)
        counts = dict(
            (
                await session.execute(
                    select(HatWord.author_member_id, func.count(HatWord.id))
                    .where(HatWord.room_id == room.id)
                    .group_by(HatWord.author_member_id)
                )
            ).all()
        )
        incomplete = [
            member for member in members if int(counts.get(member.id, 0)) < game.words_per_player
        ]
        for member in incomplete:
            member.status = "left"
            member.left_at = self._now()
            await session.execute(delete(HatWord).where(HatWord.author_member_id == member.id))
        await self._transfer_host_if_needed(session, room)
        if len(await self._active_members(session, room.id)) < MIN_PLAYERS:
            await session.execute(delete(HatWord).where(HatWord.room_id == room.id))
            await session.execute(delete(HatSeat).where(HatSeat.room_id == room.id))
            game.phase = PHASE_CONFIGURED
            game.collection_deadline_at = None
            game.round_number = 0
            game.next_explainer_seat = None
            room.status = "lobby"
            room.is_join_open = True
            return
        await self._complete_collection_if_ready(session, room, game)

    async def _transfer_host_if_needed(self, session: AsyncSession, room: Room) -> None:
        current = await self._member_for_user(session, room.id, room.host_user_id)
        if current is not None and current.status == "active":
            return
        members = await self._active_members(session, room.id)
        if members:
            room.host_user_id = members[0].user_id

    async def _members_with_users(self, session: AsyncSession, room_id: int) -> list[MemberView]:
        rows = await session.execute(
            select(RoomMember, User)
            .join(User, User.telegram_id == RoomMember.user_id)
            .where(RoomMember.room_id == room_id)
            .order_by(RoomMember.joined_at, RoomMember.id)
        )
        return [
            MemberView(
                id=member.id,
                user_id=member.user_id,
                display_name=user.display_name,
                username=user.username,
                status=member.status,
                role=None,
                joined_at=member.joined_at,
            )
            for member, user in rows
        ]

    def _turn_view(
        self, turn: HatTurn | None, members: dict[int, MemberView]
    ) -> HatTurnView | None:
        if turn is None:
            return None
        deadline = (
            turn.countdown_deadline_at
            if turn.status == TURN_COUNTDOWN
            else turn.turn_deadline_at
            if turn.status == TURN_ACTIVE
            else None
        )
        return HatTurnView(
            id=turn.id,
            round_number=turn.round_number,
            status=turn.status,
            explainer_member_id=turn.explainer_member_id,
            guesser_member_id=turn.guesser_member_id,
            explainer_name=(
                members[turn.explainer_member_id].display_name
                if turn.explainer_member_id in members
                else "Игрок"
            ),
            guesser_name=(
                members[turn.guesser_member_id].display_name
                if turn.guesser_member_id in members
                else "Игрок"
            ),
            explainer_ready=turn.explainer_ready,
            guesser_ready=turn.guesser_ready,
            seconds_remaining=self._seconds_remaining(deadline),
        )

    async def _current_card_text(self, session: AsyncSession, room_id: int) -> str | None:
        return await session.scalar(
            select(HatWord.text)
            .join(HatRoundCard, HatRoundCard.word_id == HatWord.id)
            .where(HatRoundCard.room_id == room_id, HatRoundCard.state == CARD_CURRENT)
            .order_by(HatRoundCard.id.desc())
            .limit(1)
        )

    async def _appeal_view(
        self,
        session: AsyncSession,
        room_id: int,
        actor_member_id: int | None,
        members: dict[int, MemberView],
    ) -> HatAppealView | None:
        appeal = await self._open_appeal(session, room_id)
        if appeal is None:
            return None
        card = await session.get(HatRoundCard, appeal.round_card_id)
        if card is None:
            return None
        word = await session.get(HatWord, card.word_id)
        eligible = tuple(int(member_id) for member_id in appeal.eligible_member_ids)
        eligible_set = set(eligible)
        votes = list(
            await session.scalars(select(HatAppealVote).where(HatAppealVote.appeal_id == appeal.id))
        )
        return HatAppealView(
            id=appeal.id,
            card_id=card.id,
            card_text=word.text if word else "карточка",
            explainer_name=(
                members[card.explainer_member_id].display_name
                if card.explainer_member_id in members
                else "Игрок"
            ),
            guesser_name=(
                members[card.guesser_member_id].display_name
                if card.guesser_member_id in members
                else "Игрок"
            ),
            eligible_member_ids=eligible,
            yes_count=sum(vote.choice for vote in votes if vote.member_id in eligible_set),
            no_count=sum(not vote.choice for vote in votes if vote.member_id in eligible_set),
            actor_choice=next(
                (vote.choice for vote in votes if vote.member_id == actor_member_id), None
            ),
        )

    async def _last_word_claim_view(
        self,
        session: AsyncSession,
        room_id: int,
        actor_member_id: int | None,
        members: dict[int, MemberView],
    ) -> HatLastWordClaimView | None:
        claim = await self._open_last_word_claim(session, room_id)
        if claim is None:
            return None
        card = await session.get(HatRoundCard, claim.round_card_id)
        if card is None:
            return None
        word = await session.get(HatWord, card.word_id)
        eligible = tuple(int(member_id) for member_id in claim.eligible_member_ids)
        automatic_yes = tuple(int(member_id) for member_id in claim.automatic_yes_member_ids)
        eligible_set = set(eligible)
        votes = list(
            await session.scalars(
                select(HatLastWordClaimVote).where(HatLastWordClaimVote.claim_id == claim.id)
            )
        )
        return HatLastWordClaimView(
            id=claim.id,
            card_id=card.id,
            card_text=word.text if word else "карточка",
            explainer_name=(
                members[card.explainer_member_id].display_name
                if card.explainer_member_id in members
                else "Игрок"
            ),
            guesser_name=(
                members[card.guesser_member_id].display_name
                if card.guesser_member_id in members
                else "Игрок"
            ),
            eligible_member_ids=eligible,
            automatic_yes_member_ids=automatic_yes,
            yes_count=sum(vote.choice for vote in votes if vote.member_id in eligible_set),
            no_count=sum(not vote.choice for vote in votes if vote.member_id in eligible_set),
            actor_choice=next(
                (vote.choice for vote in votes if vote.member_id == actor_member_id), None
            ),
        )

    async def _scores(
        self, session: AsyncSession, room_id: int, members: list[MemberView]
    ) -> tuple[HatScoreView, ...]:
        scores = {member.id: 0 for member in members}
        cards = list(
            await session.scalars(
                select(HatRoundCard).where(
                    HatRoundCard.room_id == room_id,
                    HatRoundCard.state == CARD_SCORED,
                    HatRoundCard.score_counted.is_(True),
                )
            )
        )
        for card in cards:
            if card.explainer_member_id is not None:
                scores[card.explainer_member_id] = scores.get(card.explainer_member_id, 0) + 1
            if card.guesser_member_id is not None:
                scores[card.guesser_member_id] = scores.get(card.guesser_member_id, 0) + 1
        return tuple(
            HatScoreView(
                member_id=member.id,
                display_name=member.display_name,
                score=scores.get(member.id, 0),
                is_active=member.status == "active",
            )
            for member in sorted(
                members,
                key=lambda member: (
                    -scores.get(member.id, 0),
                    member.display_name.casefold(),
                    member.id,
                ),
            )
        )

    async def _finish(self, session: AsyncSession, room: Room, game: HatGame, result: str) -> None:
        game.phase = PHASE_FINISHED
        game.result = result
        game.collection_deadline_at = None
        room.status = "finished"
        room.finished_at = self._now()
        turn = await self._current_turn(session, room.id)
        if turn is not None:
            turn.status = TURN_CANCELLED
            turn.ended_at = self._now()
            turn.end_reason = result
        appeal = await self._open_appeal(session, room.id)
        if appeal is not None:
            appeal.status = "closed"
            appeal.accepted = False
            appeal.closed_at = self._now()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _seconds_remaining(self, deadline: datetime | None) -> int | None:
        if deadline is None:
            return None
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return max(0, ceil((deadline - self._now()).total_seconds()))

    def _is_due(self, deadline: datetime) -> bool:
        return self._seconds_remaining(deadline) == 0

    @staticmethod
    def _bump(room: Room) -> None:
        room.revision += 1
