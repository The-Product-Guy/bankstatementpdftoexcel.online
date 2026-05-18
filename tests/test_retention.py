#!/usr/bin/env python3
"""Tests for file retention and feedback PDF handling."""
from datetime import datetime, timedelta
from uuid import uuid4


def test_cleanup_expired_s3_job_files(monkeypatch):
    import app as app_module
    from db import get_db_session
    from models import Job

    job_id = f"retention-{uuid4()}"
    deleted_keys = []

    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
            finished_at=datetime.utcnow() - timedelta(hours=3),
            input_storage_key=f"uploads/{job_id}/statement.pdf",
            output_storage_key=f"outputs/{job_id}/statement.xlsx",
            storage_key=f"outputs/{job_id}/statement.xlsx",
        ))

    monkeypatch.setattr(app_module, "S3_RESULT_RETENTION_HOURS", 1)
    monkeypatch.setattr(app_module, "_last_s3_cleanup_sweep", None)
    monkeypatch.setattr(app_module, "get_storage_config", lambda: {"bucket": "test"})
    monkeypatch.setattr(app_module, "delete_file", lambda storage, key: deleted_keys.append(key))

    try:
        app_module.cleanup_expired_s3_results(force=True)

        assert deleted_keys == [
            f"uploads/{job_id}/statement.pdf",
            f"outputs/{job_id}/statement.xlsx",
        ]
        with get_db_session() as db:
            job = db.get(Job, job_id)
            assert job.input_storage_key is None
            assert job.output_storage_key is None
            assert job.storage_key is None
            assert job.input_deleted_at is not None
            assert job.output_deleted_at is not None
    finally:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if job:
                db.delete(job)


def test_feedback_copies_retained_pdf(monkeypatch):
    import app as app_module
    from db import get_db_session
    from models import FeedbackSubmission, Job
    import routes.converter as converter_module

    job_id = f"feedback-{uuid4()}"
    source_key = f"uploads/{job_id}/statement.pdf"
    copied = []

    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
            input_storage_key=source_key,
        ))

    monkeypatch.setattr(converter_module, "get_storage_config", lambda: {"bucket": "test"})
    monkeypatch.setattr(
        converter_module,
        "copy_file",
        lambda storage, src, dest: copied.append((src, dest)),
    )

    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.test_client() as client:
            response = client.post("/feedback", data={
                "job_id": job_id,
                "feedback_type": "incorrect_data",
                "message": "Rows shifted",
                "share_pdf": "1",
            })

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "ok"
        assert copied
        assert copied[0][0] == source_key
        assert copied[0][1].startswith(f"feedback/{job_id}/")

        with get_db_session() as db:
            feedback = (
                db.query(FeedbackSubmission)
                .filter_by(job_id=job_id)
                .order_by(FeedbackSubmission.created_at.desc())
                .first()
            )
            assert feedback is not None
            assert feedback.pdf_shared is True
            assert feedback.pdf_storage_key == copied[0][1]
    finally:
        with get_db_session() as db:
            for feedback in db.query(FeedbackSubmission).filter_by(job_id=job_id).all():
                db.delete(feedback)
            job = db.get(Job, job_id)
            if job:
                db.delete(job)


def test_feedback_accepts_success_signal():
    import app as app_module
    from db import get_db_session
    from models import FeedbackSubmission, Job

    job_id = f"feedback-success-{uuid4()}"
    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
        ))

    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.test_client() as client:
            response = client.post("/feedback", data={
                "job_id": job_id,
                "feedback_type": "success",
                "message": "Output marked accurate",
                "extraction_rows": "12",
                "extraction_cols": "6",
                "quality_used": "standard",
            })

        assert response.status_code == 200
        assert response.get_json()["status"] == "ok"

        with get_db_session() as db:
            feedback = db.query(FeedbackSubmission).filter_by(job_id=job_id).first()
            assert feedback is not None
            assert feedback.feedback_type == "success"
            assert feedback.extraction_rows == 12
            assert feedback.extraction_cols == 6
    finally:
        with get_db_session() as db:
            for feedback in db.query(FeedbackSubmission).filter_by(job_id=job_id).all():
                db.delete(feedback)
            job = db.get(Job, job_id)
            if job:
                db.delete(job)
