#!/usr/bin/env python3
"""
PDF to Excel Converter Web Application
Supports HDFC and ICICI Bank statements
"""
# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

import os
import tempfile
import uuid
import gc
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# Import parsers
from parsers.hdfc_parser import HDFCParser
from parsers.icici_parser import ICICIParser
from parsers.kvb_parser import KVBParser
from parsers.universal_parser import UniversalBankParser, ProcessingConfig
from storage_utils import get_storage_config, upload_file, generate_presigned_url

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20MB max file size for Railway deployment
app.config['GA_MEASUREMENT_ID'] = os.environ.get('GA_MEASUREMENT_ID', '')  # Google Analytics Measurement ID (e.g., G-XXXXXXXXXX)
app.config['GTM_CONTAINER_ID'] = os.environ.get('GTM_CONTAINER_ID', '')  # Google Tag Manager Container ID (optional)

# Initialize SocketIO with proper configuration for production
socketio = SocketIO(
    app, 
    cors_allowed_origins="*",
    async_mode='eventlet',
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25
)

# Create uploads directory if it doesn't exist
STORAGE_ROOT = os.environ.get('SHARED_STORAGE_PATH')
UPLOAD_FOLDER = os.path.join(STORAGE_ROOT, 'uploads') if STORAGE_ROOT else 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Supported banks and their parsers
SUPPORTED_BANKS = {
    'universal': {
        'name': 'Universal (Any Bank)',
        'parser': UniversalBankParser,
        'description': 'AI-powered parser for any bank statement (uses OpenAI)',
        'is_ai': True
    },
    'hdfc': {
        'name': 'HDFC Bank',
        'parser': HDFCParser,
        'description': 'Image-based PDF statements (OCR processing)'
    },
    'icici': {
        'name': 'ICICI Bank', 
        'parser': ICICIParser,
        'description': 'Text-based PDF statements'
    },
    'kvb': {
        'name': 'Karur Vysya Bank',
        'parser': KVBParser,
        'description': 'Image-based PDF statements (OCR processing)'
    }
}

def allowed_file(filename):
    """Check if uploaded file is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'pdf'

def cleanup_old_files():
    """Clean up old uploaded files"""
    try:
        current_time = datetime.now().timestamp()
        for filename in os.listdir(UPLOAD_FOLDER):
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            if os.path.isfile(filepath):
                # Delete files older than 1 hour
                if current_time - os.path.getctime(filepath) > 3600:
                    os.remove(filepath)
    except Exception:
        pass

def progress_callback(progress_data):
    """Callback function to emit progress updates via WebSocket"""
    socketio.emit('progress_update', progress_data)

@app.route('/')
def home():
    """Home page with upload functionality"""
    cleanup_old_files()
    return render_template('home.html', banks=SUPPORTED_BANKS)

@app.route('/index')
def index():
    """Legacy route - redirect to home"""
    return redirect(url_for('home'))

@app.route('/blogs')
def blogs():
    """Blogs page"""
    return render_template('blogs.html')

@app.route('/pricing')
def pricing():
    """Pricing page"""
    return render_template('pricing.html')

@app.route('/convert', methods=['POST'])
def convert():
    """Handle PDF conversion"""
    try:
        # Validate form data
        if 'pdf_file' not in request.files:
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('home'))

        bank_code = request.form.get('bank', 'universal')
        pdf_file = request.files['pdf_file']
        
        # Validate file
        if pdf_file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('home'))
            
        if not allowed_file(pdf_file.filename):
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('home'))
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        filename = secure_filename(pdf_file.filename)
        
        # Save uploaded file with ABSOLUTE path (critical for Celery worker)
        unique_filename = f"{job_id}_{filename}"
        filepath = os.path.join(os.path.abspath(UPLOAD_FOLDER), unique_filename)
        pdf_file.save(filepath)

        storage = get_storage_config()
        if storage:
            object_key = f"uploads/{job_id}/{filename}"
            upload_file(storage, filepath, object_key)
            file_ref = {"type": "s3", "key": object_key}
            try:
                os.remove(filepath)
            except Exception:
                pass
        else:
            file_ref = {"type": "local", "path": filepath}
        
        # Get optional API key
        api_key = request.form.get('api_key')
        
        # Clear any old status for this job_id (in case it existed)
        try:
            import redis
            redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
            r = redis.StrictRedis.from_url(redis_url)
            r.delete(f"job_status:{job_id}")
        except:
            pass
        
        # Trigger Celery Task
        from worker import process_pdf_task
        task = process_pdf_task.delay(file_ref, filename, job_id, api_key)
        
        # Return JSON response for AJAX handling, or redirect for legacy
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'status': 'accepted',
                'job_id': job_id,
                'task_id': task.id,
                'message': 'Processing started'
            }), 202
            
        # For non-AJAX, existing templates might need update or we show a pending page
        # But per user request, we are moving to Progress Bar approach which implies JS
        return render_template('processing.html', job_id=job_id, filename=filename)
    
    except Exception as e:
        flash(f'Error processing request: {str(e)}', 'error')
        # Clean up uploaded file if it exists
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
        return redirect(url_for('home'))


@app.route('/status/<job_id>')
def job_status(job_id):
    """Check job status (fallback/polling)"""
    import redis
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    r = redis.StrictRedis.from_url(redis_url)
    
    # Check Redis for progress
    status_data = r.get(f"job_status:{job_id}")
    if status_data:
        try:
            import ast
            data = ast.literal_eval(status_data.decode('utf-8'))
            
            # Check for completion to provide download link
            if data.get('percent') >= 100 or data.get('status') == 'Completed successfully':
                if data.get('storage') == 's3' and data.get('download_key'):
                    storage = get_storage_config()
                    if storage:
                        data['download_url'] = generate_presigned_url(storage, data['download_key'])
                else:
                    # Find the actual Excel file in the processed directory
                    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
                        os.path.dirname(os.path.abspath(__file__)), 'processed'
                    )
                    processed_dir = os.path.join(processed_root, job_id)
                    if os.path.exists(processed_dir):
                        excel_files = [f for f in os.listdir(processed_dir) if f.endswith('.xlsx')]
                        if excel_files:
                            data['download_url'] = url_for('download_result', job_id=job_id, filename=excel_files[0])
            
            return jsonify(data)
        except Exception as e:
            return jsonify({'status': 'error', 'percent': 0, 'error': str(e)})
            
    return jsonify({'status': 'processing', 'percent': 0})

@app.route('/download/<job_id>/<filename>')
def download_result(job_id, filename):
    """Download processed file"""
    processed_root = os.environ.get('SHARED_STORAGE_PATH') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'processed'
    )
    directory = os.path.join(processed_root, job_id)
    filepath = os.path.join(directory, filename)
    
    if not os.path.exists(filepath):
        flash('File not found. It may have expired.', 'error')
        return redirect(url_for('home'))
    
    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/health')
def health_check():
    """Health check endpoint for Railway - Simple but accurate"""
    try:
        # Check if uploads directory exists
        if not os.path.exists(UPLOAD_FOLDER):
            return "UPLOADS_DIR_MISSING", 500
        
        # Test parser imports (critical for app functionality)
        try:
            HDFCParser()
            ICICIParser()
        except Exception:
            return "PARSERS_FAILED", 500
        
        # If we get here, everything is working
        return "OK", 200
        
    except Exception as e:
        return f"ERROR: {str(e)}", 500

@app.route('/health/detailed')
def detailed_health():
    """Detailed health check with more info"""
    try:
        # Test basic functionality
        response_data = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'service': 'pdf-excel-converter',
            'version': '1.0.0',
            'checks': {
                'flask': 'OK',
                'uploads_dir': 'OK' if os.path.exists(UPLOAD_FOLDER) else 'MISSING',
                'parsers': 'OK'
            }
        }
        
        # Test parsers import
        try:
            HDFCParser()
            ICICIParser()
            response_data['checks']['parsers'] = 'OK'
        except Exception:
            response_data['checks']['parsers'] = 'ERROR'
            
        return jsonify(response_data), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.errorhandler(413)
def too_large(e):
    flash('File too large. Maximum size is 20MB.', 'error')
    return redirect(url_for('home'))

@app.route('/sitemap.xml')
def sitemap():
    """Generate sitemap.xml for SEO"""
    from flask import Response
    base_url = request.url_root.rstrip('/')
    
    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
    <url>
        <loc>{base_url}/</loc>
        <lastmod>2025-01-27</lastmod>
        <changefreq>weekly</changefreq>
        <priority>1.0</priority>
    </url>
    <url>
        <loc>{base_url}/blogs</loc>
        <lastmod>2025-01-27</lastmod>
        <changefreq>weekly</changefreq>
        <priority>0.8</priority>
    </url>
    <url>
        <loc>{base_url}/pricing</loc>
        <lastmod>2025-01-27</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.9</priority>
    </url>
</urlset>"""
    
    return Response(sitemap_content, mimetype='application/xml')

@app.route('/robots.txt')
def robots():
    """Generate robots.txt for SEO"""
    from flask import Response
    base_url = request.url_root.rstrip('/')
    
    robots_content = f"""User-agent: *
Allow: /
Allow: /blogs
Allow: /pricing
Disallow: /uploads/
Disallow: /processed/
Disallow: /status/
Disallow: /download/

Sitemap: {base_url}/sitemap.xml"""
    
    return Response(robots_content, mimetype='text/plain')

# For deployment with gunicorn, we need to expose the SocketIO app
# This allows gunicorn to use: gunicorn app:socketio

if __name__ == '__main__':
    # For local development
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting Flask app on port {port}")
    socketio.run(app, debug=False, host='0.0.0.0', port=port)
