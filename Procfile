web: gunicorn -w 1 --threads 20 app:app --bind 0.0.0.0:$PORT --timeout 120
worker: celery -A celery_config.celery_app worker --loglevel=info --pool=solo --concurrency=1 --max-tasks-per-child=50
