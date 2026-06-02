"""Conversion routes: upload, status polling, download, feedback."""
import logging
import os
import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from db import get_db_session
from models import FeedbackSubmission, Job, User
from pdf_utils import PasswordProtectedPDFError, get_pdf_page_count
from storage_utils import copy_file, generate_presigned_url, get_storage_config, upload_file

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
        from tracking import record_funnel_event

        record_funnel_event(
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

        file_size_bytes = os.path.getsize(filepath)
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
        object_key = None
        if storage:
            object_key = f"uploads/{job_id}/{filename}"
            upload_file(storage, filepath, object_key)
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
            _record_converter_funnel_event('conversion_start', job_id=job_id)
        except Exception as e:
            logger.warning(f"Failed to record job: {e}")

        api_key = request.form.get('api_key')
        quality = request.form.get('quality', 'standard')
        if quality not in ('standard', 'high'):
            quality = 'standard'

        try:
            import redis
            redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
            r = redis.StrictRedis.from_url(redis_url)
            r.delete(f"job_status:{job_id}")
        except Exception:
            pass

        from celery_config import celery_app
        task = celery_app.send_task(
            'worker.process_pdf',
            args=[file_ref, filename, job_id, api_key, quality],
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'status': 'accepted',
                'job_id': job_id,
                'task_id': task.id,
                'message': 'Processing started'
            }), 202

        return render_template('processing.html', job_id=job_id, filename=filename)

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

    if rate_limited(f"rate:status:{get_client_ip()}", int(os.environ.get('RATE_LIMIT_STATUS', '120')), 60):
        return jsonify({'status': 'error', 'percent': 0, 'error': 'Too many status requests.'}), 429

    with get_db_session() as db:
        job = db.get(Job, job_id)
        if not job or not _job_access_allowed(job):
            return jsonify({'status': 'error', 'percent': 0, 'error': 'Job not found.'}), 404
        requires_download_email = _job_requires_download_email(job)
        output_filename = _job_download_filename(job)

    import redis
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    r = redis.StrictRedis.from_url(redis_url)

    status_data = r.get(f"job_status:{job_id}")
    if status_data:
        try:
            import ast
            data = ast.literal_eval(status_data.decode('utf-8'))

            if data.get('percent') >= 100 or data.get('status') == 'Completed successfully':
                if data.get('storage') == 's3' and data.get('download_key'):
                    data['download_url'] = url_for(
                        'converter.download_result',
                        job_id=job_id,
                        filename=os.path.basename(data['download_key'])
                    )
                else:
                    processed_dir = os.path.dirname(_job_local_output_path(job_id, output_filename or 'placeholder.xlsx'))
                    if os.path.exists(processed_dir):
                        excel_files = [f for f in os.listdir(processed_dir) if f.endswith('.xlsx')]
                        if excel_files:
                            data['download_url'] = url_for('converter.download_result', job_id=job_id, filename=excel_files[0])
                data['requires_download_email'] = bool(data.get('download_url') and requires_download_email)

            return jsonify(data)
        except Exception as e:
            return jsonify({'status': 'error', 'percent': 0, 'error': str(e)})

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
        if share_pdf and job_id:
            storage = get_storage_config()
            if storage:
                try:
                    with get_db_session() as db:
                        job = db.get(Job, job_id)
                        if job:
                            source_key = job.input_storage_key
                            if (
                                not source_key
                                and job.storage_key
                                and job.storage_key.startswith("uploads/")
                            ):
                                source_key = job.storage_key
                            if source_key:
                                source_name = os.path.basename(source_key)
                                pdf_storage_key = f"feedback/{job_id}/{uuid.uuid4()}_{source_name}"
                                copy_file(storage, source_key, pdf_storage_key)
                except Exception:
                    logger.warning("Unable to attach retained PDF to feedback", exc_info=True)
                    pass

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
        return jsonify({'status': 'error', 'error': 'Failed to submit feedback.'}), 500
