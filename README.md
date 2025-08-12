# 🏦 PDF to Excel Converter - Multi-Bank Web Application

A modern web application for converting bank statement PDFs to Excel format with support for multiple Indian banks. Features automatic transaction extraction, financial analysis, and secure file processing.

## ✨ Key Features

- **🌐 Web-based Interface** - Beautiful, responsive UI with drag-and-drop upload
- **🏦 Multi-Bank Support** - HDFC Bank, ICICI Bank, and Karur Vysya Bank (with OCR and text extraction)  
- **🤖 Smart Processing** - Auto-detects PDF type and uses appropriate parsing method
- **📊 Financial Analysis** - Automatic calculation of deposits, withdrawals, and balances
- **🔒 Secure Processing** - Files automatically deleted after conversion
- **📱 Responsive Design** - Works seamlessly on desktop and mobile devices
- **☁️ Cloud Ready** - Production-ready deployment configuration for Railway

## 🚀 Live Demo

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/deploy?template=https://github.com/your-username/pdf-excel-converter)

## 🏦 Supported Banks

| Bank | PDF Type | Processing Method | Features |
|------|----------|------------------|----------|
| **HDFC Bank** | Image-based | OCR (Tesseract) | ✅ All page processing<br/>✅ Pipe-delimited format<br/>✅ Large file support (100MB+) |
| **ICICI Bank** | Text-based | Direct extraction | ✅ Fast processing<br/>✅ Tabular format<br/>✅ High accuracy |
| **Karur Vysya Bank** | Image-based | OCR (Tesseract) | ✅ Enhanced OCR processing<br/>✅ Handles wrapped text<br/>✅ Summary line extraction |

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
│   ├── hdfc_parser.py        # 🏦 HDFC Bank processor
│   ├── icici_parser.py       # 🏦 ICICI Bank processor
│   └── kvb_parser.py         # 🏦 Karur Vysya Bank processor
└── uploads/                   # 📁 Temporary file storage
```

## 🛠️ Local Development

### Prerequisites

- **Python 3.9+**
- **Tesseract OCR** (for HDFC Bank processing)
- **Poppler utilities** (for PDF processing)

### Quick Start

1. **Clone & Setup**
   ```bash
   git clone <your-repo-url>
   cd PDF-XLS-Converter
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```

2. **Install System Dependencies**
   
   **macOS:**
   ```bash
   brew install tesseract poppler
   ```
   
   **Ubuntu/Debian:**
   ```bash
   sudo apt-get update
   sudo apt-get install tesseract-ocr poppler-utils
   ```

3. **Install Python Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run Application**
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
   - Set environment variable: `SECRET_KEY=your-production-secret-key`
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
| `SECRET_KEY` | Yes | Flask session security | - |
| `PORT` | No | Application port | `5001` |

### File Limits

- **Maximum file size**: 100MB
- **Supported formats**: PDF only
- **Processing timeout**: 5 minutes
- **Concurrent uploads**: Handled automatically

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