"""baseline: the schema as of the first Alembic release

Databases made before Alembic were built by create_all() and the small ALTER statements in app/database.py.
They are only marked with this revision (stamp), nothing is changed. A new empty database gets the same schema
from create_all() at start-up. Every later schema change is a new revision here:

    alembic revision --autogenerate -m "what changed"

Revision ID: 0001
Revises:
Create Date: 2026-11-01
"""
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
