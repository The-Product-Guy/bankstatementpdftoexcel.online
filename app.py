#!/usr/bin/env python3
"""
PDF to Excel Converter Web Application
Supports HDFC and ICICI Bank statements
"""
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

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

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
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Supported banks and their parsers
SUPPORTED_BANKS = {
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
def index():
    """Main upload page"""
    cleanup_old_files()
    return render_template('index.html', banks=SUPPORTED_BANKS)

@app.route('/convert', methods=['POST'])
def convert_pdf():
    """Handle PDF conversion"""
    try:
        # Lazy import heavy dependencies to speed up app startup
        import pandas as pd
        # Validate form data
        if 'bank' not in request.form or 'pdf_file' not in request.files:
            flash('Please select a bank and upload a PDF file.', 'error')
            return redirect(url_for('index'))
        
        bank_code = request.form['bank']
        pdf_file = request.files['pdf_file']
        
        # Validate bank selection
        if bank_code not in SUPPORTED_BANKS:
            flash('Invalid bank selection.', 'error')
            return redirect(url_for('index'))
        
        # Validate file
        if pdf_file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('index'))
        
        if not allowed_file(pdf_file.filename):
            flash('Please upload a PDF file.', 'error')
            return redirect(url_for('index'))
        
        # Save uploaded file
        filename = secure_filename(pdf_file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        pdf_file.save(filepath)
        
        # Process the PDF
        bank_info = SUPPORTED_BANKS[bank_code]
        parser_class = bank_info['parser']
        parser = parser_class(progress_callback)
        
        # Emit initial progress
        socketio.emit('progress_update', {
            'current_page': 0,
            'total_pages': 0,
            'status': 'Starting processing...',
            'percentage': 0
        })
        
        # Parse transactions
        transactions = parser.parse(filepath, filename)
        
        # Force garbage collection after parsing to free memory
        gc.collect()
        
        if not transactions:
            flash(f'No transactions found in the {bank_info["name"]} statement. Please check if the PDF format is supported.', 'warning')
            os.remove(filepath)  # Clean up
            return redirect(url_for('index'))
        
        # Create Excel file
        df = pd.DataFrame(transactions)
        
        # Sort by date if possible
        try:
            df['Date_Sort'] = pd.to_datetime(df['Date'], format='%d/%m/%y', errors='coerce')
            df = df.sort_values('Date_Sort', na_position='last')
            df = df.drop('Date_Sort', axis=1)
        except:
            pass
        
        # Generate output filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"{bank_info['name'].replace(' ', '_')}_Transactions_{timestamp}.xlsx"
        output_filepath = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4()}_{output_filename}")
        
        # Save Excel file
        df.to_excel(output_filepath, index=False)
        
        # Calculate summary
        total_transactions = len(df)
        deposits_total = df[df['Transaction_Amount'] > 0]['Transaction_Amount'].sum()
        withdrawals_total = abs(df[df['Transaction_Amount'] < 0]['Transaction_Amount'].sum())
        deposits_count = (df['Transaction_Amount'] > 0).sum()
        withdrawals_count = (df['Transaction_Amount'] < 0).sum()
        net_amount = deposits_total - withdrawals_total
        
        # Get date range
        date_range = ""
        try:
            dates = pd.to_datetime(df['Date'], format='%d/%m/%y', errors='coerce').dropna()
            if len(dates) > 0:
                start_date = dates.min().strftime('%d %B %Y')
                end_date = dates.max().strftime('%d %B %Y')
                date_range = f"{start_date} to {end_date}"
        except:
            pass
        
        summary = {
            'bank_name': bank_info['name'],
            'total_transactions': total_transactions,
            'deposits_count': deposits_count,
            'deposits_total': deposits_total,
            'withdrawals_count': withdrawals_count, 
            'withdrawals_total': withdrawals_total,
            'net_amount': net_amount,
            'date_range': date_range,
            'output_filename': output_filename
        }
        
        # Emit completion progress
        socketio.emit('progress_update', {
            'current_page': total_transactions,
            'total_pages': total_transactions,
            'status': 'Processing complete!',
            'percentage': 100
        })
        
        # Clean up input file
        os.remove(filepath)
        
        # Return Excel file
        return send_file(
            output_filepath,
            as_attachment=True,
            download_name=output_filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        flash(f'Error processing PDF: {str(e)}', 'error')
        # Clean up files on error
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
            if 'output_filepath' in locals() and os.path.exists(output_filepath):
                os.remove(output_filepath)
        except:
            pass
        return redirect(url_for('index'))

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
    flash('File too large. Maximum size is 100MB.', 'error')
    return redirect(url_for('index'))

# For deployment with gunicorn, we need to expose the SocketIO app
# This allows gunicorn to use: gunicorn app:socketio

if __name__ == '__main__':
    # For local development
    port = int(os.environ.get('PORT', 5001))
    print(f"Starting Flask app on port {port}")
    socketio.run(app, debug=False, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)