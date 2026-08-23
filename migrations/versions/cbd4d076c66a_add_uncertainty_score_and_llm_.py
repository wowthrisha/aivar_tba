"""add uncertainty_score and llm_confidence_raw to risk_assessments

Revision ID: cbd4d076c66a
Revises: 5517c3ad655b
Create Date: 2026-08-23 10:42:28.987239

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cbd4d076c66a'
down_revision: Union[str, None] = '5517c3ad655b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEPRECATION_COMMENT = (
    "DEPRECATED (L-G): this column actually holds 1 - llm_confidence "
    "(an uncertainty score), not a confidence score - the name lies. Use "
    "uncertainty_score (same value, honest name) or llm_confidence_raw "
    "(the true, uninverted model self-report) instead. Kept, populated, "
    "not dropped - see governance/plan/03-errors-and-fixes.md L-G."
)


def upgrade() -> None:
    # Additive only - no rename, no drop, no type change. confidence_score
    # (renamed nowhere, still written on every save_risk_assessment call)
    # actually holds 1 - llm_confidence; uncertainty_score is the same
    # value under an honest name, llm_confidence_raw is the true,
    # uninverted model self-report. Nullable: existing NOT NULL
    # confidence_score guarantees every row backfills cleanly below, but
    # NOT NULL isn't required by the task and isn't added here.
    op.add_column("risk_assessments", sa.Column("uncertainty_score", sa.Float(), nullable=True))
    op.add_column("risk_assessments", sa.Column("llm_confidence_raw", sa.Float(), nullable=True))

    op.execute(
        "UPDATE risk_assessments "
        "SET uncertainty_score = confidence_score, "
        "    llm_confidence_raw = 1 - confidence_score"
    )

    op.execute(f"COMMENT ON COLUMN risk_assessments.confidence_score IS '{DEPRECATION_COMMENT}'")


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN risk_assessments.confidence_score IS NULL")
    op.drop_column("risk_assessments", "llm_confidence_raw")
    op.drop_column("risk_assessments", "uncertainty_score")
