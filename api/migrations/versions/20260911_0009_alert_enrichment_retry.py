"""Add durable bounded alert enrichment retries without requiring snapshot contraction."""

import sqlalchemy as sa
from alembic import op

revision = "20260911_0009"
down_revision = "20260829_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_revision",
        sa.Column("enrichment_attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "alert_revision", sa.Column("enrichment_next_attempt_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("alert_revision", "enrichment_next_attempt_at")
    op.drop_column("alert_revision", "enrichment_attempt_count")
