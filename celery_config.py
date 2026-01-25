import os
from celery import Celery

def make_celery(app_name=__name__):
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    
    celery = Celery(
        app_name,
        backend=redis_url,
        broker=redis_url,
        include=['worker']  # Explicitly include the worker module
    )
    
    celery.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
        # Optimize for long-running tasks
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_track_started=True,
        broker_connection_retry_on_startup=True,
        # Use solo pool by default to avoid fork() SIGSEGV with native libraries
        # PaddleOCR, OpenCV, and other C++ extensions crash when forked
        # This can be overridden with --pool=prefork if needed
        worker_pool='solo',
    )
    
    return celery

celery_app = make_celery()

if __name__ == '__main__':
    celery_app.start()
