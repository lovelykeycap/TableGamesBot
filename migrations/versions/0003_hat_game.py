"""Add storage for the Hat game.

Revision ID: 0003_hat_game
Revises: 0002_panel_contexts
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_hat_game"
down_revision = "0002_panel_contexts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "hat_games",
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), primary_key=True),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("words_per_player", sa.Integer(), nullable=False),
        sa.Column("round_seconds", sa.JSON(), nullable=False),
        sa.Column("collection_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("next_explainer_seat", sa.Integer(), nullable=True),
        sa.Column("result", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "hat_seats",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False),
        sa.Column("seat_number", sa.Integer(), nullable=False),
        sa.UniqueConstraint("room_id", "member_id", name="uq_hat_seat_member"),
        sa.UniqueConstraint("room_id", "seat_number", name="uq_hat_seat_number"),
    )
    op.create_index("ix_hat_seats_room_id", "hat_seats", ["room_id"])
    op.create_index("ix_hat_seats_member_id", "hat_seats", ["member_id"])
    op.create_table(
        "hat_words",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column(
            "author_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False
        ),
        sa.Column("text", sa.String(length=120), nullable=False),
        sa.Column("normalized_text", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("room_id", "normalized_text", name="uq_hat_word_text"),
    )
    op.create_index("ix_hat_words_room_id", "hat_words", ["room_id"])
    op.create_index("ix_hat_words_author_member_id", "hat_words", ["author_member_id"])
    op.create_table(
        "hat_turns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column(
            "explainer_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False
        ),
        sa.Column(
            "guesser_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("explainer_ready", sa.Boolean(), nullable=False),
        sa.Column("guesser_ready", sa.Boolean(), nullable=False),
        sa.Column("countdown_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turn_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_hat_turns_room_id", "hat_turns", ["room_id"])
    op.create_index("ix_hat_turns_explainer_member_id", "hat_turns", ["explainer_member_id"])
    op.create_index("ix_hat_turns_guesser_member_id", "hat_turns", ["guesser_member_id"])
    op.create_table(
        "hat_round_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column("word_id", sa.Integer(), sa.ForeignKey("hat_words.id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("turn_id", sa.Integer(), sa.ForeignKey("hat_turns.id"), nullable=True),
        sa.Column(
            "explainer_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=True
        ),
        sa.Column("guesser_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=True),
        sa.Column("score_counted", sa.Boolean(), nullable=False),
        sa.Column("appeal_attempts", sa.Integer(), nullable=False),
        sa.UniqueConstraint("word_id", "round_number", name="uq_hat_round_card"),
    )
    op.create_index("ix_hat_round_cards_room_id", "hat_round_cards", ["room_id"])
    op.create_index("ix_hat_round_cards_word_id", "hat_round_cards", ["word_id"])
    op.create_table(
        "hat_appeals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column(
            "round_card_id", sa.Integer(), sa.ForeignKey("hat_round_cards.id"), nullable=False
        ),
        sa.Column(
            "proposer_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False
        ),
        sa.Column("eligible_member_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_hat_appeals_room_id", "hat_appeals", ["room_id"])
    op.create_index("ix_hat_appeals_round_card_id", "hat_appeals", ["round_card_id"])
    op.create_index("ix_hat_appeals_proposer_member_id", "hat_appeals", ["proposer_member_id"])
    op.create_table(
        "hat_appeal_votes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("appeal_id", sa.Integer(), sa.ForeignKey("hat_appeals.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False),
        sa.Column("choice", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("appeal_id", "member_id", name="uq_hat_appeal_vote"),
    )
    op.create_index("ix_hat_appeal_votes_appeal_id", "hat_appeal_votes", ["appeal_id"])
    op.create_index("ix_hat_appeal_votes_member_id", "hat_appeal_votes", ["member_id"])


def downgrade() -> None:
    op.drop_table("hat_appeal_votes")
    op.drop_table("hat_appeals")
    op.drop_table("hat_round_cards")
    op.drop_table("hat_turns")
    op.drop_table("hat_words")
    op.drop_table("hat_seats")
    op.drop_table("hat_games")
