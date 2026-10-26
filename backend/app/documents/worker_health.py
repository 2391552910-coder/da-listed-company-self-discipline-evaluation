"""Live worker registration; no hostname, credentials or filesystem details."""
from datetime import datetime, timedelta, timezone
from app.documents.common import DomainError, now


def record_worker(documents, worker_id, status, job_id=None):
    if status not in {"idle", "busy", "stopped"}:
        raise DomainError("INVALID_WORKER_STATUS", "Worker状态不合法", 422)
    moment = now()
    with documents.db.session() as db:
        db.execute("""INSERT INTO t_worker_health(worker_id,status,started_at,heartbeat_at,job_id)
            VALUES(?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET
            status=excluded.status,heartbeat_at=excluded.heartbeat_at,job_id=excluded.job_id""",
            (worker_id, status, moment, moment, job_id))


def worker_health(documents, max_age_seconds=60):
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 3600:
        raise DomainError("INVALID_HEALTH_WINDOW", "Worker心跳窗口不合法", 422)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
    with documents.db.session() as db:
        registered = db.one("SELECT COUNT(*) AS n FROM t_worker_health")["n"]
        live = db.rows("SELECT status,heartbeat_at FROM t_worker_health WHERE status IN ('idle','busy') AND heartbeat_at>=?", (cutoff,))
    return {"healthy": bool(live), "live_count": len(live), "registered_count": registered,
            "idle_count": sum(r["status"] == "idle" for r in live),
            "busy_count": sum(r["status"] == "busy" for r in live),
            "latest_heartbeat": max((r["heartbeat_at"] for r in live), default=None),
            "max_age_seconds": max_age_seconds}
