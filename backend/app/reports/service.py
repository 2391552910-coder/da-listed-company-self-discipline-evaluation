"""Immutable, hash-checked exports of a specified terminal evaluation run."""
import json

from app.documents.common import DomainError, dump, now, sha, uid
from app.documents.service import required
from app.reports.render import render_html, render_pdf

FORMATS = {"html": ("text/html; charset=utf-8", "html"), "pdf": ("application/pdf", "pdf")}


class Reports:
    def __init__(self, documents, evaluations=None, font_file=None):
        self.docs = documents
        self.evaluations = evaluations
        self.font_file = font_file

    def _evaluation(self, run_id):
        if self.evaluations is None:
            from app.evaluation.service import Evaluations
            return Evaluations(self.docs).get(run_id)
        return self.evaluations.get(run_id)

    def create(self, evaluation_id, format="html", key=None):
        if format not in FORMATS:
            raise DomainError("INVALID_REPORT_FORMAT", "报告格式仅支持 html/pdf", 422)
        if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > 200):
            raise DomainError("INVALID_IDEMPOTENCY_KEY", "幂等键为空或过长", 422)
        # Renderer upgrades must not break a replay of the same public request.
        # The actual renderer identity remains frozen in each export manifest.
        request_sha = sha(dump({"evaluation_id": evaluation_id, "format": format}).encode("utf-8"))
        if key:
            with self.docs.db.session() as db:
                existing = db.one("SELECT * FROM t_report_export WHERE idempotency_key=?", (key,))
            if existing:
                return self._same_request(existing, request_sha)
        evaluation = self._evaluation(evaluation_id)
        answers = (evaluation.get("snapshot") or {}).get("answers") or []
        if evaluation.get("status") not in {"succeeded", "partial"} or not any(
                isinstance(answer.get("answer_text"), str) and answer["answer_text"].strip() for answer in answers):
            raise DomainError("REPORT_NOT_READY", "仅可导出已有正文的完成或部分完成运行", 409)
        # Copy by canonical serialization. Subsequent document/model changes never
        # affect this snapshot or the stored export, including an old partial run.
        snapshot = dump(evaluation).encode("utf-8")
        snapshot_sha, export_id = sha(snapshot), uid()
        frozen = json.loads(snapshot)
        data, manifest = (render_html(frozen, snapshot) if format == "html" else
                          render_pdf(frozen, snapshot, self.font_file))
        file_sha = sha(data)
        # UUID content directories keep Windows paths below MAX_PATH.
        snapshot_key = "exports/" + export_id + "/snapshot.json"
        file_key = "exports/" + export_id + "/report." + format
        self.docs.storage.write(snapshot_key, snapshot)
        self.docs.storage.write(file_key, data)
        created_at = now()
        try:
            with self.docs.db.session() as db:
                if key:
                    existing = db.one("SELECT * FROM t_report_export WHERE idempotency_key=?", (key,))
                    if existing:
                        return self._same_request(existing, request_sha)
                db.execute("""INSERT INTO t_report_export(id,evaluation_id,format,request_sha256,idempotency_key,
                    snapshot_key,snapshot_sha256,file_key,file_sha256,size_bytes,manifest_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (export_id, evaluation_id, format, request_sha, key, snapshot_key,
                            snapshot_sha, file_key, file_sha, len(data), dump(manifest), created_at))
                return self._public(required(db.one("SELECT * FROM t_report_export WHERE id=?", (export_id,))))
        except DomainError:
            raise
        except Exception as error:
            sqlstate = getattr(error, "sqlstate", None) or getattr(getattr(error, "orig", None), "sqlstate", None)
            unique = sqlstate == "23505" or (error.__class__.__name__ == "IntegrityError" and "UNIQUE" in str(error).upper())
            if key and unique:
                with self.docs.db.session() as db:
                    existing = db.one("SELECT * FROM t_report_export WHERE idempotency_key=?", (key,))
                if existing:
                    return self._same_request(existing, request_sha)
            raise

    def _same_request(self, row, request_sha):
        if row["request_sha256"] != request_sha:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于另一报告请求", 409)
        # A replay must verify durable artifacts, never silently regenerate them.
        self.docs.storage.read(row["snapshot_key"], row["snapshot_sha256"])
        self.docs.storage.read(row["file_key"], row["file_sha256"])
        return self._public(row)

    @staticmethod
    def _public(row):
        return {key: row[key] for key in ("id", "evaluation_id", "format", "snapshot_sha256", "file_sha256", "size_bytes", "created_at")} | {
            "status": "succeeded", "media_type": FORMATS[row["format"]][0],
            "filename": "evaluation-" + row["evaluation_id"] + "." + row["format"],
            "manifest": json.loads(row["manifest_json"]),
        }

    def get(self, export_id):
        with self.docs.db.session() as db:
            row = required(db.one("SELECT * FROM t_report_export WHERE id=?", (export_id,)))
        return self._public(row)

    def list(self, evaluation_id, page=1, page_size=20):
        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise DomainError("INVALID_PAGINATION", "分页参数无效", 422)
        # Distinguish a nonexistent evaluation from an existing run with no exports.
        with self.docs.db.session() as db:
            required(db.one("SELECT id FROM t_evaluation_run WHERE id=?", (evaluation_id,)))
            count = db.one("SELECT COUNT(*) AS total FROM t_report_export WHERE evaluation_id=?", (evaluation_id,))["total"]
            rows = db.rows("SELECT * FROM t_report_export WHERE evaluation_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                           (evaluation_id, page_size, (page - 1) * page_size))
        return {"items": [self._public(row) for row in rows], "total": count, "page": page, "page_size": page_size}

    def download(self, export_id):
        with self.docs.db.session() as db:
            row = required(db.one("SELECT * FROM t_report_export WHERE id=?", (export_id,)))
        self.docs.storage.read(row["snapshot_key"], row["snapshot_sha256"])
        data = self.docs.storage.read(row["file_key"], row["file_sha256"])
        return data, self._public(row)

