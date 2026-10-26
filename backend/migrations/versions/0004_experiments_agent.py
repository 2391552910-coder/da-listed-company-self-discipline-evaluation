"""Experiment protocols, Agent call steps and worker health."""
from alembic import op
from app.documents.database import Session
from app.documents.schema_v5 import upgrade_stage6

revision = "0004_experiments_agent"
down_revision = "0003_evaluations"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    upgrade_stage6(Session(connection.connection, connection.dialect.name))


def downgrade():
    raise RuntimeError("Experiment and call provenance is immutable; restore a verified backup instead")
