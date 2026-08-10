from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_type: Mapped[str] = mapped_column(String(64), default="spy")
    status: Mapped[str] = mapped_column(String(32), default="lobby")
    join_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    join_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    host_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    is_join_open: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_reachable: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class RoomPanel(Base):
    __tablename__ = "room_panels"
    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_room_panel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    context: Mapped[str] = mapped_column(String(16), default="lobby")


class HomePanel(Base):
    __tablename__ = "home_panels"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(Integer)
    context: Mapped[str] = mapped_column(String(16), default="home")


class SpyGame(Base):
    __tablename__ = "spy_games"

    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), primary_key=True)
    theme_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    theme_name: Mapped[str] = mapped_column(String(256))
    location_name: Mapped[str] = mapped_column(String(256))
    spy_count: Mapped[int] = mapped_column(Integer)
    spies_know_each_other: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class HatGame(Base):
    __tablename__ = "hat_games"

    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(32), default="configured")
    words_per_player: Mapped[int] = mapped_column(Integer, default=3)
    round_seconds: Mapped[list[int]] = mapped_column(JSON, default=lambda: [30, 30, 30])
    collection_deadline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    round_number: Mapped[int] = mapped_column(Integer, default=0)
    next_explainer_seat: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class HatSeat(Base):
    __tablename__ = "hat_seats"
    __table_args__ = (
        UniqueConstraint("room_id", "member_id", name="uq_hat_seat_member"),
        UniqueConstraint("room_id", "seat_number", name="uq_hat_seat_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    seat_number: Mapped[int] = mapped_column(Integer)


class HatWord(Base):
    __tablename__ = "hat_words"
    __table_args__ = (UniqueConstraint("room_id", "normalized_text", name="uq_hat_word_text"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    author_member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    text: Mapped[str] = mapped_column(String(120))
    normalized_text: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HatTurn(Base):
    __tablename__ = "hat_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    explainer_member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    guesser_member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    explainer_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    guesser_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    countdown_deadline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    turn_deadline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class HatRoundCard(Base):
    __tablename__ = "hat_round_cards"
    __table_args__ = (UniqueConstraint("word_id", "round_number", name="uq_hat_round_card"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    word_id: Mapped[int] = mapped_column(ForeignKey("hat_words.id"), index=True)
    round_number: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), default="available")
    turn_id: Mapped[Optional[int]] = mapped_column(ForeignKey("hat_turns.id"), nullable=True)
    explainer_member_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("room_members.id"), nullable=True
    )
    guesser_member_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("room_members.id"), nullable=True
    )
    score_counted: Mapped[bool] = mapped_column(Boolean, default=False)
    appeal_attempts: Mapped[int] = mapped_column(Integer, default=0)


class HatAppeal(Base):
    __tablename__ = "hat_appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    round_card_id: Mapped[int] = mapped_column(ForeignKey("hat_round_cards.id"), index=True)
    proposer_member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    eligible_member_ids: Mapped[list[int]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="open")
    accepted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class HatAppealVote(Base):
    __tablename__ = "hat_appeal_votes"
    __table_args__ = (UniqueConstraint("appeal_id", "member_id", name="uq_hat_appeal_vote"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    appeal_id: Mapped[int] = mapped_column(ForeignKey("hat_appeals.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    choice: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HatLastWordClaim(Base):
    __tablename__ = "hat_last_word_claims"
    __table_args__ = (
        UniqueConstraint("round_card_id", name="uq_hat_last_word_claim_card"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    previous_turn_id: Mapped[int] = mapped_column(ForeignKey("hat_turns.id"), index=True)
    round_card_id: Mapped[int] = mapped_column(ForeignKey("hat_round_cards.id"), index=True)
    requester_member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    eligible_member_ids: Mapped[list[int]] = mapped_column(JSON)
    automatic_yes_member_ids: Mapped[list[int]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="open")
    accepted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class HatLastWordClaimVote(Base):
    __tablename__ = "hat_last_word_claim_votes"
    __table_args__ = (
        UniqueConstraint("claim_id", "member_id", name="uq_hat_last_word_claim_vote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("hat_last_word_claims.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    choice: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Vote(Base):
    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    subject_member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"))
    eligible_member_ids: Mapped[list[int]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class VoteChoice(Base):
    __tablename__ = "vote_choices"
    __table_args__ = (UniqueConstraint("vote_id", "member_id", name="uq_vote_choice"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vote_id: Mapped[int] = mapped_column(ForeignKey("votes.id"), index=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("room_members.id"), index=True)
    choice: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogTheme(Base):
    __tablename__ = "catalog_themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CatalogLocation(Base):
    __tablename__ = "catalog_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("catalog_themes.id"), index=True)
    name: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
