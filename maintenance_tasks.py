"""Lightweight Celery tasks for analytics and scheduled retention."""
import logging

from celery_config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name='maintenance.persist_page_view', ignore_result=True, acks_late=False)
def persist_page_view(**payload):
    from tracking import record_page_view

    record_page_view(**payload)


@celery_app.task(name='maintenance.persist_funnel_event', ignore_result=True, acks_late=False)
def persist_funnel_event(**payload):
    from tracking import record_funnel_event

    record_funnel_event(**payload)


@celery_app.task(name='maintenance.run_retention_sweep', ignore_result=True, acks_late=False)
def run_retention_sweep():
    """Run all retention policies away from customer and crawler requests."""
    from retention import (
        cleanup_expired_s3_results,
        cleanup_feedback_shared_pdfs,
        cleanup_first_party_analytics,
        cleanup_old_files,
    )

    cleanup_old_files()
    cleanup_feedback_shared_pdfs(force=True)
    cleanup_expired_s3_results(force=True)
    cleanup_first_party_analytics(force=True)
