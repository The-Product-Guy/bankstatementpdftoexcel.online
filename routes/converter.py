"""Conversion routes: upload, status polling, download, feedback."""
import json
import logging
import os
import uuid
from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from db import get_db_session
from models import FeedbackSubmission, Job, User
from pdf_utils import PasswordProtectedPDFError, get_pdf_page_count
from redis_utils import get_redis_client
from storage_utils import copy_file, delete_file, generate_presigned_url, get_storage_config, upload_file

logger = logging.getLogger(__name__)

converter_bp = Blueprint('converter', __name__)


def _record_converter_funnel_event(
    event_type: str,
    job_id: str = "",
    extra: str = "",
    email: str = "",
) -> None:
    try:
        from app import get_client_ip
        from tracking import enqueue_funnel_event

        enqueue_funnel_event(
            event_type=event_type,
            visitor_id=request.cookies.get('sf_visitor_id'),
            user_id=session.get('user_id'),
            guest_id=session.get('guest_id'),
            job_id=job_id or None,
            email=email or None,
            path=request.path,
            extra=extra,
            ip=get_client_ip(),
            user_agent=request.headers.get('User-Agent', ''),
        )
    except Exception:
        pass


def _captured_download_emails() -> dict:
    captured = session.get('download_email_jobs')
    if not isinstance(captured, dict):
        captured = {}
        session['download_email_jobs'] = captured
    return captured


def _download_email_captured(job_id: str) -> bool:
    return bool(_captured_download_emails().get(job_id))


def _mark_download_email_captured(job_id: str, email: str) -> None:
    captured = _captured_download_emails()
    captured[job_id] = email
    session['download_email_jobs'] = captured
    session.modified = True


def _job_access_allowed(job: Job) -> bool:
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')
    if user_id and job.user_id == user_id:
        return True
    if guest_id and job.guest_id == guest_id:
        return True
    return False


def _job_requires_download_email(job: Job) -> bool:
    return not session.get('user_id') and not _download_email_captured(job.id)


def _job_download_filename(job: Job, fallback: str = "") -> str:
    if job.output_storage_key:
        return os.path.basename(job.output_storage_key)
    if job.storage_key and job.storage_key.startswith("outputs/"):
        return os.path.basename(job.storage_key)
    return os.path.basename(fallback or "")


def _job_local_output_path(job_id: str, filename: str) -> str:
    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'processed'
    )
    return os.path.join(processed_root, job_id, filename)


def _file_too_large_response(max_upload_mb: int):
    """Return the same structured limit response used by Flask's body cap."""
    from app import is_ajax_request

    message = f'File too large. Maximum allowed size is {max_upload_mb} MB.'
    if is_ajax_request():
        return jsonify({
            'status': 'error',
            'error': message,
            'error_code': 'FILE_TOO_LARGE',
            'max_mb': max_upload_mb,
        }), 413
    flash(message, 'error')
    return redirect(url_for('converter.dashboard'))


def _saved_file_size(filepath: str) -> int:
    return os.path.getsize(filepath)


def _exceeds_upload_limit(file_size_bytes: int, max_upload_mb: int) -> bool:
    return file_size_bytes > max_upload_mb * 1024 * 1024


def _discard_conversion_input(storage, object_key: str, filepath: str) -> bool:
    """Best-effort removal when a conversion cannot be durably queued."""
    remote_deleted = True
    if storage and object_key:
        try:
            delete_file(storage, object_key)
        except Exception:
            remote_deleted = False
            logger.warning("Unable to remove unqueued S3 input %s", object_key, exc_info=True)
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            logger.warning("Unable to remove unqueued local input %s", filepath, exc_info=True)
    return remote_deleted


def _mark_unqueued_job_failed(job_id: str, object_key: str, input_deleted: bool) -> None:
    try:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if not job:
                return
            job.status = 'failed'
            job.error = 'Unable to queue conversion.'
            job.finished_at = datetime.utcnow()
            if input_deleted:
                job.input_storage_key = None
                job.input_deleted_at = datetime.utcnow()
                if job.storage_key == object_key:
                    job.storage_key = None
    except Exception:
        logger.warning("Unable to mark unqueued job %s failed", job_id, exc_info=True)


def _conversion_unavailable_response(is_ajax: bool):
    message = 'Conversion service is temporarily unavailable. Please try again.'
    if is_ajax:
        return jsonify({
            'status': 'error',
            'error': message,
            'error_code': 'CONVERSION_UNAVAILABLE',
        }), 503
    flash(message, 'error')
    return redirect(url_for('converter.dashboard'))


def _attach_download_status(
    data: dict,
    job_id: str,
    output_filename: str,
    requires_download_email: bool,
) -> dict:
    if data.get('storage') == 's3' and data.get('download_key'):
        data['download_url'] = url_for(
            'converter.download_result',
            job_id=job_id,
            filename=os.path.basename(data['download_key']),
        )
    else:
        processed_dir = os.path.dirname(
            _job_local_output_path(job_id, output_filename or 'placeholder.xlsx')
        )
        if os.path.exists(processed_dir):
            excel_files = [name for name in os.listdir(processed_dir) if name.endswith('.xlsx')]
            if excel_files:
                data['download_url'] = url_for(
                    'converter.download_result',
                    job_id=job_id,
                    filename=excel_files[0],
                )
    data['requires_download_email'] = bool(
        data.get('download_url') and requires_download_email
    )
    return data


@converter_bp.route('/dashboard')
def dashboard():
    """Converter page. Guests can upload; email is requested before download."""
    from app import (
        BETA_MODE, ENGLISH_ONLY_BETA, FEEDBACK_RETENTION_DAYS, MAX_PAGES, MAX_UPLOAD_MB,
    )

    _record_converter_funnel_event('dashboard_visit')
    return render_template(
        'dashboard.html',
        max_upload_mb=MAX_UPLOAD_MB,
        max_pages=MAX_PAGES,
        beta_mode=BETA_MODE,
        english_only_beta=ENGLISH_ONLY_BETA,
        feedback_retention_days=FEEDBACK_RETENTION_DAYS,
    )


@converter_bp.route('/convert/preflight', methods=['POST'])
def convert_preflight():
    """Lightweight validation before uploading a potentially large PDF body."""
    from app import (
        check_conversion_quota, get_client_ip, get_identity, rate_limited,
    )

    if rate_limited(f"rate:convert_preflight:{get_client_ip()}", int(os.environ.get('RATE_LIMIT_CONVERT', '15')), 3600):
        return jsonify({
            'status': 'error',
            'error': 'Too many conversion requests. Please try again later.',
            'error_code': 'RATE_LIMITED',
        }), 429

    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        return jsonify({
            'status': 'error',
            'error': 'Invalid request token. Please refresh and try again.',
            'error_code': 'INVALID_CSRF',
        }), 400

    if not session.get('user_id'):
        get_identity()

    user_id, guest_id = get_identity()
    allowed, quota_error = check_conversion_quota(user_id, guest_id)
    if not allowed:
        return jsonify({'status': 'error', **quota_error}), 403

    return jsonify({'status': 'ok'}), 200


@converter_bp.route('/convert', methods=['POST'])
def convert():
    """Handle PDF conversion."""
    from app import (
        MAX_PAGES, MAX_UPLOAD_MB, UPLOAD_FOLDER,
        check_conversion_quota, get_client_ip, get_identity, is_ajax_request, is_pdf_file, rate_limited,
    )

    try:
        if not session.get('user_id'):
            get_identity()

        user_id, guest_id = get_identity()

        allowed, quota_error = check_conversion_quota(user_id, guest_id)
        if not allowed:
            if is_ajax_request():
                return jsonify({'status': 'error', **quota_error}), 403
            flash(quota_error.get('error', 'Conversion limit reached.'), 'error')
            return redirect(url_for('converter.dashboard'))

        if rate_limited(f"rate:convert:{get_client_ip()}", int(os.environ.get('RATE_LIMIT_CONVERT', '15')), 3600):
            message = 'Too many conversion requests. Please try again later.'
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': message}), 429
            flash(message, 'error')
            return redirect(url_for('converter.dashboard'))

        csrf_token = request.form.get('csrf_token')
        if not csrf_token or csrf_token != session.get('csrf_token'):
            message = 'Invalid request token. Please refresh and try again.'
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': message}), 400
            flash(message, 'error')
            return redirect(url_for('converter.dashboard'))

        if 'pdf_file' not in request.files:
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('converter.dashboard'))

        bank_code = request.form.get('bank', 'universal')
        extraction_mode = (
            request.form.get('extraction_mode')
            or os.environ.get('DEFAULT_EXTRACTION_MODE', 'layout_replica')
            or 'layout_replica'
        ).strip().lower().replace('-', '_')
        if extraction_mode in {'structured', 'structured_transactions', 'transactions'}:
            extraction_mode = 'structured_transactions'
        else:
            extraction_mode = 'layout_replica'
        pdf_file = request.files['pdf_file']

        if pdf_file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('converter.dashboard'))

        if not is_pdf_file(pdf_file):
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('converter.dashboard'))

        job_id = str(uuid.uuid4())
        filename = secure_filename(pdf_file.filename)

        unique_filename = f"{job_id}_{filename}"
        filepath = os.path.join(os.path.abspath(UPLOAD_FOLDER), unique_filename)
        pdf_file.save(filepath)

        file_size_bytes = _saved_file_size(filepath)
        if _exceeds_upload_limit(file_size_bytes, MAX_UPLOAD_MB):
            os.remove(filepath)
            return _file_too_large_response(MAX_UPLOAD_MB)

        page_count = None
        try:
            page_count = get_pdf_page_count(filepath)
        except PasswordProtectedPDFError as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            message = str(e)
            if is_ajax_request():
                return jsonify({
                    'status': 'error',
                    'error': message,
                    'error_code': 'PASSWORD_PROTECTED_PDF',
                }), 400
            flash(message, 'error')
            return redirect(url_for('converter.dashboard'))
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            message = f'Unable to read PDF pages: {str(e)}'
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': message}), 400
            flash(message, 'error')
            return redirect(url_for('converter.dashboard'))

        if page_count and page_count > MAX_PAGES:
            if os.path.exists(filepath):
                os.remove(filepath)
            message = f'File has {page_count} pages. Maximum allowed is {MAX_PAGES}.'
            if is_ajax_request():
                return jsonify({
                    'status': 'error',
                    'error': message,
                    'error_code': 'PAGE_LIMIT_EXCEEDED',
                    'max_pages': MAX_PAGES,
                    'page_count': page_count
                }), 413
            flash(message, 'error')
            return redirect(url_for('converter.dashboard'))

        retain_for_feedback = request.form.get('retain_input_pdf', '').lower() in {'1', 'true', 'yes', 'on'}
        storage = get_storage_config()
        object_key = f"uploads/{job_id}/{filename}" if storage else None

        # Persist ownership and the planned storage key before writing to S3.
        # If the process dies during upload, retention still has a durable key
        # to clean after the active-job grace period.
        try:
            with get_db_session() as db:
                job = Job(
                    id=job_id,
                    user_id=user_id,
                    guest_id=guest_id,
                    filename=filename,
                    file_size_bytes=file_size_bytes,
                    page_count=page_count,
                    status='queued',
                    input_storage_key=object_key if storage else None,
                    storage_key=object_key if storage else None,
                    ip=get_client_ip(),
                    user_agent=request.headers.get('User-Agent', '')[:512]
                )
                db.add(job)
        except Exception as e:
            logger.warning(f"Failed to record job: {e}")
            # No remote upload has happened yet, so only the local body exists.
            _discard_conversion_input(None, None, filepath)
            return _conversion_unavailable_response(is_ajax_request())

        if storage:
            try:
                upload_file(storage, filepath, object_key)
            except Exception as exc:
                logger.warning("Unable to upload conversion %s: %s", job_id, exc)
                input_deleted = _discard_conversion_input(storage, object_key, filepath)
                _mark_unqueued_job_failed(job_id, object_key, input_deleted)
                return _conversion_unavailable_response(is_ajax_request())

            file_ref = {
                "type": "s3",
                "key": object_key,
                "retain_for_feedback": retain_for_feedback,
                "extraction_mode": extraction_mode,
            }
            try:
                os.remove(filepath)
            except Exception:
                pass
        else:
            file_ref = {"type": "local", "path": filepath, "extraction_mode": extraction_mode}

        _record_converter_funnel_event('conversion_start', job_id=job_id)

        api_key = request.form.get('api_key')
        quality = request.form.get('quality', 'standard')
        if quality not in ('standard', 'high'):
            quality = 'standard'

        try:
            get_redis_client().delete(
                f"job_status:{job_id}",
                f"job_status_v2:{job_id}",
            )
        except Exception:
            pass

        try:
            from celery_config import celery_app
            task = celery_app.send_task(
                'worker.process_pdf',
                args=[file_ref, filename, job_id, api_key, quality],
            )
        except Exception as exc:
            logger.warning("Unable to enqueue conversion %s: %s", job_id, exc)
            input_deleted = _discard_conversion_input(storage, object_key, filepath)
            _mark_unqueued_job_failed(job_id, object_key, input_deleted)
            return _conversion_unavailable_response(is_ajax_request())

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'status': 'accepted',
                'job_id': job_id,
                'task_id': task.id,
                'message': 'Processing started'
            }), 202

        return render_template('processing.html', job_id=job_id, filename=filename)

    except RequestEntityTooLarge:
        # Let Flask's 413 handler return the structured response that opens the
        # splitter modal. The broad route handler must not turn it into a 302.
        raise
    except Exception as e:
        flash(f'Error processing request: {str(e)}', 'error')
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
        return redirect(url_for('converter.dashboard'))


@converter_bp.route('/status/<job_id>')
def job_status(job_id):
    """Check job status (fallback/polling)."""
    from app import get_client_ip, rate_limited

    if rate_limited(
        f"rate:status:{get_client_ip()}",
        int(os.environ.get('RATE_LIMIT_STATUS', '120')),
        60,
        # This read is already ownership-protected. Preserve access to a
        # completed DB-backed result when the Redis limiter is unavailable.
        fail_closed=False,
    ):
        return jsonify({'status': 'error', 'percent': 0, 'error': 'Too many status requests.'}), 429

    with get_db_session() as db:
        job = db.get(Job, job_id)
        if not job or not _job_access_allowed(job):
            return jsonify({'status': 'error', 'percent': 0, 'error': 'Job not found.'}), 404
        requires_download_email = _job_requires_download_email(job)
        output_filename = _job_download_filename(job)
        persisted_status = job.status
        output_storage_key = job.output_storage_key or (
            job.storage_key
            if job.storage_key and job.storage_key.startswith('outputs/')
            else None
        )
        transaction_count = job.transaction_count or 0

    try:
        redis_client = get_redis_client()
        status_data = redis_client.get(f"job_status_v2:{job_id}")
        status_format = 'json'
        if not status_data:
            status_data = redis_client.get(f"job_status:{job_id}")
            status_format = 'legacy'
    except Exception:
        logger.warning("Unable to read job status from Redis", exc_info=True)
        status_data = None
    if status_data:
        try:
            if isinstance(status_data, bytes):
                status_data = status_data.decode('utf-8')
            if status_format == 'json':
                data = json.loads(status_data)
            else:
                # Temporary read compatibility for workers from the prior
                # release. literal_eval does not execute code.
                import ast
                data = ast.literal_eval(status_data)

            redis_status = str(data.get('status') or '')
            redis_is_completed = data.get('percent') >= 100 and not redis_status.startswith(
                ('Error', 'Unsupported language')
            )
            redis_is_failed = redis_status.startswith(('Error', 'Unsupported language'))
            db_is_completed = persisted_status in {'completed', 'completed_no_data'}
            db_is_failed = persisted_status in {'failed', 'unsupported_language'}

            # A terminal database row is authoritative. A stale Redis snapshot
            # must not mask a completion/failure for the remainder of its TTL.
            redis_matches_terminal_db = (
                (db_is_completed and redis_is_completed)
                or (db_is_failed and redis_is_failed)
                or (not db_is_completed and not db_is_failed)
            )
            if redis_matches_terminal_db:
                if redis_is_completed:
                    _attach_download_status(
                        data, job_id, output_filename, requires_download_email
                    )
                return jsonify(data)
        except Exception:
            logger.warning("Invalid job status JSON for %s", job_id, exc_info=True)

    if persisted_status in {'completed', 'completed_no_data'}:
        data = {
            'status': (
                'Completed successfully'
                if persisted_status == 'completed'
                else 'Completed - no data extracted'
            ),
            'percent': 100,
            'state': 'completed',
            'confidence': 'good' if persisted_status == 'completed' else 'empty',
            'extraction_rows': transaction_count,
            'extraction_cols': 0,
        }
        if output_storage_key:
            data.update({'storage': 's3', 'download_key': output_storage_key})
        return jsonify(_attach_download_status(
            data, job_id, output_filename, requires_download_email
        ))
    if persisted_status in {'failed', 'unsupported_language'}:
        return jsonify({
            'status': (
                'Error: Unsupported statement language.'
                if persisted_status == 'unsupported_language'
                else 'Error: Conversion failed.'
            ),
            'percent': 0,
            'state': persisted_status,
        })

    return jsonify({'status': 'processing', 'percent': 0})


@converter_bp.route('/download/<job_id>/<filename>')
def download_result(job_id, filename):
    """Download processed file."""
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        flash('File not found. It may have expired.', 'error')
        return redirect(url_for('converter.dashboard'))

    with get_db_session() as db:
        job = db.get(Job, job_id)
        if not job or not _job_access_allowed(job):
            flash('File not found. It may have expired.', 'error')
            return redirect(url_for('converter.dashboard'))

        if _job_requires_download_email(job):
            _record_converter_funnel_event('download_email_required', job_id=job_id)
            flash('Enter your email to download the converted Excel file.', 'warning')
            return redirect(url_for('converter.dashboard'))

        output_key = job.output_storage_key or (
            job.storage_key if job.storage_key and job.storage_key.startswith("outputs/") else None
        )

    if output_key:
        storage = get_storage_config()
        if not storage:
            flash('File storage is unavailable. Please try again later.', 'error')
            return redirect(url_for('converter.dashboard'))
        _record_converter_funnel_event('download_started', job_id=job_id)
        return redirect(generate_presigned_url(storage, output_key))

    filepath = _job_local_output_path(job_id, safe_filename)

    if not os.path.exists(filepath):
        flash('File not found. It may have expired.', 'error')
        return redirect(url_for('converter.dashboard'))

    _record_converter_funnel_event('download_started', job_id=job_id)
    return send_file(filepath, as_attachment=True, download_name=filename)


@converter_bp.route('/download/email', methods=['POST'])
def capture_download_email():
    """Capture guest email immediately before allowing an Excel download."""
    from app import get_client_ip, is_ajax_request, rate_limited

    if rate_limited(f"rate:download_email:{get_client_ip()}", 10, 3600):
        return jsonify({'status': 'error', 'error': 'Too many download attempts. Please try again later.'}), 429

    job_id = (request.form.get('job_id') or '').strip()
    filename = os.path.basename((request.form.get('filename') or '').strip())
    email = (request.form.get('email') or '').strip().lower()
    csrf_token = request.form.get('csrf_token')

    _record_converter_funnel_event('download_email_submit_attempt', job_id=job_id, email=email)

    if not csrf_token or csrf_token != session.get('csrf_token'):
        return jsonify({'status': 'error', 'error': 'Invalid request token. Please refresh and try again.'}), 400
    if not job_id or not filename:
        return jsonify({'status': 'error', 'error': 'Download is not ready yet. Please try again.'}), 400
    if not email or '@' not in email:
        return jsonify({'status': 'error', 'error': 'Please enter a valid email address.'}), 400

    try:
        with get_db_session() as db:
            job = db.get(Job, job_id)
            if not job or not _job_access_allowed(job):
                return jsonify({'status': 'error', 'error': 'Download is not available.'}), 404
            if not (job.status or '').startswith('completed'):
                return jsonify({'status': 'error', 'error': 'Conversion is not complete yet.'}), 409

            user = db.query(User).filter_by(email=email).first()
            if not user:
                user = User(email=email)
                db.add(user)
                db.flush()

            if not job.user_id:
                job.user_id = user.id

        _mark_download_email_captured(job_id, email)
        _record_converter_funnel_event('download_email_submitted', job_id=job_id, email=email)
        return jsonify({
            'status': 'ok',
            'download_url': url_for('converter.download_result', job_id=job_id, filename=filename)
        }), 200
    except Exception as e:
        logger.warning(f"Download email capture failed: {e}")
        if is_ajax_request():
            return jsonify({'status': 'error', 'error': 'Unable to prepare the download. Please try again.'}), 500
        flash('Unable to prepare the download. Please try again.', 'error')
        return redirect(url_for('converter.dashboard'))


@converter_bp.route('/feedback', methods=['POST'])
def submit_feedback():
    """Accept user feedback for poor or empty results."""
    from app import get_client_ip, get_identity, rate_limited

    if rate_limited(f"rate:feedback:{get_client_ip()}", 10, 3600):
        return jsonify({'status': 'error', 'error': 'Too many feedback submissions.'}), 429

    csrf_token = request.form.get('csrf_token')
    if not csrf_token or csrf_token != session.get('csrf_token'):
        return jsonify({
            'status': 'error',
            'error': 'Invalid request token. Please refresh and try again.',
            'error_code': 'INVALID_CSRF',
        }), 400

    try:
        user_id, guest_id = get_identity()
        job_id = request.form.get('job_id', '').strip()
        feedback_type = request.form.get('feedback_type', 'other').strip()
        message = request.form.get('message', '').strip()[:2000]
        share_pdf = request.form.get('share_pdf', '').lower() in {'1', 'true', 'yes', 'on'}
        quality_used = request.form.get('quality_used', 'standard')
        extraction_rows = int(request.form.get('extraction_rows', 0) or 0)
        extraction_cols = int(request.form.get('extraction_cols', 0) or 0)

        if feedback_type not in ('success', 'empty_result', 'incorrect_data', 'formatting', 'other'):
            feedback_type = 'other'

        pdf_storage_key = None
        if job_id:
            with get_db_session() as db:
                job = db.get(Job, job_id)
                if not job or not _job_access_allowed(job):
                    return jsonify({
                        'status': 'error',
                        'error': 'Feedback is not available for this conversion.',
                    }), 404

                if share_pdf:
                    storage = get_storage_config()
                    source_key = job.input_storage_key
                    if (
                        not source_key
                        and job.storage_key
                        and job.storage_key.startswith("uploads/")
                    ):
                        source_key = job.storage_key

                    if storage and source_key:
                        source_name = os.path.basename(source_key)
                        destination_key = f"feedback/{job_id}/{uuid.uuid4()}_{source_name}"
                        try:
                            copy_file(storage, source_key, destination_key)
                            pdf_storage_key = destination_key
                        except Exception:
                            logger.warning("Unable to attach retained PDF to feedback", exc_info=True)

        with get_db_session() as db:
            fb = FeedbackSubmission(
                job_id=job_id or None,
                user_id=user_id,
                guest_id=guest_id,
                feedback_type=feedback_type,
                message=message,
                pdf_shared=bool(pdf_storage_key),
                pdf_storage_key=pdf_storage_key,
                extraction_rows=extraction_rows,
                extraction_cols=extraction_cols,
                quality_used=quality_used,
                ip=get_client_ip(),
                user_agent=request.headers.get('User-Agent', '')[:512]
            )
            db.add(fb)

        message = 'Thank you for your feedback!'
        if share_pdf and not pdf_storage_key:
            message = 'Feedback saved. The source PDF had already been deleted, so only metadata was submitted.'
        return jsonify({'status': 'ok', 'message': message}), 200

    except Exception as e:
        logger.warning(f"Feedback submission error: {e}")
        if 'pdf_storage_key' in locals() and pdf_storage_key:
            try:
                storage = get_storage_config()
                if storage:
                    delete_file(storage, pdf_storage_key)
            except Exception:
                logger.warning(
                    "Unable to remove orphaned feedback PDF %s",
                    pdf_storage_key,
                    exc_info=True,
                )
        return jsonify({'status': 'error', 'error': 'Failed to submit feedback.'}), 500
