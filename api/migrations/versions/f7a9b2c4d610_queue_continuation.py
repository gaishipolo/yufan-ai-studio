"""Persist a fenced handler continuation without releasing its active key."""

from alembic import op
import sqlalchemy as sa

revision = "f7a9b2c4d610"
down_revision = "e8f1a3c6b290"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("queued_jobs", sa.Column("next_job", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("queued_jobs", "next_job")
