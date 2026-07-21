"""Parser-free retention configuration and cleanup routines."""
import logging
import os
import shutil
from datetime import datetime, timedelta

from db import get_db_session
from models import FeedbackSubmission, Job
from storage_utils import delete_file, get_storage_config
from tracking import cleanup_tracking_logs

logger = logging.getLogger(__name__)

STORAGE_ROOT = os.environ.get('SHARED_STORAGE_PATH')
UPLOAD_FOLDER = os.path.join(STORAGE_ROOT, 'uploads') if STORAGE_ROOT else 'uploads'
PROCESSED_FOLDER = os.path.join(STORAGE_ROOT, 'processed') if STORAGE_ROOT else 'processed'

FEEDBACK_RETENTION_DAYS = int(os.environ.get('FEEDBACK_RETENTION_DAYS', '30'))
FEEDBACK_RETENTION_SWEEP_MINS = int(os.environ.get('FEEDBACK_RETENTION_SWEEP_MINS', '60'))
FIRST_PARTY_ANALYTICS_RETENTION_DAYS = int(
    os.environ.get('FIRST_PARTY_ANALYTICS_RETENTION_DAYS', '180')
)
FIRST_PARTY_ANALYTICS_SWEEP_MINS = int(
    os.environ.get('FIRST_PARTY_ANALYTICS_SWEEP_MINS', '1440')
)
RESULT_RETENTION_HOURS = int(os.environ.get(
    'RESULT_RETENTION_HOURS',
    os.environ.get('S3_RESULT_RETENTION_HOURS', '24'),
))
S3_RESULT_RETENTION_HOURS = RESULT_RETENTION_HOURS
LOCAL_RESULT_RETENTION_HOURS = int(os.environ.get(
    'LOCAL_RESULT_RETENTION_HOURS',
    str(RESULT_RETENTION_HOURS),
))
ACTIVE_UPLOAD_GRACE_HOURS = int(os.environ.get('ACTIVE_UPLOAD_GRACE_HOURS', '24'))

_last_feedback_retention_sweep = None
_last_analytics_retention_sweep = None
_last_s3_cleanup_sweep = None


def cleanup_old_files():
    """Clean up stale local uploads and generated result files."""
    try:
        active_cutoff = datetime.utcnow() - timedelta(hours=ACTIVE_UPLOAD_GRACE_HOURS)
        with get_db_session() as db:
            active_job_ids = {
                job_id
                for (job_id,) in (
                    db.query(Job.id)
                    .filter(Job.status.in_(('queued', 'processing')))
                    .filter(Job.created_at >= active_cutoff)
                    .all()
                )
            }
        current_time = datetime.now().timestamp()
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            job_id = filename.split('_', 1)[0]
            if job_id in active_job_ids:
                continue
            if os.path.isfile(filepath) and current_time - os.path.getmtime(filepath) > 3600:
                os.remove(filepath)
    except Exception as exc:
        # Fail safe: if job state cannot be checked, do not risk deleting an
        # input that a long-running conversion is still reading.
        logger.warning("Local upload cleanup failed: %s", exc)

    try:
        if LOCAL_RESULT_RETENTION_HOURS <= 0 or not os.path.exists(PROCESSED_FOLDER):
            return

        cutoff_seconds = LOCAL_RESULT_RETENTION_HOURS * 3600
        current_time = datetime.now().timestamp()
        for name in os.listdir(PROCESSED_FOLDER):
            path = os.path.join(PROCESSED_FOLDER, name)
            if current_time - os.path.getmtime(path) <= cutoff_seconds:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.isfile(path):
                os.remove(path)
    except Exception as exc:
        logger.warning("Local result cleanup failed: %s", exc)


def cleanup_feedback_shared_pdfs(force: bool = False):
    """Delete user-consented shared PDFs after the retention period."""
    global _last_feedback_retention_sweep

    if FEEDBACK_RETENTION_DAYS <= 0:
        return

    now = datetime.utcnow()
    if (
        not force
        and _last_feedback_retention_sweep
        and now - _last_feedback_retention_sweep
        < timedelta(minutes=FEEDBACK_RETENTION_SWEEP_MINS)
    ):
        return

    storage = get_storage_config()
    if not storage:
        _last_feedback_retention_sweep = now
        return

    cutoff = now - timedelta(days=FEEDBACK_RETENTION_DAYS)
    try:
        with get_db_session() as db:
            stale = (
                db.query(FeedbackSubmission)
                .filter(FeedbackSubmission.pdf_shared.is_(True))
                .filter(FeedbackSubmission.pdf_storage_key.isnot(None))
                .filter(FeedbackSubmission.created_at < cutoff)
                .limit(200)
                .all()
            )
            for item in stale:
                key = item.pdf_storage_key
                if key:
                    try:
                        delete_file(storage, key)
                    except Exception as exc:
                        logger.warning("Feedback retention delete failed for %s: %s", key, exc)
                        continue
                item.pdf_storage_key = None
                if item.status == 'new':
                    item.status = 'expired'
    except Exception as exc:
        logger.warning("Feedback retention sweep failed: %s", exc)
    finally:
        _last_feedback_retention_sweep = now


def cleanup_first_party_analytics(force: bool = False):
    """Delete old first-party analytics and login-event rows."""
    global _last_analytics_retention_sweep

    if FIRST_PARTY_ANALYTICS_RETENTION_DAYS <= 0:
        return

    now = datetime.utcnow()
    if (
        not force
        and _last_analytics_retention_sweep
        and now - _last_analytics_retention_sweep
        < timedelta(minutes=FIRST_PARTY_ANALYTICS_SWEEP_MINS)
    ):
        return

    try:
        deleted = cleanup_tracking_logs(FIRST_PARTY_ANALYTICS_RETENTION_DAYS)
        if deleted.get('site_visits') or deleted.get('login_events') or deleted.get('funnel_events'):
            logger.info(
                "Analytics retention sweep deleted site_visits=%s login_events=%s funnel_events=%s",
                deleted.get('site_visits', 0),
                deleted.get('login_events', 0),
                deleted.get('funnel_events', 0),
            )
    except Exception as exc:
        logger.warning("Analytics retention sweep failed: %s", exc)
    finally:
        _last_analytics_retention_sweep = now


def cleanup_expired_s3_results(force: bool = False):
    """Delete S3 job files older than the retention window."""
    global _last_s3_cleanup_sweep

    if S3_RESULT_RETENTION_HOURS <= 0:
        return

    now = datetime.utcnow()
    if (
        not force
        and _last_s3_cleanup_sweep
        and now - _last_s3_cleanup_sweep < timedelta(hours=1)
    ):
        return

    storage = get_storage_config()
    if not storage:
        _last_s3_cleanup_sweep = now
        return

    cutoff = now - timedelta(hours=S3_RESULT_RETENTION_HOURS)
    inactive_cutoff = now - timedelta(hours=ACTIVE_UPLOAD_GRACE_HOURS)
    try:
        with get_db_session() as db:
            stale_jobs = (
                db.query(Job)
                .filter(
                    (Job.finished_at < cutoff)
                    | (
                        Job.finished_at.is_(None)
                        & (Job.created_at < inactive_cutoff)
                    )
                )
                .filter(
                    Job.input_storage_key.isnot(None)
                    | Job.output_storage_key.isnot(None)
                    | Job.storage_key.isnot(None)
                )
                .limit(100)
                .all()
            )
            for job in stale_jobs:
                candidate_keys = [
                    job.input_storage_key,
                    job.output_storage_key,
                    job.storage_key,
                ]
                for key in dict.fromkeys(value for value in candidate_keys if value):
                    try:
                        delete_file(storage, key)
                    except Exception as exc:
                        logger.warning("S3 cleanup failed for %s: %s", key, exc)
                        continue

                    if key == job.input_storage_key:
                        job.input_storage_key = None
                        job.input_deleted_at = now
                    if key == job.output_storage_key:
                        job.output_storage_key = None
                        job.output_deleted_at = now
                    if key == job.storage_key:
                        job.storage_key = None
    except Exception as exc:
        logger.warning("S3 cleanup sweep failed: %s", exc)
    finally:
        _last_s3_cleanup_sweep = now
