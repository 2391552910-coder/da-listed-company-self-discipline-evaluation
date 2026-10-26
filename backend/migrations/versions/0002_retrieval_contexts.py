"""Freeze retrieval contexts and track job lifecycle without deleting stage2 data."""
from alembic import op
from app.documents.database import Session
from app.documents.schema_v3 import upgrade_stage3
revision="0002_contexts"
down_revision="0001_documents"
branch_labels=None
depends_on=None

def upgrade():
    connection=op.get_bind()
    # SQLAlchemy manages the migration transaction. The wrapper does not commit.
    upgrade_stage3(Session(connection.connection,connection.dialect.name))

def downgrade():
    raise RuntimeError("Evidence snapshots are immutable; restore a verified backup instead")
