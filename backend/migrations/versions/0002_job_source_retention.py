from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analysis_jobs', sa.Column('source_purged_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('analysis_jobs', 'source_purged_at')
