"""Add per-market official institution and social platform inventories.

Revision ID: 20260811_0002
Revises: 20260811_0001
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_0002"
down_revision = "20260811_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "official_source_inventory",
        sa.Column("inventory_id", sa.String(length=64), nullable=False),
        sa.Column("country_id", sa.String(length=64), nullable=False),
        sa.Column("institution_type", sa.String(length=32), nullable=False),
        sa.Column("source_scope", sa.String(length=16), nullable=False),
        sa.Column("institution_name", sa.String(length=300), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=False),
        sa.Column("notice_url", sa.String(length=1500), nullable=False),
        sa.Column("allowed_hosts", sa.JSON(), nullable=False),
        sa.Column("languages", sa.JSON(), nullable=False),
        sa.Column("access_method", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["country_id"], ["country.eden_country_id"]),
        sa.PrimaryKeyConstraint("inventory_id"),
        sa.UniqueConstraint(
            "country_id",
            "institution_type",
            "source_scope",
            name="uq_official_inventory_country_type_scope",
        ),
    )
    op.create_index(
        "ix_official_inventory_country_status",
        "official_source_inventory",
        ["country_id", "status"],
    )
    op.create_table(
        "social_source_inventory",
        sa.Column("inventory_id", sa.String(length=64), nullable=False),
        sa.Column("country_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("platform_name", sa.String(length=100), nullable=False),
        sa.Column("relevance_tier", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_reason", sa.String(length=1000), nullable=True),
        sa.Column("docs_url", sa.String(length=1000), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["country_id"], ["country.eden_country_id"]),
        sa.ForeignKeyConstraint(["source_id"], ["source_registry.source_id"]),
        sa.PrimaryKeyConstraint("inventory_id"),
        sa.UniqueConstraint(
            "country_id",
            "source_id",
            name="uq_social_inventory_country_source",
        ),
    )
    op.create_index(
        "ix_social_inventory_country_status",
        "social_source_inventory",
        ["country_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_social_inventory_country_status",
        table_name="social_source_inventory",
    )
    op.drop_table("social_source_inventory")
    op.drop_index(
        "ix_official_inventory_country_status",
        table_name="official_source_inventory",
    )
    op.drop_table("official_source_inventory")
