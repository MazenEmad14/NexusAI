import asyncio
import secrets
import time
from typing import Optional

# Per-user queues for proactive messages produced by background jobs.
_MAX_QUEUED = 50
_queues: dict[str, asyncio.Queue] = {}

# One-time tickets that let the browser's EventSource (which cannot send an
# Authorization header) open the notifications stream for a logged-in user.
_TICKET_TTL_SECONDS = 60
_tickets: dict[str, tuple[str, float]] = {}


def get_user_queue(user_id: str) -> asyncio.Queue:
    queue = _queues.get(user_id)
    if queue is None:
        queue = asyncio.Queue(maxsize=_MAX_QUEUED)
        _queues[user_id] = queue
    return queue


async def publish(user_id: str, message: str) -> None:
    queue = get_user_queue(user_id)
    if queue.full():
        try:
            queue.get_nowait()  # drop the oldest so a user who never connects can't grow memory
        except asyncio.QueueEmpty:
            pass
    await queue.put(message)


def issue_ticket(user_id: str) -> str:
    now = time.time()
    for key in [k for k, (_, exp) in _tickets.items() if exp < now]:
        del _tickets[key]
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = (user_id, now + _TICKET_TTL_SECONDS)
    return ticket


def redeem_ticket(ticket: str) -> Optional[str]:
    entry = _tickets.pop(ticket, None)
    if entry is None or entry[1] < time.time():
        return None
    return entry[0]
