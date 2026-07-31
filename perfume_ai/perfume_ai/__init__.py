# This ensures the Celery app is always imported when Django starts
# so that the @shared_task decorator uses this app.
from .celery import app as celery_app

__all__ = ('celery_app',)
