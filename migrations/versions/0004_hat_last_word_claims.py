"""Add voting for a last word explained at the turn deadline.

Revision ID: 0004_hat_last_word_claims
Revises: 0003_hat_game
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_hat_last_word_claims"
down_revision = "0003_hat_game"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "hat_last_word_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column(
            "previous_turn_id", sa.Integer(), sa.ForeignKey("hat_turns.id"), nullable=False
        ),
        sa.Column(
            "round_card_id", sa.Integer(), sa.ForeignKey("hat_round_cards.id"), nullable=False
        ),
        sa.Column(
            "requester_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False
        ),
        sa.Column("eligible_member_ids", sa.JSON(), nullable=False),
        sa.Column("automatic_yes_member_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("round_card_id", name="uq_hat_last_word_claim_card"),
    )
    op.create_index("ix_hat_last_word_claims_room_id", "hat_last_word_claims", ["room_id"])
    op.create_index(
        "ix_hat_last_word_claims_previous_turn_id",
        "hat_last_word_claims",
        ["previous_turn_id"],
    )
    op.create_index(
        "ix_hat_last_word_claims_round_card_id", "hat_last_word_claims", ["round_card_id"]
    )
    op.create_index(
        "ix_hat_last_word_claims_requester_member_id",
        "hat_last_word_claims",
        ["requester_member_id"],
    )
    op.create_table(
        "hat_last_word_claim_votes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "claim_id", sa.Integer(), sa.ForeignKey("hat_last_word_claims.id"), nullable=False
        ),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False),
        sa.Column("choice", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("claim_id", "member_id", name="uq_hat_last_word_claim_vote"),
    )
    op.create_index(
        "ix_hat_last_word_claim_votes_claim_id", "hat_last_word_claim_votes", ["claim_id"]
    )
    op.create_index(
        "ix_hat_last_word_claim_votes_member_id", "hat_last_word_claim_votes", ["member_id"]
    )


def downgrade() -> None:
    op.drop_table("hat_last_word_claim_votes")
    op.drop_table("hat_last_word_claims")
