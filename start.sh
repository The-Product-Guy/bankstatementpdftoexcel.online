#!/bin/bash

# Start script for Railway deployment
echo "🚀 Starting PDF Excel Converter..."

# Get the port from environment (Railway sets $PORT)
PORT=${PORT:-8080}
echo "📡 Using port: $PORT"

# Check if uploads directory exists
if [ ! -d "uploads" ]; then
    echo "📁 Creating uploads directory..."
    mkdir -p uploads
fi

# Check if parsers are importable
echo "🔍 Checking parsers..."
python3 -c "
try:
    from parsers.hdfc_parser import HDFCParser
    from parsers.icici_parser import ICICIParser
    print('✅ Parsers loaded successfully')
except Exception as e:
    print(f'❌ Parser error: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "❌ Parser check failed, exiting..."
    exit 1
fi

# Start the application
echo "🌐 Starting Gunicorn server..."
exec gunicorn app:app \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --timeout 120 \
    --worker-class sync \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --preload \
    --max-requests 1000 \
    --max-requests-jitter 100