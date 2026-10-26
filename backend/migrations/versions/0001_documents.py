"""Add stage2 tables without deleting or fabricating legacy provenance."""
from alembic import op
from app.documents.database import schema_statements

revision="0001_documents"
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    connection=op.get_bind()
    dialect=connection.dialect.name
    for statement in schema_statements(dialect):
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("INSERT INTO t_document_schema(revision) VALUES(2) ON CONFLICT(revision) DO NOTHING")

def downgrade():
    # Preserve all existing source records and old prototype tables.
    # Destructive downgrade requires a separately reviewed backup/restore procedure.
    raise RuntimeError("Source-preserving migration: restore a verified backup instead of dropping evidence")
