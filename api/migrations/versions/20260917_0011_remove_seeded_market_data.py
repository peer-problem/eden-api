"""Remove fixed market statistics and inferred country defaults.

Public readers discard legacy synthetic scores even before this cleanup runs.
This migration does not remove collected observations or their raw evidence.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260917_0011"
# Independent of the separately gated snapshot storage contraction.
down_revision = "20260911_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("market_cohort")
    op.drop_column("country", "default_language")
    op.drop_column("country", "default_currency")
    op.execute(sa.text("UPDATE place_relation SET score = NULL WHERE relation_type = 'related'"))
    op.execute(
        sa.text(
            "DELETE FROM metric_definition "
            "WHERE metric_id IN ('inbound_score', 'recommendation_score', 'crowd_index')"
        )
    )


def downgrade() -> None:
    # Restore only the old schema. Removed fixed values must not be recreated.
    op.add_column("country", sa.Column("default_language", sa.String(16), nullable=True))
    op.add_column("country", sa.Column("default_currency", sa.String(3), nullable=True))
    op.create_table(
        "market_cohort",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("statistics_period", sa.String(32), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("country_id", sa.String(64), sa.ForeignKey("country.eden_country_id")),
        sa.Column("visitor_count", sa.BigInteger()),
        sa.Column("fixed_at", sa.DateTime(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("version", "rank"),
        sa.UniqueConstraint("version", "country_id"),
    )
