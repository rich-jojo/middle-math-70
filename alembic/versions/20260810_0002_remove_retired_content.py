"""remove retired static exam content

Revision ID: 20260810_0002
Revises: 20260809_0001
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op

revision = "20260810_0002"
down_revision = "20260809_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This release intentionally removes the retired exam and all dependent
    # snapshots. The remaining XP ledger is authoritative, so totals are
    # recomputed after its entries are removed.
    op.execute(
        """
        CREATE TEMP TABLE retired_problem_ids ON COMMIT DROP AS
        SELECT id FROM problems WHERE external_key LIKE 'math70-v2-%';

        CREATE TEMP TABLE retired_exam_version_ids ON COMMIT DROP AS
        SELECT ev.id
        FROM exam_versions ev
        JOIN exams e ON e.id = ev.exam_id
        WHERE e.slug = 'math70-v2'
        UNION
        SELECT DISTINCT evi.exam_version_id
        FROM exam_version_items evi
        WHERE evi.problem_id IN (SELECT id FROM retired_problem_ids);

        CREATE TEMP TABLE retired_attempt_ids ON COMMIT DROP AS
        SELECT id
        FROM attempts
        WHERE exam_version_id IN (SELECT id FROM retired_exam_version_ids);

        DELETE FROM xp_ledger
        WHERE attempt_id IN (SELECT id FROM retired_attempt_ids)
           OR problem_id IN (SELECT id FROM retired_problem_ids);

        DELETE FROM attempt_answers
        WHERE attempt_id IN (SELECT id FROM retired_attempt_ids);
        DELETE FROM attempts
        WHERE id IN (SELECT id FROM retired_attempt_ids);

        DELETE FROM exam_version_items
        WHERE exam_version_id IN (SELECT id FROM retired_exam_version_ids);
        DELETE FROM exam_versions
        WHERE id IN (SELECT id FROM retired_exam_version_ids);
        DELETE FROM exams WHERE slug = 'math70-v2';

        DELETE FROM problem_solves
        WHERE problem_id IN (SELECT id FROM retired_problem_ids);
        DELETE FROM problem_versions
        WHERE problem_id IN (SELECT id FROM retired_problem_ids);
        DELETE FROM problems
        WHERE id IN (SELECT id FROM retired_problem_ids);

        UPDATE users
        SET total_xp = COALESCE(
            (SELECT SUM(x.amount) FROM xp_ledger x WHERE x.user_id = users.id),
            0
        );
        """
    )


def downgrade() -> None:
    # Removed user attempts and retired content cannot be reconstructed safely.
    pass
