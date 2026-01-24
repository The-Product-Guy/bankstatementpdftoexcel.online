
# Load environment variables FIRST before anything else
from dotenv import load_dotenv
load_dotenv()

import os
import tempfile
import redis
from celery_config import celery_app
from parsers.universal_parser import create_universal_parser
from storage_utils import get_storage_config, download_file, upload_file
import logging
import pandas as pd

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress verbose HTTP logs from OpenAI SDK
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# Redis for progress updates (separate from Celery broker if needed, but usually same)
redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
redis_client = redis.StrictRedis.from_url(redis_url)

def update_progress(job_id, current, total, status_message, percent_override=None, extra=None):
    """Publish progress update to Redis channel"""
    try:
        if percent_override is not None:
            percent = max(0, min(100, int(percent_override)))
        elif total > 0:
            percent = int((current / total) * 100)
        else:
            percent = 0
            
        message = {
            'job_id': job_id,
            'current': current,
            'total': total,
            'percent': percent,
            'status': status_message
        }
        if extra:
            message.update(extra)
        
        # Publish to job-specific channel
        channel = f"job_progress:{job_id}"
        redis_client.publish(channel, str(message))
        # Also set a key for polling fallback
        redis_client.setex(f"job_status:{job_id}", 3600, str(message))
        
    except Exception as e:
        logger.error(f"Failed to update progress: {str(e)}")

@celery_app.task(bind=True, name='worker.process_pdf')
def process_pdf_task(self, file_ref, original_filename, job_id, api_key=None):
    """
    Celery task to process a PDF file.
    """
    logger.info(f"Starting job {job_id} for file {original_filename}")
    
    # Set API key if provided (important for worker environment)
    if api_key:
        os.environ['OPENAI_API_KEY'] = api_key
    
    temp_dir = None
    local_input_path = None
    try:
        storage = get_storage_config()
        if isinstance(file_ref, dict):
            ref_type = file_ref.get("type")
            if ref_type == "s3":
                if not storage:
                    raise RuntimeError("Storage is not configured for S3 input.")
                temp_dir = tempfile.mkdtemp(prefix=f"job_{job_id}_")
                local_input_path = os.path.join(temp_dir, original_filename)
                download_file(storage, file_ref.get("key"), local_input_path)
                file_path = local_input_path
            elif ref_type == "local":
                file_path = file_ref.get("path")
            else:
                raise RuntimeError(f"Unsupported file_ref type: {ref_type}")
        else:
            file_path = file_ref

        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        # Define progress callback wrapper
        def progress_callback(data):
            current_page = data.get('current_page') or data.get('page_num') or data.get('current') or 0
            total_pages = data.get('total_pages') or data.get('total') or 0
            status = data.get('status', 'Processing...')
            stage = data.get('stage')

            percent_override = None
            if stage and total_pages > 0:
                if stage == 'text_extract':
                    start, end = 0, 40
                elif stage == 'ocr':
                    start, end = 0, 60
                elif stage == 'llm_text':
                    start, end = 40, 95
                elif stage == 'llm_ocr':
                    start, end = 60, 95
                elif stage == 'excel':
                    start, end = 95, 100
                else:
                    start = end = None

                if start is not None:
                    ratio = min(max(current_page / total_pages, 0), 1)
                    percent_override = start + (end - start) * ratio

            update_progress(job_id, current_page, total_pages, status, percent_override=percent_override)

        # Create parser instance
        def env_bool(name, default=False):
            value = os.environ.get(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        parser = create_universal_parser(
            progress_callback=progress_callback,
            use_llm=False,
            use_table_structure=env_bool("USE_TABLE_STRUCTURE", True),
            min_table_transactions=int(os.environ.get("MIN_TABLE_TRANSACTIONS", "5"))
        )
        
        # Determine output path using absolute paths
        processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'processed'
        )
        output_dir = os.path.join(processed_root, job_id)
        os.makedirs(output_dir, exist_ok=True)
        
        update_progress(job_id, 0, 100, "Starting extraction...")
        
        # Run parsing
        transactions = parser.parse(file_path, original_filename)
        
        update_progress(job_id, 0, 1, "Generating Excel file...", percent_override=95)
        # Generate Excel file from transactions
        excel_filename = f"{os.path.splitext(original_filename)[0]}_extracted.xlsx"
        excel_path = os.path.join(output_dir, excel_filename)
        
        raw_table = getattr(parser, "raw_table", None)

        if raw_table:
            raw_df = pd.DataFrame(raw_table["rows"], columns=raw_table["columns"])
            raw_df.to_excel(excel_path, index=False)
            if storage:
                output_key = f"outputs/{job_id}/{excel_filename}"
                upload_file(storage, excel_path, output_key)
                update_progress(
                    job_id,
                    100,
                    100,
                    "Completed successfully",
                    percent_override=100,
                    extra={"storage": "s3", "download_key": output_key}
                )
            else:
                update_progress(job_id, 100, 100, "Completed successfully", percent_override=100)
        else:
            # Create empty Excel if no raw table was extracted
            pd.DataFrame().to_excel(excel_path, index=False)
            if storage:
                output_key = f"outputs/{job_id}/{excel_filename}"
                upload_file(storage, excel_path, output_key)
                update_progress(
                    job_id,
                    100,
                    100,
                    "Completed - no data extracted",
                    percent_override=100,
                    extra={"storage": "s3", "download_key": output_key}
                )
            else:
                update_progress(job_id, 100, 100, "Completed - no data extracted", percent_override=100)
        
        return {
            'status': 'success',
            'job_id': job_id,
            'excel_path': excel_path,
            'transaction_count': len(transactions),
            'filename': excel_filename
        }
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {str(e)}")
        update_progress(job_id, 0, 0, f"Error: {str(e)}")
        return {
            'status': 'error',
            'job_id': job_id,
            'error': str(e)
        }
    finally:
        if local_input_path and os.path.exists(local_input_path):
            try:
                os.remove(local_input_path)
            except Exception:
                pass
        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass
