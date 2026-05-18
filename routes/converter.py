"""Conversion routes: upload, status polling, download, feedback."""
import logging
import os
import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from db import get_db_session
from models import FeedbackSubmission, Job
from pdf_utils import PasswordProtectedPDFError, get_pdf_page_count
from storage_utils import copy_file, generate_presigned_url, get_storage_config, upload_file

logger = logging.getLogger(__name__)

converter_bp = Blueprint('converter', __name__)


@converter_bp.route('/dashboard')
def dashboard():
    """Auth-gated converter page."""
    from app import (
        BETA_MODE, ENGLISH_ONLY_BETA, FEEDBACK_RETENTION_DAYS, MAX_PAGES, MAX_UPLOAD_MB,
    )

    if not session.get('user_id'):
        flash('Please sign in to access the dashboard.', 'error')
        return redirect(url_for('auth.signin'))

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
            if is_ajax_request():
                return jsonify({'status': 'error', 'error': 'Please sign in to convert files.'}), 401
            flash('Please sign in to convert files.', 'error')
            return redirect(url_for('auth.signin'))

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
            }
            try:
                os.remove(filepath)
            except Exception:
                pass
        else:
            file_ref = {"type": "local", "path": filepath}

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

        from worker import process_pdf_task
        task = process_pdf_task.delay(file_ref, filename, job_id, api_key, quality)

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
                    storage = get_storage_config()
                    if storage:
                        data['download_url'] = generate_presigned_url(storage, data['download_key'])
                else:
                    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), '..', 'processed'
                    )
                    processed_dir = os.path.join(processed_root, job_id)
                    if os.path.exists(processed_dir):
                        excel_files = [f for f in os.listdir(processed_dir) if f.endswith('.xlsx')]
                        if excel_files:
                            data['download_url'] = url_for('converter.download_result', job_id=job_id, filename=excel_files[0])

            return jsonify(data)
        except Exception as e:
            return jsonify({'status': 'error', 'percent': 0, 'error': str(e)})

    return jsonify({'status': 'processing', 'percent': 0})


@converter_bp.route('/download/<job_id>/<filename>')
def download_result(job_id, filename):
    """Download processed file."""
    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'processed'
    )
    directory = os.path.join(processed_root, job_id)
    filepath = os.path.join(directory, filename)

    if not os.path.exists(filepath):
        flash('File not found. It may have expired.', 'error')
        return redirect(url_for('converter.dashboard'))

    return send_file(filepath, as_attachment=True, download_name=filename)


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
