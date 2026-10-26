"""Persist evaluation answers and provider attempts without rewriting earlier schemas."""
from alembic import op
from app.documents.database import Session
from app.documents.schema_v4 import upgrade_stage4
revision = "0003_evaluations"
down_revision = "0002_contexts"
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    upgrade_stage4(Session(connection.connection, connection.dialect.name))

def downgrade():
    raise RuntimeError("Evaluation evidence is immutable; restore a verified backup instead")
