"""Stage 4 evaluation snapshots and provider call ledger (additive only)."""

def upgrade_stage4(db):
    for sql in [
        """CREATE TABLE IF NOT EXISTS t_evaluation_run (
        id VARCHAR(36) PRIMARY KEY,job_id VARCHAR(36) UNIQUE REFERENCES t_job(id),
        index_id VARCHAR(36) NOT NULL REFERENCES t_index_version(id),
        stock_code VARCHAR(6) NOT NULL,report_year INTEGER NOT NULL,
        request_json TEXT NOT NULL,request_sha256 VARCHAR(64) NOT NULL,status VARCHAR(24) NOT NULL,
        snapshot_key TEXT,snapshot_sha256 VARCHAR(64),execution_started_at VARCHAR(40),
        parent_run_id VARCHAR(36) REFERENCES t_evaluation_run(id),created_at VARCHAR(40) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS t_evaluation_answer (
        id VARCHAR(36) PRIMARY KEY,run_id VARCHAR(36) NOT NULL REFERENCES t_evaluation_run(id),
        question_key VARCHAR(80) NOT NULL,dimension VARCHAR(2) NOT NULL,status VARCHAR(24) NOT NULL,
        snapshot_key TEXT,snapshot_sha256 VARCHAR(64),error_code TEXT,
        created_at VARCHAR(40) NOT NULL,updated_at VARCHAR(40) NOT NULL,UNIQUE(run_id,question_key))""",
        """CREATE TABLE IF NOT EXISTS t_model_call (
        id VARCHAR(36) PRIMARY KEY,run_id VARCHAR(36) NOT NULL REFERENCES t_evaluation_run(id),
        answer_id VARCHAR(36) NOT NULL REFERENCES t_evaluation_answer(id),
        job_id VARCHAR(36) NOT NULL REFERENCES t_job(id),
        attempt_number INTEGER NOT NULL,call_number INTEGER NOT NULL,status VARCHAR(24) NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,response_key TEXT,response_sha256 VARCHAR(64),
        usage_json TEXT,error_code TEXT,provider_request_id TEXT,
        started_at VARCHAR(40) NOT NULL,finished_at VARCHAR(40),UNIQUE(run_id,call_number))""",
        "CREATE INDEX IF NOT EXISTS ix_evaluation_runs ON t_evaluation_run(stock_code,report_year,created_at,id)",
        "CREATE INDEX IF NOT EXISTS ix_model_call_answer ON t_model_call(answer_id,call_number)",
        "INSERT INTO t_document_schema(revision) VALUES(4) ON CONFLICT(revision) DO NOTHING",
    ]:
        db.execute(sql)
    from app.reports.schema import upgrade_reports
    upgrade_reports(db)
