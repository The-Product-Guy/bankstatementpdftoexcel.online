#!/usr/bin/env sh
set -e

PORT_TO_USE=${PORT:-8080}
export PORT="$PORT_TO_USE"

echo "Starting Gunicorn on 0.0.0.0:${PORT_TO_USE}"
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT_TO_USE} \
  --workers 1 \
  --timeout 120 \
  --graceful-timeout 30 \
  --worker-tmp-dir /dev/shm \
  --worker-class sync \
  --log-level info \
  --access-logfile - \
  --error-logfile -
