"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.text("CURRENT_TIMESTAMP")
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("game_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("join_code", sa.String(length=12), nullable=False),
        sa.Column("join_token", sa.String(length=64), nullable=False),
        sa.Column(
            "host_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id"), nullable=False
        ),
        sa.Column("is_join_open", sa.Boolean(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("join_code"),
        sa.UniqueConstraint("join_token"),
    )
    op.create_index("ix_rooms_join_code", "rooms", ["join_code"])
    op.create_index("ix_rooms_join_token", "rooms", ["join_token"])
    op.create_table(
        "room_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("is_reachable", sa.Boolean(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("room_id", "user_id", name="uq_room_member"),
    )
    op.create_index("ix_room_members_room_id", "room_members", ["room_id"])
    op.create_index("ix_room_members_user_id", "room_members", ["user_id"])
    op.create_table(
        "room_panels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id"), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint("room_id", "user_id", name="uq_room_panel"),
    )
    op.create_index("ix_room_panels_room_id", "room_panels", ["room_id"])
    op.create_index("ix_room_panels_user_id", "room_panels", ["user_id"])
    op.create_table(
        "home_panels",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id"), primary_key=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
    )
    op.create_table(
        "catalog_themes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "catalog_locations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("theme_id", sa.Integer(), sa.ForeignKey("catalog_themes.id"), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_index("ix_catalog_locations_theme_id", "catalog_locations", ["theme_id"])
    op.create_table(
        "spy_games",
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), primary_key=True),
        sa.Column("theme_id", sa.Integer(), nullable=True),
        sa.Column("theme_name", sa.String(length=256), nullable=False),
        sa.Column("location_name", sa.String(length=256), nullable=False),
        sa.Column("spy_count", sa.Integer(), nullable=False),
        sa.Column("spies_know_each_other", sa.Boolean(), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "subject_member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False
        ),
        sa.Column("eligible_member_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_votes_room_id", "votes", ["room_id"])
    op.create_table(
        "vote_choices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vote_id", sa.Integer(), sa.ForeignKey("votes.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("room_members.id"), nullable=False),
        sa.Column("choice", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("vote_id", "member_id", name="uq_vote_choice"),
    )
    op.create_index("ix_vote_choices_vote_id", "vote_choices", ["vote_id"])
    op.create_index("ix_vote_choices_member_id", "vote_choices", ["member_id"])


def downgrade() -> None:
    op.drop_table("vote_choices")
    op.drop_table("votes")
    op.drop_table("spy_games")
    op.drop_table("catalog_locations")
    op.drop_table("catalog_themes")
    op.drop_table("home_panels")
    op.drop_table("room_panels")
    op.drop_table("room_members")
    op.drop_table("rooms")
    op.drop_table("users")
