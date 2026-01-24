web: gunicorn --worker-class eventlet -w 1 app:app --bind 0.0.0.0:$PORT
worker: celery -A celery_config.celery_app worker --loglevel=info
