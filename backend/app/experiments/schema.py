"""Additive experiment ledger; historical records never become evaluation runs."""


def upgrade_experiments(db):
    for sql in [
        """CREATE TABLE IF NOT EXISTS t_experiment (
        id VARCHAR(36) PRIMARY KEY,name VARCHAR(200) NOT NULL,request_json TEXT NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,protocol_json TEXT NOT NULL,protocol_sha256 VARCHAR(64) NOT NULL,
        idempotency_key VARCHAR(200) UNIQUE,cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at VARCHAR(40) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS t_experiment_item (
        id VARCHAR(36) PRIMARY KEY,experiment_id VARCHAR(36) NOT NULL REFERENCES t_experiment(id),
        case_key VARCHAR(100) NOT NULL,mode VARCHAR(32) NOT NULL,origin VARCHAR(24) NOT NULL,
        status VARCHAR(24) NOT NULL,request_json TEXT,run_id VARCHAR(36) REFERENCES t_evaluation_run(id),
        error_code TEXT,label TEXT NOT NULL DEFAULT '',created_at VARCHAR(40) NOT NULL,
        UNIQUE(experiment_id,case_key,mode),UNIQUE(experiment_id,run_id))""",
        """CREATE TABLE IF NOT EXISTS t_experiment_rating (
        id VARCHAR(36) PRIMARY KEY,experiment_id VARCHAR(36) NOT NULL REFERENCES t_experiment(id),
        run_id VARCHAR(36) NOT NULL REFERENCES t_evaluation_run(id),
        answer_id VARCHAR(36) NOT NULL REFERENCES t_evaluation_answer(id),
        answer_snapshot_sha256 VARCHAR(64) NOT NULL,source_kind VARCHAR(20) NOT NULL,
        rubric_version VARCHAR(80) NOT NULL,values_json TEXT NOT NULL,reviewer VARCHAR(200) NOT NULL,
        method_version VARCHAR(200) NOT NULL,comment TEXT NOT NULL,
        supersedes_score_id VARCHAR(36) REFERENCES t_experiment_rating(id),
        request_sha256 VARCHAR(64) NOT NULL,idempotency_key VARCHAR(200) UNIQUE,
        created_at VARCHAR(40) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS t_experiment_history (
        id VARCHAR(36) PRIMARY KEY,experiment_id VARCHAR(36) NOT NULL REFERENCES t_experiment(id),
        label VARCHAR(200) NOT NULL,source_identity TEXT NOT NULL,records_json TEXT NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,idempotency_key VARCHAR(200) UNIQUE,
        created_at VARCHAR(40) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS t_experiment_export (
        id VARCHAR(36) PRIMARY KEY,experiment_id VARCHAR(36) NOT NULL REFERENCES t_experiment(id),
        format VARCHAR(8) NOT NULL,snapshot_key TEXT NOT NULL,snapshot_sha256 VARCHAR(64) NOT NULL,
        file_key TEXT NOT NULL,file_sha256 VARCHAR(64) NOT NULL,created_at VARCHAR(40) NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS ix_experiment_items ON t_experiment_item(experiment_id,created_at,id)",
        "CREATE INDEX IF NOT EXISTS ix_experiment_ratings ON t_experiment_rating(experiment_id,run_id,created_at,id)",
    ]:
        db.execute(sql)
