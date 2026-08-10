from __future__ import annotations

import secrets
from datetime import datetime, timezone
from random import SystemRandom

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction

from app.domain.errors import DomainError
from app.domain.games.spy import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    ROLE_CIVILIAN,
    ROLE_SPY,
    VOTE_EXILE,
    VOTE_REVEAL,
    resolve_vote,
)
from app.services.locks import RoomLocks
from app.services.views import (
    MemberView,
    OpenRoomSummary,
    RoomView,
    SpyGameView,
    ThemeOption,
    VoteOutcome,
    VoteView,
)
from app.storage.database import Database
from app.storage.models import (
    CatalogLocation,
    CatalogTheme,
    HatGame,
    Room,
    RoomMember,
    SpyGame,
    User,
    Vote,
    VoteChoice,
)


class RoomService:
    def __init__(self, database: Database, locks: RoomLocks) -> None:
        self._database = database
        self._locks = locks
        self._random = SystemRandom()

    async def ensure_user(self, user_id: int, display_name: str, username: str | None) -> None:
        async with self._database.session() as session, session.begin():
            user = await session.get(User, user_id)
            if user is None:
                session.add(User(telegram_id=user_id, display_name=display_name, username=username))
            else:
                user.display_name = display_name
                user.username = username
            members = await session.scalars(
                select(RoomMember).where(
                    RoomMember.user_id == user_id, RoomMember.is_reachable.is_(False)
                )
            )
            for member in members:
                member.is_reachable = True

    async def create_spy_room(self, host_user_id: int) -> int:
        async with self._database.session() as session, session.begin():
            theme_ids = list(
                await session.scalars(
                    select(CatalogTheme.id).where(CatalogTheme.is_active.is_(True))
                )
            )
            room = Room(
                game_type="spy",
                status="lobby",
                join_code=await self._new_join_code(session),
                join_token=secrets.token_urlsafe(18),
                host_user_id=host_user_id,
                is_join_open=True,
                settings={
                    "spy_count_mode": "random",
                    "spy_count": 1,
                    "spies_know_each_other": False,
                    "theme_ids": theme_ids,
                },
            )
            session.add(room)
            await session.flush()
            session.add(RoomMember(room_id=room.id, user_id=host_user_id, status="active"))
            return room.id

    async def create_hat_room(self, host_user_id: int) -> int:
        async with self._database.session() as session, session.begin():
            room = Room(
                game_type="hat",
                status="lobby",
                join_code=await self._new_join_code(session),
                join_token=secrets.token_urlsafe(18),
                host_user_id=host_user_id,
                is_join_open=True,
                settings={},
            )
            session.add(room)
            await session.flush()
            session.add(RoomMember(room_id=room.id, user_id=host_user_id, status="active"))
            session.add(HatGame(room_id=room.id))
            return room.id

    async def join_by_token(self, token: str, user_id: int) -> int:
        return await self._join_by_field(Room.join_token, token, user_id)

    async def join_by_code(self, code: str, user_id: int) -> int:
        return await self._join_by_field(Room.join_code, code.strip().upper(), user_id)

    async def _join_by_field(self, field: object, value: str, user_id: int) -> int:
        # Close the lookup transaction before waiting for a per-room write lock.
        # Otherwise a SQLite reader could block the writer it is waiting on.
        async with self._database.session() as session:
            room_id = await session.scalar(select(Room.id).where(field == value))  # type: ignore[arg-type]
        if room_id is None:
            raise DomainError("Комната с таким кодом не найдена.")
        async with self._room_transaction(room_id) as (session, room):
            if room.status != "lobby":
                raise DomainError("Игра в этой комнате уже началась или завершилась.")
            member = await session.scalar(
                select(RoomMember).where(
                    RoomMember.room_id == room.id, RoomMember.user_id == user_id
                )
            )
            if member is not None:
                if member.status == "active":
                    return room.id
                if not room.is_join_open:
                    raise DomainError("Хост закрыл вход в эту комнату.")
                member.status = "active"
                member.left_at = None
                member.is_reachable = True
                await self._transfer_host_if_needed(session, room)
                self._bump(room)
                return room.id
            if not room.is_join_open:
                raise DomainError("Хост закрыл вход в эту комнату.")
            active_count = await self._active_member_count(session, room.id)
            if active_count >= MAX_PLAYERS:
                raise DomainError("В комнате уже максимальное число игроков — 40.")
            session.add(RoomMember(room_id=room.id, user_id=user_id, status="active"))
            self._bump(room)
            return room.id

    async def set_join_open(self, room_id: int, actor_user_id: int, is_open: bool) -> None:
        async with self._room_transaction(room_id) as (session, room):
            self._require_lobby_host(room, actor_user_id)
            room.is_join_open = is_open
            self._bump(room)

    async def set_spy_count_mode(self, room_id: int, actor_user_id: int, mode: str) -> None:
        if mode not in {"random", "exact"}:
            raise DomainError("Неизвестный режим числа шпионов.")
        async with self._room_transaction(room_id) as (session, room):
            self._require_lobby_host(room, actor_user_id)
            settings = dict(room.settings)
            settings["spy_count_mode"] = mode
            room.settings = settings
            self._bump(room)

    async def set_exact_spy_count(self, room_id: int, actor_user_id: int, count: int) -> None:
        async with self._room_transaction(room_id) as (session, room):
            self._require_lobby_host(room, actor_user_id)
            players = await self._active_member_count(session, room.id)
            if not 1 <= count <= players:
                raise DomainError(f"Число шпионов должно быть от 1 до {players}.")
            settings = dict(room.settings)
            settings["spy_count_mode"] = "exact"
            settings["spy_count"] = count
            room.settings = settings
            self._bump(room)

    async def set_spies_know_each_other(
        self, room_id: int, actor_user_id: int, enabled: bool
    ) -> None:
        async with self._room_transaction(room_id) as (_, room):
            self._require_lobby_host(room, actor_user_id)
            settings = dict(room.settings)
            settings["spies_know_each_other"] = enabled
            room.settings = settings
            self._bump(room)

    async def toggle_theme(self, room_id: int, actor_user_id: int, theme_id: int) -> None:
        async with self._room_transaction(room_id) as (session, room):
            self._require_lobby_host(room, actor_user_id)
            theme = await session.get(CatalogTheme, theme_id)
            if theme is None or not theme.is_active:
                raise DomainError("Эта тема больше недоступна в каталоге.")
            settings = dict(room.settings)
            enabled = {int(item) for item in settings.get("theme_ids", [])}
            if theme_id in enabled:
                enabled.remove(theme_id)
            else:
                enabled.add(theme_id)
            settings["theme_ids"] = sorted(enabled)
            room.settings = settings
            self._bump(room)

    async def leave_room(
        self, room_id: int, actor_user_id: int, *, technical: bool = False
    ) -> None:
        async with self._room_transaction(room_id) as (session, room):
            member = await self._member_for_user(session, room.id, actor_user_id)
            if member is None or member.status != "active":
                raise DomainError("Вы уже не участвуете в этой комнате.")
            member.status = "left"
            member.left_at = datetime.now(timezone.utc)
            if technical:
                member.is_reachable = False
            await self._transfer_host_if_needed(session, room)
            if room.status == "playing":
                await self._prune_and_resolve_open_vote(session, room, member.id)
                await self._maybe_finish_civilians(session, room)
            elif room.status == "lobby" and await self._active_member_count(session, room.id) == 0:
                room.status = "cancelled"
                room.finished_at = datetime.now(timezone.utc)
            self._bump(room)

    async def remove_lobby_member(
        self, room_id: int, actor_user_id: int, target_member_id: int
    ) -> None:
        async with self._room_transaction(room_id) as (session, room):
            self._require_lobby_host(room, actor_user_id)
            target = await session.get(RoomMember, target_member_id)
            if target is None or target.room_id != room.id or target.status != "active":
                raise DomainError("Этот участник уже не находится в лобби.")
            if target.user_id == actor_user_id:
                raise DomainError("Хост не может исключить самого себя.")
            target.status = "kicked"
            target.left_at = datetime.now(timezone.utc)
            await self._transfer_host_if_needed(session, room)
            self._bump(room)

    async def start_spy_game(self, room_id: int, actor_user_id: int) -> None:
        async with self._room_transaction(room_id) as (session, room):
            self._require_lobby_host(room, actor_user_id)
            members = await self._active_members(session, room.id)
            if len(members) < MIN_PLAYERS:
                raise DomainError("Для старта нужно как минимум три игрока.")
            settings = room.settings
            theme_ids = [int(item) for item in settings.get("theme_ids", [])]
            if not theme_ids:
                raise DomainError("Включите хотя бы одну тему с локациями.")
            themes = list(
                await session.scalars(
                    select(CatalogTheme)
                    .where(CatalogTheme.id.in_(theme_ids), CatalogTheme.is_active.is_(True))
                    .order_by(CatalogTheme.id)
                )
            )
            candidates: list[tuple[CatalogTheme, list[CatalogLocation]]] = []
            for theme in themes:
                locations = list(
                    await session.scalars(
                        select(CatalogLocation)
                        .where(
                            CatalogLocation.theme_id == theme.id,
                            CatalogLocation.is_active.is_(True),
                        )
                        .order_by(CatalogLocation.id)
                    )
                )
                if locations:
                    candidates.append((theme, locations))
            if not candidates:
                raise DomainError("Среди включённых тем нет активных локаций.")
            theme, locations = self._random.choice(candidates)
            location = self._random.choice(locations)
            mode = str(settings.get("spy_count_mode", "random"))
            if mode == "random":
                spy_count = self._random.randint(1, len(members))
            else:
                spy_count = int(settings.get("spy_count", 1))
                if not 1 <= spy_count <= len(members):
                    raise DomainError("Выбранное число шпионов не подходит к текущему составу.")
            spy_member_ids = {member.id for member in self._random.sample(members, spy_count)}
            for member in members:
                member.role = ROLE_SPY if member.id in spy_member_ids else ROLE_CIVILIAN
            session.add(
                SpyGame(
                    room_id=room.id,
                    theme_id=theme.id,
                    theme_name=theme.name,
                    location_name=location.name,
                    spy_count=spy_count,
                    spies_know_each_other=bool(settings.get("spies_know_each_other", False)),
                )
            )
            room.status = "playing"
            room.started_at = datetime.now(timezone.utc)
            self._bump(room)

    async def propose_exile(self, room_id: int, actor_user_id: int, target_member_id: int) -> None:
        async with self._room_transaction(room_id) as (session, room):
            actor = await self._require_active_playing_member(session, room, actor_user_id)
            if await self._open_vote(session, room.id) is not None:
                raise DomainError("Сначала завершите текущее голосование.")
            target = await session.get(RoomMember, target_member_id)
            if target is None or target.room_id != room.id or target.status != "active":
                raise DomainError("Этого игрока уже нельзя выдвинуть.")
            if target.id == actor.id:
                raise DomainError("Нельзя выдвинуть на изгнание самого себя.")
            eligible = [member.id for member in await self._active_members(session, room.id)]
            vote = Vote(
                room_id=room.id,
                kind=VOTE_EXILE,
                subject_member_id=target.id,
                eligible_member_ids=eligible,
                status="open",
            )
            session.add(vote)
            await session.flush()
            session.add_all(
                [
                    VoteChoice(vote_id=vote.id, member_id=actor.id, choice=True),
                    VoteChoice(vote_id=vote.id, member_id=target.id, choice=False),
                ]
            )
            self._bump(room)

    async def begin_reveal(self, room_id: int, actor_user_id: int) -> None:
        async with self._room_transaction(room_id) as (session, room):
            actor = await self._require_active_playing_member(session, room, actor_user_id)
            if actor.role != ROLE_SPY:
                raise DomainError("Вскрыться может только шпион.")
            if await self._open_vote(session, room.id) is not None:
                raise DomainError("Сначала завершите текущее голосование.")
            session.add(
                Vote(
                    room_id=room.id,
                    kind=VOTE_REVEAL,
                    subject_member_id=actor.id,
                    eligible_member_ids=[
                        member.id for member in await self._active_members(session, room.id)
                    ],
                    status="open",
                )
            )
            self._bump(room)

    async def cast_vote(
        self, room_id: int, actor_user_id: int, vote_id: int, choice: bool
    ) -> VoteOutcome | None:
        async with self._room_transaction(room_id) as (session, room):
            actor = await self._require_active_playing_member(session, room, actor_user_id)
            vote = await session.get(Vote, vote_id)
            if vote is None or vote.room_id != room.id or vote.status != "open":
                raise DomainError("Это голосование уже закрыто.")
            eligible = await self._current_eligible_members(
                session, room.id, vote.eligible_member_ids
            )
            vote.eligible_member_ids = [member.id for member in eligible]
            if actor.id not in vote.eligible_member_ids:
                raise DomainError("Вы не участвуете в этом голосовании.")
            prior_choice = await session.scalar(
                select(VoteChoice).where(
                    VoteChoice.vote_id == vote.id, VoteChoice.member_id == actor.id
                )
            )
            if prior_choice is not None:
                raise DomainError("Ваш голос уже учтён.")
            session.add(VoteChoice(vote_id=vote.id, member_id=actor.id, choice=choice))
            await session.flush()
            outcome = await self._resolve_open_vote_if_ready(session, room, vote)
            self._bump(room)
            return outcome

    async def emergency_cancel(self, room_id: int, actor_user_id: int) -> None:
        async with self._room_transaction(room_id) as (session, room):
            if room.status != "playing" or room.host_user_id != actor_user_id:
                raise DomainError("Аварийно завершить игру может только текущий хост.")
            game = await session.get(SpyGame, room.id)
            if game is None:
                raise DomainError("Данные этой партии не найдены.")
            game.result = "cancelled"
            room.status = "finished"
            room.finished_at = datetime.now(timezone.utc)
            open_vote = await self._open_vote(session, room.id)
            if open_vote is not None:
                open_vote.status = "cancelled"
                open_vote.closed_at = datetime.now(timezone.utc)
            self._bump(room)

    async def mark_delivery_failure(self, room_id: int, user_id: int) -> None:
        """Exclude a player only when the Telegram API proves delivery failed."""
        try:
            await self.leave_room(room_id, user_id, technical=True)
        except DomainError:
            return

    async def get_room_view(self, room_id: int, actor_user_id: int) -> RoomView:
        async with self._database.session() as session:
            room = await session.get(Room, room_id)
            if room is None:
                raise DomainError("Комната не найдена.")
            members = await self._members_with_users(session, room.id)
            actor = next((member for member in members if member.user_id == actor_user_id), None)
            theme_ids = {int(item) for item in room.settings.get("theme_ids", [])}
            theme_rows = list(
                await session.scalars(
                    select(CatalogTheme).order_by(CatalogTheme.name.collate("NOCASE"))
                )
            )
            active_location_theme_ids = set(
                await session.scalars(
                    select(CatalogLocation.theme_id)
                    .where(CatalogLocation.is_active.is_(True))
                    .distinct()
                )
            )
            themes = tuple(
                ThemeOption(
                    id=theme.id,
                    name=theme.name,
                    is_selected=theme.id in theme_ids,
                    has_active_locations=theme.is_active and theme.id in active_location_theme_ids,
                )
                for theme in theme_rows
                if theme.is_active
            )
            spy_game = await session.get(SpyGame, room.id)
            game = (
                SpyGameView(
                    theme_name=spy_game.theme_name,
                    location_name=spy_game.location_name,
                    spy_count=spy_game.spy_count,
                    spies_know_each_other=spy_game.spies_know_each_other,
                    result=spy_game.result,
                )
                if spy_game
                else None
            )
            vote = await self._open_vote(session, room.id)
            vote_view = await self._vote_view(session, vote, actor.id if actor else None)
            known_spies: tuple[str, ...] = ()
            if actor and actor.role == ROLE_SPY and game and game.spies_know_each_other:
                known_spies = tuple(
                    member.display_name
                    for member in members
                    if member.id != actor.id
                    and member.status == "active"
                    and member.role == ROLE_SPY
                )
            return RoomView(
                room_id=room.id,
                status=room.status,
                game_type=room.game_type,
                join_code=room.join_code,
                join_token=room.join_token,
                host_user_id=room.host_user_id,
                is_join_open=room.is_join_open,
                settings=dict(room.settings),
                revision=room.revision,
                actor_user_id=actor_user_id,
                actor_member_id=actor.id if actor else None,
                actor_status=actor.status if actor else None,
                actor_role=actor.role if actor else None,
                members=tuple(members),
                themes=themes,
                game=game,
                vote=vote_view,
                known_spy_names=known_spies,
            )

    async def room_recipients(self, room_id: int) -> list[int]:
        async with self._database.session() as session:
            return list(
                await session.scalars(
                    select(RoomMember.user_id).where(
                        RoomMember.room_id == room_id, RoomMember.is_reachable.is_(True)
                    )
                )
            )

    async def room_game_type(self, room_id: int) -> str:
        async with self._database.session() as session:
            game_type = await session.scalar(select(Room.game_type).where(Room.id == room_id))
            if game_type is None:
                raise DomainError("Комната не найдена.")
            return game_type

    async def playing_room_for_user(self, user_id: int) -> int | None:
        """Return an unfinished party in which the user still participates or spectates."""
        async with self._database.session() as session:
            return await session.scalar(
                select(Room.id)
                .join(RoomMember, RoomMember.room_id == Room.id)
                .outerjoin(HatGame, HatGame.room_id == Room.id)
                .where(
                    RoomMember.user_id == user_id,
                    RoomMember.status.in_(("active", "exiled", "left")),
                    or_(
                        Room.status == "playing",
                        and_(
                            Room.game_type == "hat",
                            HatGame.phase.in_(
                                (
                                    "collecting",
                                    "round_break",
                                    "ready",
                                    "countdown",
                                    "turn",
                                    "appeal",
                                    "final_break",
                                )
                            ),
                        ),
                    ),
                )
                .order_by(Room.id)
                .limit(1)
            )

    async def open_rooms_for_user(self, user_id: int) -> list[OpenRoomSummary]:
        async with self._database.session() as session:
            rows = await session.execute(
                select(Room.id, Room.game_type, Room.status, Room.join_code)
                .join(RoomMember, RoomMember.room_id == Room.id)
                .where(
                    RoomMember.user_id == user_id,
                    RoomMember.status.in_(("active", "exiled", "left")),
                    Room.status.in_(("lobby", "playing")),
                )
                .order_by(Room.created_at.desc(), Room.id.desc())
            )
            return [
                OpenRoomSummary(
                    id=room_id,
                    game_type=game_type,
                    status=status,
                    join_code=join_code,
                )
                for room_id, game_type, status, join_code in rows
            ]

    async def _new_join_code(self, session: AsyncSession) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for _ in range(10):
            code = "".join(secrets.choice(alphabet) for _ in range(7))
            exists = await session.scalar(select(Room.id).where(Room.join_code == code))
            if exists is None:
                return code
        raise RuntimeError("Unable to allocate a unique room code")

    async def _active_member_count(self, session: AsyncSession, room_id: int) -> int:
        return len(await self._active_members(session, room_id))

    async def _active_members(self, session: AsyncSession, room_id: int) -> list[RoomMember]:
        return list(
            await session.scalars(
                select(RoomMember)
                .where(RoomMember.room_id == room_id, RoomMember.status == "active")
                .order_by(RoomMember.joined_at, RoomMember.id)
            )
        )

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
                role=member.role,
                joined_at=member.joined_at,
            )
            for member, user in rows
        ]

    async def _member_for_user(
        self, session: AsyncSession, room_id: int, user_id: int
    ) -> RoomMember | None:
        return await session.scalar(
            select(RoomMember).where(RoomMember.room_id == room_id, RoomMember.user_id == user_id)
        )

    async def _open_vote(self, session: AsyncSession, room_id: int) -> Vote | None:
        return await session.scalar(
            select(Vote)
            .where(Vote.room_id == room_id, Vote.status == "open")
            .order_by(Vote.id.desc())
        )

    async def _vote_view(
        self, session: AsyncSession, vote: Vote | None, actor_member_id: int | None
    ) -> VoteView | None:
        if vote is None:
            return None
        choices = list(
            await session.scalars(select(VoteChoice).where(VoteChoice.vote_id == vote.id))
        )
        eligible = tuple(int(member_id) for member_id in vote.eligible_member_ids)
        eligible_set = set(eligible)
        yes_count = sum(choice.choice for choice in choices if choice.member_id in eligible_set)
        no_count = sum(not choice.choice for choice in choices if choice.member_id in eligible_set)
        actor_choice = next(
            (choice.choice for choice in choices if choice.member_id == actor_member_id), None
        )
        return VoteView(
            id=vote.id,
            kind=vote.kind,
            subject_member_id=vote.subject_member_id,
            eligible_member_ids=eligible,
            yes_count=yes_count,
            no_count=no_count,
            actor_choice=actor_choice,
        )

    async def _current_eligible_members(
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

    async def _require_active_playing_member(
        self, session: AsyncSession, room: Room, user_id: int
    ) -> RoomMember:
        if room.status != "playing":
            raise DomainError("Партия сейчас не идёт.")
        member = await self._member_for_user(session, room.id, user_id)
        if member is None or member.status != "active":
            raise DomainError("Зрители не могут управлять игрой.")
        return member

    def _require_lobby_host(self, room: Room, user_id: int) -> None:
        if room.status != "lobby":
            raise DomainError("Эту настройку можно изменить только до старта.")
        if room.host_user_id != user_id:
            raise DomainError("Эта настройка доступна только хосту.")

    async def _transfer_host_if_needed(self, session: AsyncSession, room: Room) -> None:
        current_host = await self._member_for_user(session, room.id, room.host_user_id)
        if current_host is not None and current_host.status == "active":
            return
        successors = await self._active_members(session, room.id)
        if successors:
            room.host_user_id = successors[0].user_id

    async def _prune_and_resolve_open_vote(
        self, session: AsyncSession, room: Room, departed_member_id: int
    ) -> None:
        vote = await self._open_vote(session, room.id)
        if vote is None or departed_member_id not in vote.eligible_member_ids:
            return
        vote.eligible_member_ids = [
            member_id for member_id in vote.eligible_member_ids if member_id != departed_member_id
        ]
        await self._resolve_open_vote_if_ready(session, room, vote)

    async def _resolve_open_vote_if_ready(
        self, session: AsyncSession, room: Room, vote: Vote
    ) -> VoteOutcome | None:
        eligible = await self._current_eligible_members(session, room.id, vote.eligible_member_ids)
        vote.eligible_member_ids = [member.id for member in eligible]
        choices = list(
            await session.scalars(select(VoteChoice).where(VoteChoice.vote_id == vote.id))
        )
        eligible_ids = set(vote.eligible_member_ids)
        yes_count = sum(choice.choice for choice in choices if choice.member_id in eligible_ids)
        cast_count = sum(1 for choice in choices if choice.member_id in eligible_ids)
        resolution = resolve_vote(
            eligible_count=len(eligible_ids), yes_count=yes_count, cast_count=cast_count
        )
        if not resolution.is_resolved:
            return None
        subject = await session.get(RoomMember, vote.subject_member_id)
        if subject is None or subject.room_id != room.id:
            raise RuntimeError("Vote subject is missing from its room")
        subject_user = await session.get(User, subject.user_id)
        outcome = VoteOutcome(
            kind=vote.kind,
            subject_name=subject_user.display_name if subject_user else str(subject.user_id),
            subject_role=subject.role,
            accepted=bool(resolution.accepted),
        )
        vote.status = "closed"
        vote.closed_at = datetime.now(timezone.utc)
        if vote.kind == VOTE_EXILE:
            if resolution.accepted:
                await self._exile_member(session, room, vote.subject_member_id)
                await self._maybe_finish_civilians(session, room)
        elif vote.kind == VOTE_REVEAL:
            if resolution.accepted:
                await self._finish(session, room, "spies_win_reveal")
            else:
                await self._exile_member(session, room, vote.subject_member_id)
                await self._maybe_finish_civilians(session, room)
        else:
            raise RuntimeError(f"Unknown vote kind: {vote.kind}")
        return outcome

    async def _exile_member(self, session: AsyncSession, room: Room, member_id: int) -> None:
        member = await session.get(RoomMember, member_id)
        if member is None or member.room_id != room.id or member.status != "active":
            return
        member.status = "exiled"
        member.left_at = datetime.now(timezone.utc)
        await self._transfer_host_if_needed(session, room)

    async def _maybe_finish_civilians(self, session: AsyncSession, room: Room) -> None:
        game = await session.get(SpyGame, room.id)
        if game is None or room.status != "playing":
            return
        active_spies = await session.scalar(
            select(RoomMember.id).where(
                RoomMember.room_id == room.id,
                RoomMember.status == "active",
                RoomMember.role == ROLE_SPY,
            )
        )
        if active_spies is None:
            await self._finish(session, room, "civilians_win")

    async def _finish(self, session: AsyncSession, room: Room, result: str) -> None:
        game = await session.get(SpyGame, room.id)
        if game is not None:
            game.result = result
        room.status = "finished"
        room.finished_at = datetime.now(timezone.utc)
        open_vote = await self._open_vote(session, room.id)
        if open_vote is not None:
            open_vote.status = "closed"
            open_vote.closed_at = datetime.now(timezone.utc)

    def _bump(self, room: Room) -> None:
        room.revision += 1

    class _RoomTransaction:
        def __init__(self, service: RoomService, room_id: int) -> None:
            self._service = service
            self._room_id = room_id
            self._session_cm = None
            self._session: AsyncSession | None = None
            self._transaction: AsyncSessionTransaction | None = None
            self._lock = service._locks.for_room(room_id)

        async def __aenter__(self) -> tuple[AsyncSession, Room]:
            await self._lock.acquire()
            self._session_cm = self._service._database.session()
            self._session = await self._session_cm.__aenter__()
            self._transaction = self._session.begin()
            await self._transaction.__aenter__()
            room = await self._session.get(Room, self._room_id)
            if room is None:
                await self.__aexit__(None, None, None)
                raise DomainError("Комната не найдена.")
            return self._session, room

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            try:
                if self._transaction is not None:
                    await self._transaction.__aexit__(exc_type, exc, tb)
            finally:
                if self._session_cm is not None:
                    await self._session_cm.__aexit__(exc_type, exc, tb)
                if self._lock.locked():
                    self._lock.release()

    def _room_transaction(self, room_id: int) -> _RoomTransaction:
        return self._RoomTransaction(self, room_id)

    def room_transaction(self, room_id: int) -> _RoomTransaction:
        """Shared room lock and transaction for game-specific services."""
        return self._room_transaction(room_id)
