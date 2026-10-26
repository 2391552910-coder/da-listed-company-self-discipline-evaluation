"""Mode filtering happens before pagination; old fixed snapshots remain visible."""
import json
import pytest
from app.documents.common import DomainError, dump, sha
from app.documents.jobs import JobControl
from app.evaluation.service import Evaluations
from test_context_service import ready
from test_evaluation_service import MockProvider, submit


def test_mode_filter_paginates_before_reading_and_preserves_legacy_fixed(ready):
    service = Evaluations(ready.docs, MockProvider())
    fixed = submit(ready, service)
    legacy = submit(ready, service)
    agent = submit(ready, service, mode="dual_agent")
    # Pre-Agent requests have no mode field. Only the persisted mode identity is
    # changed here; no generation, score or fake successful result is introduced.
    with ready.docs.db.session() as db:
        row = db.one("SELECT request_json FROM t_evaluation_run WHERE id=?", (legacy["run_id"],))
        request = json.loads(row["request_json"])
        request.pop("mode", None)
        db.execute("UPDATE t_evaluation_run SET request_json=?,request_sha256=? WHERE id=?",
                   (dump(request), sha(dump(request).encode()), legacy["run_id"]))
    fixed_page = service.list(size=1, mode="dual_fixed")
    next_page = service.list(page=2, size=1, mode="dual_fixed")
    assert fixed_page["total"] == next_page["total"] == 2
    assert {fixed_page["items"][0]["id"], next_page["items"][0]["id"]} == {fixed["run_id"], legacy["run_id"]}
    assert fixed_page["items"][0]["mode"] == next_page["items"][0]["mode"] == "dual_fixed"
    filtered = service.list(stock_code="000001", report_year=2024, status="queued", mode="dual_agent")
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == agent["run_id"]
    assert service.list(report_year=2023, mode="dual_agent")["total"] == 0
    JobControl(ready.docs).cancel(agent["id"])
    assert service.list(status="queued", mode="dual_agent")["total"] == 0
    assert service.list(status="cancelled", mode="dual_agent")["total"] == 1


@pytest.mark.parametrize("mode", ["agent", "", True, ["dual_agent"]])
def test_mode_filter_rejects_unknown_mode(ready, mode):
    with pytest.raises(DomainError) as caught:
        Evaluations(ready.docs, MockProvider()).list(mode=mode)
    assert caught.value.code == "INVALID_EVALUATION_MODE"
