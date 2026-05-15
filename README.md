# 🏦 PDF to Excel Converter - Universal Bank Statement Parser

A modern web application for converting bank statement PDFs to Excel format with universal extraction. Features automatic transaction extraction, reliable OCR, and secure file processing.

## ✨ Key Features

- **🌐 Web-based Interface** - Beautiful, responsive UI with drag-and-drop upload
- **🏦 Universal Bank Support** - Works across text-based and scanned statements  
- **🤖 Smart Processing** - Auto-detects PDF type and uses the universal parsing pipeline
- **📊 Financial Analysis** - Automatic calculation of deposits, withdrawals, and balances
- **🔒 Secure Processing** - Files automatically deleted after conversion
- **📱 Responsive Design** - Works seamlessly on desktop and mobile devices
- **☁️ Cloud Ready** - Production-ready deployment configuration for Railway

## 🚀 Live Demo

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/deploy?template=https://github.com/your-username/pdf-excel-converter)

## 🏦 Supported Banks

Universal parser designed to handle any bank statement format (text-based or scanned) with raw table extraction.

## 📁 Clean Project Structure

```
PDF-XLS-Converter/
├── app.py                     # 🌐 Flask web application
├── requirements.txt           # 📦 Python dependencies
├── Procfile                   # 🚀 Railway deployment config
├── nixpacks.toml             # 🔧 Build configuration
├── railway.json              # ⚙️ Service configuration
├── README.md                 # 📖 Documentation
├── templates/
│   └── index.html            # 🎨 Web interface
├── static/
│   ├── styles.css            # 💎 Responsive styling
│   └── script.js             # ⚡ Interactive functionality
├── parsers/                   # 🏗️ Modular architecture
│   ├── __init__.py
│   ├── base_parser.py        # 🔧 Common utilities
│   └── universal_parser.py   # 🌐 Universal bank statement parser
└── uploads/                   # 📁 Temporary file storage
```

## 🛠️ Local Development

### Prerequisites

- **Python 3.9+**
- **Redis** (Required for background processing)
- **Tesseract OCR** (for Image-based PDFs)
- **Poppler utilities** (for PDF processing)

### Quick Start (Recommended)

We provide a script to automatically set up and run the application:

```bash
./run_local.sh
```

This script will:
- Check and install system dependencies (Redis, Tesseract, Poppler) via Homebrew
- Update Python dependencies
- Start Redis and Celery worker
- Launch the Flask application

### Manual Setup

1. **Install System Dependencies**
   
   **macOS:**
   ```bash
   brew install redis tesseract poppler
   ```
   
   **Ubuntu/Debian:**
   ```bash
   sudo apt-get update
   sudo apt-get install redis-server tesseract-ocr poppler-utils
   ```

2. **Clone & Setup**
   ```bash
   git clone <your-repo-url>
   cd PDF-XLS-Converter
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

3. **Install Python Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Start Services**
   
   You need to run these in separate terminals:

   **Terminal 1 (Redis):**
   ```bash
   redis-server
   ```

   **Terminal 2 (Celery Worker):**
   ```bash
   celery -A celery_config.celery_app worker --loglevel=info
   ```

   **Terminal 3 (Flask App):**
   ```bash
   python app.py
   ```
   
   🌐 Open `http://localhost:5001`

## 🚀 Production Deployment

### Railway (Recommended)

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Deploy PDF to Excel converter"
   git push origin main
   ```

2. **Deploy on Railway**
   - Connect your GitHub repository at [railway.app](https://railway.app)
   - Railway auto-detects all configurations
   - Set `SECRET_KEY=your-production-secret-key`
   - Set `PUBLIC_BASE_URL=https://your-domain.com` for canonical sitemap URLs and production SocketIO CORS defaults
   - Deploy automatically with zero configuration

3. **Production Features**
   - ✅ Automatic HTTPS
   - ✅ Custom domains
   - ✅ Auto-scaling
   - ✅ Health monitoring
   - ✅ System dependencies (Tesseract, Poppler) included

### Other Platforms

Ensure these system packages are installed:
```bash
# Required system packages
tesseract-ocr
poppler-utils
python3
pip3
```

## 📊 Output Excel Format

| Column | Description | Example |
|--------|-------------|---------|
| `Date` | Transaction date | `23/07/25` |
| `Description` | Transaction details | `UPI/ZOMATO LIM/zomatoorder` |
| `Reference_Number` | Bank reference/serial | `1`, `CHQ001` |
| `Withdrawal_Amount` | Debit amount | `254.59` |
| `Deposit_Amount` | Credit amount | `5000.00` |
| `Transaction_Amount` | Net amount (+/-) | `-254.59` |
| `Closing_Balance` | Balance after transaction | `9745.41` |
| `Source_File` | Original PDF name | `statement.pdf` |
| `Page_Line` | Reference for debugging | `Page1_Line15` |

## 🔧 Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `SECRET_KEY` | Yes in production | Flask session security. Required when `APP_ENV=production`, `FLASK_ENV=production`, or Railway runtime vars are present. | Local dev fallback |
| `APP_ENV` / `FLASK_ENV` | No | Set either to `production` to enable strict production defaults. | - |
| `PORT` | No | Application port | `5001` |
| `PUBLIC_BASE_URL` / `CANONICAL_BASE_URL` | Recommended in production | Public site origin for sitemap URLs and production SocketIO CORS defaults. | Request host |
| `SOCKETIO_CORS_ORIGINS` / `ALLOWED_ORIGINS` | No | Comma-separated SocketIO origins. Overrides the public base URL default. | Public base in production, `*` locally |
| `SESSION_COOKIE_SECURE` | No | Enables secure session cookies. | `true` in production, `false` locally |
| `RATE_LIMIT_FAIL_CLOSED` | No | Blocks rate-limited routes when Redis is unavailable. Leave off for availability-first behavior. | `false` |
| `MAX_UPLOAD_MB` | No | Upload size limit for guest/default conversions. Plan-specific limits may be higher. | `20` |
| `MAX_PAGES` | No | Maximum PDF pages processed per conversion. | `250` |
| `RESULT_RETENTION_HOURS` | No | Retention window for generated outputs in object storage. | `24` |
| `LOCAL_RESULT_RETENTION_HOURS` | No | Retention window for locally stored generated outputs. | `RESULT_RETENTION_HOURS` |
| `FEEDBACK_RETENTION_DAYS` | No | Retention window for feedback submissions and copied source/output files. | `30` |
| `USE_PYMUPDF` | No | Enables the optional PyMuPDF table-detection fallback for extraction. | `false` |
| `GA_MEASUREMENT_ID` / `GTM_CONTAINER_ID` | No | Analytics IDs injected into public pages. | - |

### File Limits

- **Maximum file size**: 20MB by default; paid plans allow larger uploads up to the enterprise cap
- **Supported formats**: PDF only
- **Processing timeout**: 5 minutes
- **Concurrent uploads**: Handled automatically

### Accuracy Gates

Use the configured benchmark gates before changing extraction logic:

```bash
python tools/run_accuracy_gates.py --dry-run
python tools/run_accuracy_gates.py --dataset synthetic_canada
```

The gate config lives in `tests/evaluation/accuracy_gates.json`. It runs `tools/evaluate_extraction.py` with dataset-specific presets and thresholds for row-match accuracy, field accuracy, proxy accuracy, and balance consistency.

## 🏗️ Architecture & Extensibility

### Adding New Banks

1. **Create Parser Class**
   ```python
   # parsers/new_bank_parser.py
   from .base_parser import BaseParser
   
   class NewBankParser(BaseParser):
       def parse(self, pdf_path, filename):
           # Implement parsing logic
           return transactions
   ```

2. **Register in App**
   ```python
   # app.py
   from parsers.new_bank_parser import NewBankParser
   
   SUPPORTED_BANKS = {
       'newbank': {
           'name': 'New Bank',
           'parser': NewBankParser,
           'description': 'Description of format'
       }
   }
   ```

### Technical Stack

- **Backend**: Flask, Gunicorn
- **PDF Processing**: PDFPlumber, pdf2image
- **OCR**: Tesseract, Pytesseract  
- **Data Processing**: Pandas, OpenPyXL
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Deployment**: Railway, Nixpacks

## 🔍 Processing Details

### HDFC Bank (Image-based PDFs)
```
PDF → Images → OCR → Text → Regex Parsing → Transactions
```
- **Method**: Tesseract OCR at 150 DPI
- **Format**: Pipe-delimited (`|`) transaction lines
- **Pattern**: `DD/MM/YY | Description | Reference | Amounts`
- **Performance**: ~1-2 minutes for 100MB files

### ICICI Bank (Text-based PDFs)
```
PDF → Text Extraction → Pattern Matching → Transactions
```
- **Method**: PDFPlumber direct text extraction
- **Format**: Tabular with fixed columns
- **Pattern**: `Serial Date Date Description Amount Amount Balance`
- **Performance**: ~10-30 seconds for typical files

## 🐛 Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|--------|----------|
| "No transactions found" | Unsupported PDF format | Try different bank selection |
| "File too large" | >100MB file | Split PDF or compress |
| OCR errors | Poor image quality | Use higher quality PDF |
| Missing dependencies | System packages not installed | Install Tesseract/Poppler |

### Debug Mode

```python
# Enable debug logging
app.run(debug=True)
```

### Health Check

```bash
curl https://your-app.railway.app/health
```

## 📈 Performance & Scalability

- **Memory Usage**: ~50-200MB per concurrent upload
- **Processing Speed**: 
  - Text PDFs: 10-30 seconds
  - Image PDFs: 1-3 minutes
- **Concurrent Users**: Handles multiple uploads simultaneously
- **File Cleanup**: Automatic deletion after processing

## 🔒 Security Features

- ✅ **Secure File Handling** - Files processed in isolated containers
- ✅ **No Data Persistence** - Files automatically deleted after processing
- ✅ **Input Validation** - File type, size, and format validation
- ✅ **CSRF Protection** - Flask security features enabled
- ✅ **HTTPS Encryption** - Automatic in production deployment

## 📝 License & Compliance

This project is for **educational and personal use**. When processing financial documents:

- ✅ Ensure compliance with your bank's terms of service
- ✅ Only process your own bank statements
- ✅ Be aware of data privacy regulations
- ✅ Use secure networks for uploads

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/new-bank-support`
3. Add your bank parser in `parsers/`
4. Update `SUPPORTED_BANKS` in `app.py`
5. Test thoroughly with real statements
6. Submit a pull request

## 🆘 Support

For issues and questions:
- 🐛 **Bug Reports**: [GitHub Issues](https://github.com/your-username/pdf-excel-converter/issues)
- 💬 **Discussions**: [GitHub Discussions](https://github.com/your-username/pdf-excel-converter/discussions)
- 📧 **Email**: your-email@example.com

---

<div align="center">

### 🚀 Ready to Convert Your Bank Statements?

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/deploy?template=https://github.com/your-username/pdf-excel-converter)

**Built with ❤️ for secure financial document processing**

</div>
