#!/bin/sh

# Railway-compatible entrypoint
echo "🚀 Railway entrypoint starting..."
echo "SERVICE_ROLE: ${SERVICE_ROLE:-web}"
echo "PORT environment variable: ${PORT:-'not set'}"

if [ "${SERVICE_ROLE}" = "worker" ]; then
  echo "Starting Celery worker..."
  # Use solo pool to avoid fork() issues with PaddleOCR/OpenCV native libraries
  # Fork can cause SIGSEGV when native C++ code is involved
  exec celery -A celery_config.celery_app worker --loglevel=info --pool=solo
fi

# Railway injects PORT - bind on IPv4 with fallback to 8080
PORT_TO_USE=${PORT:-8080}
echo "Binding Gunicorn on 0.0.0.0:${PORT_TO_USE}"
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT_TO_USE} \
  --workers 1 \
  --timeout 120 \
  --graceful-timeout 30 \
  --worker-class eventlet \
  --access-logfile - \
  --error-logfile - \
  --log-level info
