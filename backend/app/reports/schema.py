"""Additive report-export schema, shared by CLI and Alembic."""


def upgrade_reports(db):
    db.execute("""CREATE TABLE IF NOT EXISTS t_report_export (
        id VARCHAR(36) PRIMARY KEY,
        evaluation_id VARCHAR(36) NOT NULL REFERENCES t_evaluation_run(id),
        format VARCHAR(8) NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,
        idempotency_key VARCHAR(200) UNIQUE,
        snapshot_key TEXT NOT NULL,
        snapshot_sha256 VARCHAR(64) NOT NULL,
        file_key TEXT NOT NULL,
        file_sha256 VARCHAR(64) NOT NULL,
        size_bytes INTEGER NOT NULL,
        manifest_json TEXT NOT NULL,
        created_at VARCHAR(40) NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_report_export_run ON t_report_export(evaluation_id,created_at,id)")

