"""Install vector extension and agent schema.

Revision ID: 0001_bootstrap
Revises:
Create Date: 2026-11-09
"""

from alembic import op

revision = "0001_bootstrap"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS agent_state")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS agent_state")
    # Keep the shared vector extension: dropping it may affect other schemas.
