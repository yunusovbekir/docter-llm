"""Create the initial Docter application schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("base_url", sa.String),
        sa.Column("embedding_model", sa.String, nullable=False),
        sa.Column("temperature", sa.Float, nullable=False),
        sa.Column("max_agent_steps", sa.Integer, nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("current_branch", sa.String),
        sa.Column("index_status", sa.String(7), nullable=False),
        sa.Column("indexed_at", sa.DateTime),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("path"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("mode", sa.String, nullable=False),
        sa.Column("mode_params", sa.JSON, nullable=False),
        sa.Column("branch_name", sa.String),
        sa.Column("run_status", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_sessions_project_id", "sessions", ["project_id"])
    op.create_table(
        "change_sets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("run_id", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_change_sets_session_id", "change_sets", ["session_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text),
        sa.Column("tool_calls", sa.JSON),
        sa.Column("tool_call_id", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_table(
        "changes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("change_set_id", sa.Integer, sa.ForeignKey("change_sets.id"), nullable=False),
        sa.Column("file_path", sa.String, nullable=False),
        sa.Column("patch", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("parent_change_id", sa.Integer, sa.ForeignKey("changes.id")),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("original_content", sa.Text, nullable=False),
        sa.Column("proposed_content", sa.Text, nullable=False),
    )
    op.create_index("ix_changes_change_set_id", "changes", ["change_set_id"])
    op.create_table(
        "change_hunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("change_id", sa.Integer, sa.ForeignKey("changes.id"), nullable=False),
        sa.Column("index", sa.Integer, nullable=False),
        sa.Column("header", sa.String, nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.UniqueConstraint("change_id", "index"),
    )
    op.create_index("ix_change_hunks_change_id", "change_hunks", ["change_id"])
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("change_id", sa.Integer, sa.ForeignKey("changes.id"), nullable=False),
        sa.Column("hunk_index", sa.Integer),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_comments_change_id", "comments", ["change_id"])


def downgrade() -> None:
    op.drop_table("comments")
    op.drop_table("change_hunks")
    op.drop_table("changes")
    op.drop_table("messages")
    op.drop_table("change_sets")
    op.drop_table("sessions")
    op.drop_table("projects")
    op.drop_table("app_settings")
