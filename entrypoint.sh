#!/bin/sh

# Railway-compatible entrypoint
# =============================================================================
# SCALING GUIDE (Railway Pro Plan):
#
# Architecture: Web service + Worker service (separate Railway services)
# - Web:    Handles HTTP/WebSocket, no OCR models loaded, lightweight
# - Worker: Runs Celery tasks, loads PaddleOCR + ONNX Runtime (~2-3 GB RAM)
#
# Horizontal scaling:
#   1. Create TWO Railway services from the same repo
#   2. Web service:   SERVICE_ROLE=web   (1 replica, small instance)
#   3. Worker service: SERVICE_ROLE=worker (N replicas for N concurrent jobs)
#   4. Both share the same REDIS_URL and DATABASE_URL
#   5. Increase worker replicas to handle more concurrent users
#
# Each worker replica uses ~2-3 GB RAM (ONNX Runtime backend).
# With 32 GB / 32 vCPU per replica, you can comfortably run 1 job per replica.
# 10 replicas = 10 concurrent PDF conversions.
#
# Worker recycling: --max-tasks-per-child prevents slow memory leaks.
# =============================================================================

echo "🚀 Railway entrypoint starting..."
echo "SERVICE_ROLE: ${SERVICE_ROLE:-web}"
echo "PORT environment variable: ${PORT:-'not set'}"

if [ "${SERVICE_ROLE}" = "worker" ]; then
  echo "Starting Celery worker (solo pool, ONNX Runtime backend)..."
  # Keep native OCR libs constrained to avoid memory spikes in container runtime.
  export OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}
  export OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-1}
  export MKL_NUM_THREADS=${MKL_NUM_THREADS:-1}
  export NUMEXPR_NUM_THREADS=${NUMEXPR_NUM_THREADS:-1}
  # Use solo pool to avoid fork() issues with ONNX Runtime/OpenCV native libraries
  # Fork can cause SIGSEGV when native C++ code is involved.
  # For horizontal scaling, increase Railway replicas instead of concurrency.
  # --max-tasks-per-child=50 recycles the worker process periodically to prevent
  # memory leaks from accumulating over long-running deployments.
  exec celery -A celery_config.celery_app worker \
    --loglevel=info \
    --pool=solo \
    --concurrency=1 \
    --max-tasks-per-child=50
fi

# Railway injects PORT - bind on IPv4 with fallback to 8080
PORT_TO_USE=${PORT:-8080}
echo "Binding Gunicorn on 0.0.0.0:${PORT_TO_USE}"
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT_TO_USE} \
  --workers 1 \
  --threads ${WEB_THREADS:-20} \
  --timeout 120 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  --log-level info
