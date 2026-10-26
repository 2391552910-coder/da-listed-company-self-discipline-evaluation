"""Additive experiment/Agent and worker-observability schema."""


def upgrade_stage6(db):
    if db.dialect == "sqlite":
        columns = {r["name"] for r in db.rows("PRAGMA table_info(t_model_call)")}
    else:
        columns = {r["column_name"] for r in db.rows(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='t_model_call'")}
    for name, definition in (("purpose", "VARCHAR(32) NOT NULL DEFAULT 'answer'"),
                             ("step_key", "VARCHAR(80)")):
        if name not in columns:
            db.execute(f"ALTER TABLE t_model_call ADD COLUMN {name} {definition}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_model_call_step ON t_model_call(answer_id,step_key) WHERE step_key IS NOT NULL")
    db.execute("""CREATE TABLE IF NOT EXISTS t_worker_health (
        worker_id VARCHAR(36) PRIMARY KEY,status VARCHAR(16) NOT NULL,
        started_at VARCHAR(40) NOT NULL,heartbeat_at VARCHAR(40) NOT NULL,
        job_id VARCHAR(36) REFERENCES t_job(id))""")
    from app.experiments.schema import upgrade_experiments
    upgrade_experiments(db)
    db.execute("INSERT INTO t_document_schema(revision) VALUES(5) ON CONFLICT(revision) DO NOTHING")
