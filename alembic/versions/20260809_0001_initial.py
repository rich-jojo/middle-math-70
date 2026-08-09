"""initial central math schema

Revision ID: 20260809_0001
Revises:
Create Date: 2026-08-09
"""

from __future__ import annotations

from alembic import op
from app import models  # noqa: F401
from app.db import Base

revision = "20260809_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
