# 🏦 PDF to Excel Converter - Geometry-First Bank Statement Parser

A modern web application for converting bank statement PDFs to Excel format with geometry-based extraction, OCR support, and secure file processing.

## ✨ Key Features

- **🌐 Web-based Interface** - Beautiful, responsive UI with drag-and-drop upload
- **🏦 Geometry-Based Bank Support** - Designed for varied text-based and scanned statement layouts
- **🤖 Smart Processing** - Auto-detects PDF type, builds the layout-preserving `Exact_Copy` sheet, and attempts a convenient `Table_Data` view
- **📊 Layout-Preserving Output** - `Exact_Copy` retains every successfully extracted word in visual row order with approximate page geometry; `Table_Data` adds a best-effort transaction view
- **🔒 Secure Processing** - Source PDFs are deleted after processing unless the user explicitly opts into temporary feedback retention
- **📱 Responsive Design** - Works seamlessly on desktop and mobile devices
- **☁️ Cloud Ready** - Production-ready deployment configuration for Railway

## 🚀 Live Demo

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/deploy?template=https://github.com/your-username/pdf-excel-converter)

## 🏦 Supported Banks

The geometry-first parser is designed for varied bank statement formats, including text-based and scanned PDFs. Scans depend on OCR and require careful output review.

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
- **Tesseract OCR** (fallback for image-based PDFs)
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
   SERVICE_ROLE=local-worker celery -A celery_config.celery_app worker \
     --loglevel=info --pool=solo --queues=conversion,maintenance,celery --beat
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
   - Set `CANONICAL_BASE_URL=https://your-domain.com` for canonical URLs, magic links, and trusted production host validation
   - Set `RESEND_API_KEY` and a `RESEND_FROM_EMAIL` address on a verified Resend domain; conversion uploads require magic-link sign-in
   - Create web (`SERVICE_ROLE=web`), worker (`SERVICE_ROLE=worker`), and one scheduler (`SERVICE_ROLE=scheduler`) services
   - Give all three services the same Postgres, Redis, storage, and application configuration
   - If the public domain is Cloudflare-proxied, configure the Cloudflare-to-Railway origin protection below before enabling it in Railway.

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

The workbook prioritises source fidelity before table cleanup. `Exact_Copy` keeps every word the engine successfully extracts, groups the words into visual rows, and spreads them across Excel cells using approximate PDF coordinates. `Table_Data` is a separate convenience view whose detected headers, rows, and columns are best effort:

| Sheet | Purpose |
|-------|---------|
| `Exact_Copy` | Every successfully extracted word in visual row order, including headings, summaries, transaction lines, and footers. Cell placement approximates the horizontal geometry of the PDF. |
| `Table_Data` | Best-effort detection of transaction headers, rows, columns, and wrapped descriptions for convenient filtering and cleanup. A materially different later-page schema is written to `Table_Data_2`, `Table_Data_3`, and so on instead of truncating earlier rows. |
| `Full_Text` | Searchable page, line, extraction source, and text for every extracted visual line. |

Keep the original workbook as a review copy. Clean a duplicate of `Exact_Copy` or start from `Table_Data`, and verify important rows, columns, dates, amounts, and balances against the PDF. Scanned documents use OCR, which can miss words, confuse characters, or shift placement.

## 🔧 Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `SECRET_KEY` | Yes in production | Flask session security. Required when `APP_ENV=production`, `FLASK_ENV=production`, or Railway runtime vars are present. | Local dev fallback |
| `DATABASE_URL` | Yes in production | Shared Postgres database URL. Must be set on web, worker, and scheduler services so users, jobs, analytics, feedback, and retention share one durable store. | `sqlite:///local.db` locally |
| `RESEND_API_KEY` | Yes in production | Sends the mandatory one-time sign-in link. | None |
| `RESEND_FROM_EMAIL` | Yes in production | Sender on a verified Resend domain that can deliver sign-in links to users. | `onboarding@resend.dev` for limited development only |
| `APP_ENV` / `FLASK_ENV` | No | Set either to `production` to enable strict production defaults. | - |
| `PORT` | No | Application port | `5001` |
| `PUBLIC_BASE_URL` / `CANONICAL_BASE_URL` | Yes in production | Canonical public origin used for crawler URLs, emailed links, Stripe redirects, and host validation. | Request host locally |
| `SERVICE_ROLE` | Yes per Railway service | `web`, `worker`, or `scheduler`. Run exactly one scheduler replica. | `web` |
| `WEB_THREADS` | No | Gunicorn thread count for the web service entrypoint. | `20` |
| `SESSION_COOKIE_SECURE` | No | Enables secure session cookies. | `true` in production, `false` locally |
| `RATE_LIMIT_FAIL_CLOSED` | No | Blocks rate-limited routes when Redis is unavailable. | `true` in production, `false` locally |
| `CLOUDFLARE_PROXY_ENABLED` | No | Enables authenticated Cloudflare-to-Railway origin requests. | `false` |
| `CLOUDFLARE_ORIGIN_SECRET` | Required when Cloudflare protection is enabled | At least 32 random characters shared only by Railway and Cloudflare's edge rule. | Empty |
| `RATE_LIMIT_CONVERT` | No | Per-IP conversion limit; it remains independent from sign-in controls. | `15` per hour |
| `RATE_LIMIT_AUTH_IP` | No | Magic-link requests allowed per IP in the auth window. | `10` |
| `RATE_LIMIT_AUTH_EMAIL` | No | Magic-link requests allowed per normalized email in the auth window. | `5` |
| `RATE_LIMIT_AUTH_WINDOW_SECONDS` | No | Shared magic-link limiter window. | `900` |
| `AUTH_DISTINCT_EMAIL_ALERT_THRESHOLD` | No | Distinct normalized emails from an IP that trigger an abuse warning. | `5` |
| `AUTH_DISTINCT_EMAIL_WINDOW_SECONDS` | No | Window for the distinct-email abuse observation. | `86400` |
| `MAX_UPLOAD_MB` | No | PDF upload cap for every plan; values above the public cap are clamped. | `50` |
| `FILE_SPLITTER_URL` | No | Splitter link shown when a PDF exceeds the file-size or page limit. | `https://smallpdfsplit.online/` |
| `MAX_PAGES` | No | Maximum PDF pages processed per conversion. | `250` |
| `RESULT_RETENTION_HOURS` | No | Retention window for generated outputs in object storage. | `24` |
| `LOCAL_RESULT_RETENTION_HOURS` | No | Retention window for locally stored generated outputs. | `RESULT_RETENTION_HOURS` |
| `ACTIVE_UPLOAD_GRACE_HOURS` | No | Maximum age for protecting queued/processing local inputs from cleanup. | `24` |
| `FEEDBACK_RETENTION_DAYS` | No | Retention window for feedback submissions and copied source/output files. | `30` |
| `FIRST_PARTY_ANALYTICS_RETENTION_DAYS` | No | Retention window for internal page-view and login-event logs shown in the admin portal. | `180` |
| `FIRST_PARTY_ANALYTICS_SWEEP_MINS` | No | Minimum interval between analytics cleanup sweeps. | `1440` |
| `SECURITY_CONTACT` | No | Public vulnerability contact used in `/.well-known/security.txt`. Use a `mailto:` or HTTPS URL. | Privacy policy URL |
| `INDEXNOW_KEY` | No | Enables `/indexnow-key.txt` for IndexNow ownership verification when configured. | Disabled |
| `USE_PYMUPDF` | No | Enables the optional PyMuPDF table-detection fallback for extraction. | `false` |
| `VALIDATION_SUMMARY_OCR` | No | Allows the worker to OCR sampled first/last pages when native text does not provide a complete statement summary. | `true` |
| `VALIDATION_SUMMARY_SAMPLE_PAGES` | No | Number of pages sampled from the beginning and end for opening/closing balance and total extraction. | `2` |
| `GA_MEASUREMENT_ID` / `GTM_CONTAINER_ID` | No | Analytics IDs injected into public pages. | - |

### Cloudflare-to-Railway Origin Protection

Use this only when the production hostname is proxied through Cloudflare. It prevents a direct Railway-origin request from choosing a forged visitor IP or bypassing Cloudflare controls.

1. Generate a random secret of at least 32 characters and add it to Railway as `CLOUDFLARE_ORIGIN_SECRET`; keep `CLOUDFLARE_PROXY_ENABLED=false` initially.
2. In Cloudflare, create a Transform Rule for the proxied production hostname that **overwrites** the request header `X-Statement-Origin` with that exact secret. Do not append the header and do not let a client-supplied value pass through.
3. Confirm the rule is active, then set `CLOUDFLARE_PROXY_ENABLED=true` in the Railway web service and deploy it. The worker and scheduler do not receive public HTTP traffic, but may share the setting safely.
4. Verify normal requests through the Cloudflare hostname. In production, unauthenticated direct-origin requests are rejected with HTTP `421`; `/health` and `/health/detailed` remain exempt so Railway health checks can reach the service.

Enable the Railway flag only after the Cloudflare overwrite rule is live. Reversing that order rejects all normal public requests.

### File Limits

- **Maximum file size**: 50 MB for every plan. Larger PDFs are directed to the file splitter.
- **Supported formats**: PDF only
- **Processing timeout**: 5 minutes
- **Concurrent uploads**: Handled automatically

### Accuracy Gates

Use the configured benchmark gates before changing extraction logic:

```bash
python tools/run_accuracy_gates.py --dry-run
python tools/run_accuracy_gates.py --dataset synthetic_canada_ci
python tools/validate_statement_output.py output.xlsx --pdf statement.pdf --repair
```

The gate config lives in `tests/evaluation/accuracy_gates.json`. CI uses a deterministic generated dataset and fails on row-match, proxy-quality, or balance-consistency regressions.

## 🏗️ Architecture & Extensibility

### Improving Extraction

The primary parser is universal. Prefer improving shared layout detection, normalization, confidence scoring, and benchmark coverage before adding narrow format-specific logic.

1. **Improve Shared Parser Logic**
   ```python
   # parsers/universal_parser.py
   parser = create_universal_parser(use_llm=False)
   ```

2. **Use Optional Header Hints Sparingly**
   ```python
   # parsers/bank_profiles.py
   # Keep hints limited to signatures and header aliases.
   ```

### Technical Stack

- **Backend**: Flask, Gunicorn
- **PDF Processing**: PDFPlumber, pinned PyMuPDF, pdf2image fallback
- **OCR**: RapidOCR/ONNX Runtime, with Tesseract fallback
- **Data Processing**: Pandas, OpenPyXL
- **Frontend**: HTML5, CSS3, Vanilla JavaScript
- **Deployment**: Railway, Nixpacks

## 🔍 Processing Details

### Scanned statements

```text
PDF → PyMuPDF render → RapidOCR coordinates → visual rows → Exact_Copy + Table_Data
```

- **Standard**: 150 DPI; **High Quality**: 200 DPI.
- Tesseract is used only when the primary ONNX path is unavailable or lacks useful coverage.
- Suspect financial cells may receive a bounded row-band re-read. A value changes only when independent direct OCR renders agree; arithmetic is diagnostic only.
- Scanned workbooks are always marked for review against the source PDF.

### Text-based statements

```text
PDF → embedded words and coordinates → visual rows → Exact_Copy + Table_Data
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
| "File too large" | File exceeds 50 MB | Use the linked PDF splitter, then upload each part |
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
