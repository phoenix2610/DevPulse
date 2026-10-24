from celery import Celery

from ..config import get_settings

s = get_settings()
celery_app = Celery("reviewer", broker=s.redis_url or "memory://", backend=s.redis_url or None, include=["app.worker.tasks"])
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_time_limit=s.task_time_limit_s, task_soft_time_limit=s.task_time_limit_s - 60,
                       task_always_eager=s.eager, task_eager_propagates=False,
                       beat_schedule={"maintenance": {"task": "maintenance", "schedule": float(s.maintenance_interval_s)}})
