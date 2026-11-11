"""Version replacement state writes and shared-workspace projections."""

import sqlalchemy as sa
from alembic import op

revision = "a9c2e5f8b310"
down_revision = "f7a9b2c4d610"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "replacement_runs",
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("replacement_runs", "state_version")
