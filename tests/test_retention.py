#!/usr/bin/env python3
"""Tests for file retention and feedback PDF handling."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from uuid import uuid4


def test_cleanup_expired_s3_job_files(monkeypatch):
    import retention as retention_module
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

    monkeypatch.setattr(retention_module, "S3_RESULT_RETENTION_HOURS", 1)
    monkeypatch.setattr(retention_module, "_last_s3_cleanup_sweep", None)
    monkeypatch.setattr(retention_module, "get_storage_config", lambda: {"bucket": "test"})
    monkeypatch.setattr(retention_module, "delete_file", lambda storage, key: deleted_keys.append(key))

    try:
        retention_module.cleanup_expired_s3_results(force=True)

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


def test_cleanup_expired_s3_input_for_stuck_job(monkeypatch):
    import retention as retention_module
    from db import get_db_session
    from models import Job

    job_id = f"stuck-retention-{uuid4()}"
    input_key = f"uploads/{job_id}/statement.pdf"
    deleted_keys = []
    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="processing",
            created_at=datetime.utcnow() - timedelta(hours=25),
            input_storage_key=input_key,
            storage_key=input_key,
        ))

    monkeypatch.setattr(retention_module, "S3_RESULT_RETENTION_HOURS", 24)
    monkeypatch.setattr(retention_module, "ACTIVE_UPLOAD_GRACE_HOURS", 24)
    monkeypatch.setattr(retention_module, "_last_s3_cleanup_sweep", None)
    monkeypatch.setattr(retention_module, "get_storage_config", lambda: {"bucket": "test"})
    monkeypatch.setattr(retention_module, "delete_file", lambda storage, key: deleted_keys.append(key))

    try:
        retention_module.cleanup_expired_s3_results(force=True)
        assert deleted_keys == [input_key]
        with get_db_session() as db:
            job = db.get(Job, job_id)
            assert job.input_storage_key is None
            assert job.storage_key is None
            assert job.input_deleted_at is not None
    finally:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if job:
                db.delete(job)


def test_local_cleanup_preserves_active_upload_and_removes_orphan(monkeypatch, tmp_path):
    import os
    import retention as retention_module
    from db import get_db_session
    from models import Job

    job_id = str(uuid4())
    active_upload = tmp_path / f"{job_id}_statement.pdf"
    stale_job_id = str(uuid4())
    stale_active_upload = tmp_path / f"{stale_job_id}_stale.pdf"
    orphan_upload = tmp_path / f"{uuid4()}_orphan.pdf"
    active_upload.write_bytes(b"active")
    stale_active_upload.write_bytes(b"stale")
    orphan_upload.write_bytes(b"orphan")
    old_timestamp = (datetime.utcnow() - timedelta(hours=2)).timestamp()
    os.utime(active_upload, (old_timestamp, old_timestamp))
    os.utime(stale_active_upload, (old_timestamp, old_timestamp))
    os.utime(orphan_upload, (old_timestamp, old_timestamp))

    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="processing",
        ))
        db.add(Job(
            id=stale_job_id,
            filename="stale.pdf",
            status="processing",
            created_at=datetime.utcnow() - timedelta(hours=25),
        ))

    monkeypatch.setattr(retention_module, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(retention_module, "LOCAL_RESULT_RETENTION_HOURS", 0)
    try:
        retention_module.cleanup_old_files()
        assert active_upload.exists()
        assert not stale_active_upload.exists()
        assert not orphan_upload.exists()
    finally:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if job:
                db.delete(job)
            stale_job = db.get(Job, stale_job_id)
            if stale_job:
                db.delete(stale_job)


def test_feedback_copies_retained_pdf(monkeypatch):
    import app as app_module
    from db import get_db_session
    from models import FeedbackSubmission, Job
    import routes.converter as converter_module

    job_id = f"feedback-{uuid4()}"
    guest_id = f"guest-{uuid4()}"
    source_key = f"uploads/{job_id}/statement.pdf"
    copied = []

    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
            guest_id=guest_id,
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
            with client.session_transaction() as sess:
                sess["guest_id"] = guest_id
                sess["csrf_token"] = "feedback-token"
            response = client.post("/feedback", data={
                "csrf_token": "feedback-token",
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
    guest_id = f"guest-{uuid4()}"
    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
            guest_id=guest_id,
        ))

    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["guest_id"] = guest_id
                sess["csrf_token"] = "feedback-token"
            response = client.post("/feedback", data={
                "csrf_token": "feedback-token",
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


def test_feedback_rejects_missing_csrf():
    import app as app_module
    from db import get_db_session
    from models import FeedbackSubmission

    message = f"csrf-probe-{uuid4()}"
    app_module.app.config["TESTING"] = True

    with app_module.app.test_client() as client:
        response = client.post("/feedback", data={
            "feedback_type": "other",
            "message": message,
        })

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "INVALID_CSRF"
    with get_db_session() as db:
        assert db.query(FeedbackSubmission).filter_by(message=message).first() is None


def test_feedback_rejects_job_owned_by_another_guest(monkeypatch):
    import app as app_module
    from db import get_db_session
    from models import FeedbackSubmission, Job
    import routes.converter as converter_module

    job_id = f"feedback-forbidden-{uuid4()}"
    owner_guest_id = f"owner-{uuid4()}"
    copied = []
    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
            guest_id=owner_guest_id,
            input_storage_key=f"uploads/{job_id}/statement.pdf",
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
            with client.session_transaction() as sess:
                sess["guest_id"] = f"other-{uuid4()}"
                sess["csrf_token"] = "feedback-token"
            response = client.post("/feedback", data={
                "csrf_token": "feedback-token",
                "job_id": job_id,
                "feedback_type": "incorrect_data",
                "share_pdf": "1",
            })

        assert response.status_code == 404
        assert copied == []
        with get_db_session() as db:
            assert db.query(FeedbackSubmission).filter_by(job_id=job_id).first() is None
    finally:
        with get_db_session() as db:
            for feedback in db.query(FeedbackSubmission).filter_by(job_id=job_id).all():
                db.delete(feedback)
            job = db.get(Job, job_id)
            if job:
                db.delete(job)


def test_feedback_removes_copied_pdf_if_database_insert_fails(monkeypatch):
    import app as app_module
    import routes.converter as converter_module
    from db import get_db_session
    from models import Job

    job_id = f"feedback-db-failure-{uuid4()}"
    guest_id = f"guest-{uuid4()}"
    source_key = f"uploads/{job_id}/statement.pdf"
    copied = []
    deleted = []
    with get_db_session() as db:
        db.add(Job(
            id=job_id,
            filename="statement.pdf",
            status="completed",
            guest_id=guest_id,
            input_storage_key=source_key,
        ))

    call_count = 0

    @contextmanager
    def fail_second_db_session():
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated feedback insert failure")
        with get_db_session() as db:
            yield db

    monkeypatch.setattr(app_module, "rate_limited", lambda *args, **kwargs: False)
    monkeypatch.setattr(converter_module, "get_db_session", fail_second_db_session)
    monkeypatch.setattr(converter_module, "get_storage_config", lambda: {"bucket": "test"})
    monkeypatch.setattr(
        converter_module,
        "copy_file",
        lambda storage, source, destination: copied.append(destination),
    )
    monkeypatch.setattr(
        converter_module,
        "delete_file",
        lambda storage, key: deleted.append(key),
    )

    app_module.app.config["TESTING"] = True
    try:
        with app_module.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["guest_id"] = guest_id
                sess["csrf_token"] = "feedback-token"
            response = client.post("/feedback", data={
                "csrf_token": "feedback-token",
                "job_id": job_id,
                "feedback_type": "incorrect_data",
                "share_pdf": "1",
            })

        assert response.status_code == 500
        assert len(copied) == 1
        assert deleted == copied
    finally:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if job:
                db.delete(job)


def test_dashboard_feedback_requests_include_csrf_token():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        dashboard_response = client.get("/dashboard")
        script_response = client.get("/static/script.js")

    assert dashboard_response.status_code == 200
    assert b'name="csrf_token" id="feedbackCsrfToken" value="' in dashboard_response.data
    assert script_response.status_code == 200
    assert b"formData.append('csrf_token', csrfInput.value)" in script_response.data
