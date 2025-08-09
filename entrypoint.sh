#!/bin/sh

# Railway-compatible entrypoint
echo "🚀 Railway entrypoint starting..."
echo "PORT environment variable: ${PORT:-'not set'}"

# Railway injects PORT - use it with fallback to 8080
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 120 --access-logfile - --error-logfile - --log-level info