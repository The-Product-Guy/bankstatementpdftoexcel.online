#!/bin/sh

# Railway-compatible entrypoint
echo "🚀 Railway entrypoint starting..."
echo "PORT environment variable: ${PORT:-'not set'}"

# Railway injects PORT - bind on both IPv4 and IPv6 with fallback to 8080
PORT_TO_USE=${PORT:-8080}
echo "Binding Gunicorn on 0.0.0.0:${PORT_TO_USE} and [::]:${PORT_TO_USE}"
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT_TO_USE} \
  --bind [::]:${PORT_TO_USE} \
  --workers 1 \
  --timeout 120 \
  --graceful-timeout 30 \
  --worker-class sync \
  --access-logfile - \
  --error-logfile - \
  --log-level info