web: gunicorn -w 1 --threads 20 app:app --bind 0.0.0.0:$PORT --timeout 120
worker: env SERVICE_ROLE=worker celery -A celery_config.celery_app worker --loglevel=info --pool=solo --concurrency=1 --queues=conversion,celery --max-tasks-per-child=50
scheduler: env SERVICE_ROLE=scheduler celery -A celery_config.celery_app worker --loglevel=info --pool=solo --concurrency=1 --queues=maintenance --beat --schedule=/tmp/statement-converter-celerybeat-schedule
