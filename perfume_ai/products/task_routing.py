"""Give each store its own Celery queue so one busy store cannot hold up the others.

Every store's messages used to share the default queue, a single FIFO behind two worker
slots. The rate limits in tasks.py are per store, but the store ceiling (120/min) is far
above what two slots can process, so a store comfortably under its own limit could still
build a backlog that every other store's customers waited behind.

With one queue per store, the worker takes turns between them: kombu's Redis transport
(default `queue_order_strategy = "round_robin"`) moves the queue it just served to the
back of its BRPOP order, so a flooded store gets one task and then every other store that
has work gets one. A store working alone still has both slots. Per-conversation ordering
is untouched; it is enforced by InboundEvent and the conversation lock, not by the queue.

A router applies to every dispatch - `.delay`, `.apply_async`, deferral re-dispatch and
`self.retry` - so no call site names a queue.

Queues are `store_id % STORE_QUEUE_COUNT`. Two stores whose ids share a remainder share a
queue (and so share turns); with 32 queues that starts at the 33rd store.
"""

STORE_QUEUE_COUNT = 32
DEFAULT_QUEUE = "celery"

# Tasks whose first argument is the store id and whose work is that store's alone.
STORE_TASKS = frozenset({
    "products.tasks.process_incoming_message",
    "products.tasks.process_incoming_attachment",
    "products.tasks.process_comment_task",
})


def store_queue(store_id):
    return f"store-{int(store_id) % STORE_QUEUE_COUNT}"


def all_queue_names():
    return [DEFAULT_QUEUE] + [store_queue(i) for i in range(STORE_QUEUE_COUNT)]


def route_store_task(name, args, kwargs, options, task=None, **kw):
    """Celery router: a store task goes to its store's queue; anything else to the default."""
    if name not in STORE_TASKS:
        return None
    store_id = (kwargs or {}).get("store_id")
    if store_id is None and args:
        store_id = args[0]
    if store_id is None:
        return None
    return {"queue": store_queue(store_id)}
