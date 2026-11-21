from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models import AnalysisJob, Answer
from app.schemas import CanonicalAnswer
from app.services import analysis
from app.services.ingest import upsert_answer
from app.services.llm import LLMError, MockProvider


def _answer(db, external_id="reply-1"):
    canonical = CanonicalAnswer(
        external_answer_id=external_id,
        intern_external_id="intern-1",
        period="2026-03",
        respondent_type="intern",
        scores={"overall": 5},
        comment="Задач слишком много.",
    )
    answer, _ = upsert_answer(db, canonical)
    db.commit()
    return answer


def test_expired_analysis_is_reclaimed_and_its_old_result_is_discarded(db, monkeypatch):
    answer = _answer(db)
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(analysis, "utcnow", lambda: now)
    with SessionLocal() as old_job, SessionLocal() as new_job:
        lost = analysis._claim_answer(old_job, answer.id)
        assert lost is not None
        assert analysis._claim_answer(new_job, answer.id) is None

        now += analysis._lease_duration() + timedelta(seconds=1)
        lease = analysis._claim_answer(new_job, answer.id)
        assert lease and lease != lost
        unrelated = MockProvider().analyze("Всё отлично.", "intern")
        assert analysis._finish_claim(old_job, answer.id, lost, unrelated, model="mock") is None

        result = MockProvider().analyze(answer.comment, "intern")
        final = analysis._finish_claim(new_job, answer.id, lease, result, model="mock")
        assert final is not None
        assert final.problem_topics and final.problem_topics == result.result.problem_topics
    db.refresh(answer)
    assert answer.analysis_status == "done"
    assert answer.comment == "Задач слишком много."


def test_worker_retries_temporary_failure_with_backoff_and_a_limit(client, db, monkeypatch):
    answer = _answer(db)
    answer_id = answer.id
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(analysis, "utcnow", lambda: now)
    calls = 0

    class Unavailable(MockProvider):
        def analyze(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise LLMError("private provider diagnostic", retryable=True)

    provider = Unavailable()
    for attempt in range(1, analysis.MAX_JOB_ATTEMPTS + 1):
        assert analysis.analyze_pending(db, provider=provider) == 1
        assert calls == attempt
        assert analysis.analyze_pending(db, provider=provider) == 0
        now += timedelta(minutes=1)

    failed = db.get(Answer, answer_id, populate_existing=True)
    assert failed.analysis_status == "failed"
    assert failed.comment == "Задач слишком много."
    assert "private provider diagnostic" not in failed.analysis_error
    assert db.get(AnalysisJob, answer_id).attempts == analysis.MAX_JOB_ATTEMPTS
    queue = client.get("/api/analysis/queue").json()
    assert queue["failed"] == 1 and queue["pending"] == 0 and queue["latest_failed_at"]

    response = client.post(f"/api/interns/{failed.intern_id}/answers/{answer_id}/retry")
    assert response.status_code == 200
    assert response.json()["queued"] == 1
    state = db.get(Answer, answer_id, populate_existing=True)
    assert state.analysis_status == "done"


def test_worker_recovers_processing_row_from_an_older_version(db):
    answer = _answer(db)
    answer_id = answer.id
    answer.analysis_status = "processing"
    db.commit()
    assert db.get(AnalysisJob, answer_id) is None
    assert analysis.analyze_pending(db, provider=MockProvider()) == 1
    recovered = db.get(Answer, answer_id, populate_existing=True)
    assert recovered.analysis_status == "done"
    assert recovered.analysis is not None
