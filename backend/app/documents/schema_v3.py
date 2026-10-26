"""Additive schema upgrade shared by CLI and Alembic."""
def upgrade_stage3(db):
    if db.dialect=="sqlite":
        fields={row["name"] for row in db.rows("PRAGMA table_info(t_job)")}
    else:
        fields={row["column_name"] for row in db.rows("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='t_job'")}
    for name,definition in (
        ("cancel_requested","INTEGER NOT NULL DEFAULT 0"),
        ("parent_job_id","VARCHAR(36) REFERENCES t_job(id)"),
        ("progress_json","TEXT NOT NULL DEFAULT '{}'"),
    ):
        if name not in fields:
            db.execute(f"ALTER TABLE t_job ADD COLUMN {name} {definition}")
    for sql in [
        """CREATE TABLE IF NOT EXISTS t_job_attempt (
        id VARCHAR(36) PRIMARY KEY,job_id VARCHAR(36) NOT NULL REFERENCES t_job(id),worker_id VARCHAR(36) NOT NULL,
        attempt_number INTEGER NOT NULL,started_at VARCHAR(40) NOT NULL,finished_at VARCHAR(40),status VARCHAR(24) NOT NULL,
        error_code TEXT,result_json TEXT,UNIQUE(job_id,attempt_number))""",
        """CREATE TABLE IF NOT EXISTS t_retrieval_run (
        id VARCHAR(36) PRIMARY KEY,job_id VARCHAR(36) UNIQUE REFERENCES t_job(id),
        index_id VARCHAR(36) NOT NULL REFERENCES t_index_version(id),request_json TEXT NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,status VARCHAR(24) NOT NULL,
        snapshot_key TEXT,snapshot_sha256 VARCHAR(64),parent_run_id VARCHAR(36) REFERENCES t_retrieval_run(id),
        created_at VARCHAR(40) NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS ix_retrieval_runs ON t_retrieval_run(created_at,id)",
        "INSERT INTO t_document_schema(revision) VALUES(3) ON CONFLICT(revision) DO NOTHING",
    ]:
        db.execute(sql)
