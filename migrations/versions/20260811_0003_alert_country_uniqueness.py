"""Allow one official notice to target multiple market countries.

Revision ID: 20260811_0003
Revises: 20260811_0002
"""

from alembic import op

revision = "20260811_0003"
down_revision = "20260811_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_alert_document_source_fk",
        "alert_document",
        ["source_id"],
    )
    op.drop_constraint("source_id", "alert_document", type_="unique")
    op.create_unique_constraint(
        "uq_alert_source_country_url",
        "alert_document",
        ["source_id", "country_id", "canonical_url_hash"],
    )
    op.drop_index(
        "ix_alert_document_source_fk",
        table_name="alert_document",
    )


def downgrade() -> None:
    op.create_index(
        "ix_alert_document_source_fk",
        "alert_document",
        ["source_id"],
    )
    op.drop_constraint(
        "uq_alert_source_country_url",
        "alert_document",
        type_="unique",
    )
    op.create_unique_constraint(
        "source_id",
        "alert_document",
        ["source_id", "canonical_url_hash"],
    )
    op.drop_index(
        "ix_alert_document_source_fk",
        table_name="alert_document",
    )
