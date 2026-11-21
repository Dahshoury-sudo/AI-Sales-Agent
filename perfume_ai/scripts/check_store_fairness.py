"""Show that a flooded store no longer delays another store's messages.

Runs a real Celery worker in-process on the in-memory broker, using the project's own queue
and routing settings. Store A has a backlog of 30 tasks queued before store B's one task.
The worker has one slot (solo pool, prefetch 1), the tightest case.

  before  - routing off: every task lands in the single default queue (the old behaviour)
  after   - routing on: each store has its own queue and the worker takes turns

Usage (from the perfume_ai directory):
    python scripts/check_store_fairness.py

Each mode runs in a fresh subprocess so Celery's cached router and queues start clean. No
database, Redis or model calls are used.
"""
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKLOG = 30
STORE_A, STORE_B = 1, 2
PROBE = "fairness.probe"


def run(mode):
    sys.path.insert(0, ROOT)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "perfume_ai.settings")
    # Celery reads these environment variables ahead of Django settings. Without them the
    # probes would be published to the real broker, where a running dev worker would
    # take them.
    os.environ["CELERY_BROKER_URL"] = "memory://"
    os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
    import django
    django.setup()
    from django.conf import settings
    settings.CELERY_BROKER_URL = "memory://"
    settings.CELERY_RESULT_BACKEND = "cache+memory://"

    from celery.contrib.testing.worker import start_worker
    from perfume_ai.celery import app
    from products import task_routing

    with app.connection_for_write() as conn:
        transport = type(conn.transport).__module__
    if transport != "kombu.transport.memory":
        print(f"refusing to run: broker transport is {transport}, not the in-memory one")
        return 2

    # The probe stands in for a store task: same router, same queues, no side effects.
    task_routing.STORE_TASKS = (task_routing.STORE_TASKS | {PROBE}) if mode == "after" else frozenset()

    order, done = [], threading.Event()

    @app.task(name=PROBE, ignore_result=True)
    def probe(store_id, n):
        order.append("B" if store_id == STORE_B else "A")
        time.sleep(0.01)
        if len(order) == BACKLOG + 1:
            done.set()

    # A real worker declares its queues on start. Here the backlog is published before the
    # worker exists, and the memory broker drops messages for undeclared queues.
    with app.connection_for_write() as conn:
        for queue in app.amqp.queues.values():
            queue.bind(conn.default_channel).declare()

    for n in range(BACKLOG):
        probe.delay(STORE_A, n)
    probe.delay(STORE_B, 0)

    with start_worker(app, pool="solo", concurrency=1, perform_ping_check=False, loglevel="ERROR"):
        if not done.wait(60):
            print(f"{mode}: timed out after {len(order)} tasks")
            return 1
    position = order.index("B") + 1
    print(f"{mode:6} store B ran at position {position:2} of {len(order)}   order: {''.join(order)}")
    return 0


def main():
    codes = [subprocess.call([sys.executable, __file__, mode]) for mode in ("before", "after")]
    return max(codes)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]) if len(sys.argv) > 1 else main())
