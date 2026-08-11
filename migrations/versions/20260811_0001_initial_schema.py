"""Create EDEN canonical, provenance, fact, and read-model tables.

Revision ID: 20260811_0001
Revises: None
"""

from alembic import op

from app.repositories.models import Base

revision = "20260811_0001"
down_revision = None
branch_labels = None
depends_on = None

POST_INITIAL_TABLES = {
    "official_source_inventory",
    "social_source_inventory",
}


def _initial_tables():
    return [table for table in Base.metadata.sorted_tables if table.name not in POST_INITIAL_TABLES]


def upgrade() -> None:
    # The initial schema is emitted from this revision's pinned application release. Future
    # revisions must use explicit operations and never mutate a deployed revision.
    Base.metadata.create_all(bind=op.get_bind(), tables=_initial_tables(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=_initial_tables(), checkfirst=True)
