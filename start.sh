#!/bin/bash

# Start script for Railway deployment
echo "🚀 Starting PDF Excel Converter..."

# Get the port from environment (Railway sets $PORT)
if [ -z "$PORT" ]; then
    export PORT=8080
    echo "📡 No PORT env var found, defaulting to: $PORT"
else
    echo "📡 Using Railway PORT: $PORT"
fi

# Validate port is a number (simple numeric check)
if echo "$PORT" | grep -E '^[0-9]+$' > /dev/null; then
    echo "✅ PORT validation passed: $PORT"
else
    echo "❌ Invalid PORT value: '$PORT'"
    export PORT=8080
    echo "📡 Defaulting to safe PORT: $PORT"
fi

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

# Print environment info for debugging
echo "🔍 Environment debug info:"
echo "   PORT = '$PORT'"
echo "   PWD = '$PWD'"
echo "   FLASK_APP = '$FLASK_APP'"

# Start the application
echo "🌐 Starting Gunicorn server on 0.0.0.0:$PORT ..."
exec gunicorn app:app \
    --bind "0.0.0.0:$PORT" \
    --workers 1 \
    --timeout 120 \
    --worker-class sync \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --preload \
    --max-requests 1000 \
    --max-requests-jitter 100