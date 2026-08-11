# Use Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies for OCR and Image Processing
# - tesseract-ocr: fallback OCR engine
# - libgl1, libglib2.0-0: OpenCV dependencies
# - libgomp1: OpenMP for ONNX Runtime parallel inference
# - poppler-utils: pdf2image (pdftoppm)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
# RapidOCR uses ONNX Runtime. PaddleOCR/img2table remain optional compatibility layers.
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure entrypoint is executable
RUN chmod +x entrypoint.sh

# Create necessary directories
RUN mkdir -p uploads processed

# Expose port
EXPOSE 5000

# Command to run (will be overridden by Railway service config / entrypoint.sh)
CMD ["sh", "-c", "./entrypoint.sh"]
