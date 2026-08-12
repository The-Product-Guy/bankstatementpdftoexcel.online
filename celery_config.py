import os
from celery import Celery

def make_celery(app_name=__name__):
    redis_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    service_role = os.environ.get('SERVICE_ROLE', 'web').strip().lower() or 'web'
    includes = {
        'web': [],
        'worker': ['worker'],
        'scheduler': ['maintenance_tasks'],
        'local-worker': ['worker', 'maintenance_tasks'],
    }.get(service_role, [])
    
    celery = Celery(
        app_name,
        backend=redis_url,
        broker=redis_url,
        include=includes,
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
        task_publish_retry=False,
        broker_connection_timeout=1,
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            'socket_connect_timeout': 1,
            'socket_timeout': 1,
        },
        beat_schedule={
            'hourly-retention-sweep': {
                'task': 'maintenance.run_retention_sweep',
                'schedule': 3600.0,
                'options': {'queue': 'maintenance'},
            },
        },
        task_routes={
            'worker.process_pdf': {'queue': 'conversion'},
            'maintenance.*': {'queue': 'maintenance'},
        },
        # Use solo pool by default to avoid fork() SIGSEGV with native libraries
        # PaddleOCR, OpenCV, and other C++ extensions crash when forked
        # This can be overridden with --pool=prefork if needed
        worker_pool='solo',
    )
    
    return celery

celery_app = make_celery()

if __name__ == '__main__':
    celery_app.start()
