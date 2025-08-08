# Use Python 3.9 slim image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create uploads directory
RUN mkdir -p uploads

# Set environment variables
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Expose and set a default port (Railway may inject $PORT; default remains 8080)
EXPOSE 8080

# Health check (respect platform-provided $PORT)
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD sh -c 'curl -f http://localhost:${PORT:-8080}/health || exit 1'

# Start application with proper error handling (bind to platform $PORT)
# Add startup script
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Use startup script that respects $PORT and logs it
CMD ["/app/start.sh"]