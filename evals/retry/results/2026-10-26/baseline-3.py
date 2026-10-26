"""Order client with bounded retries and safe order replay."""

from copy import deepcopy
from urllib.parse import quote


_RETRYABLE_STATUSES = {429, 502, 503, 504}


def _retry_delay(response, delay_ms):
    if response is None or response["status"] not in (429, 503):
        return delay_ms
    for name, value in response["headers"].items():
        if name.lower() != "retry-after":
            continue
        value = value.strip()
        if value and all("0" <= char <= "9" for char in value):
            try:
                return max(delay_ms, int(value) * 1000)
            except ValueError:
                pass
        return delay_ms
    return delay_ms


def _request(send, wait, now, method, path, headers, body, deadline,
             attempt_timeout_ms, max_attempts, delay_ms,
             transport_retries=False, retry_allowed=None):
    attempts = 1 if transport_retries else max_attempts
    saw_timeout = False
    last_error = None
    for attempt in range(attempts):
        current = now()
        remaining = deadline - current
        if remaining <= 0:
            return None, "budget", saw_timeout
        if attempt and retry_allowed is not None and not retry_allowed(current):
            return None, last_error, saw_timeout

        response = None
        try:
            response = send(method, path, dict(headers), deepcopy(body),
                            min(attempt_timeout_ms, remaining))
        except TimeoutError:
            saw_timeout = True
            last_error = "timeout"
        else:
            status = response["status"]
            if 200 <= status < 300:
                return response, None, saw_timeout
            last_error = "http:" + str(status)
            if status not in _RETRYABLE_STATUSES:
                return None, last_error, saw_timeout

        if attempt + 1 >= attempts:
            return None, last_error, saw_timeout

        planned_delay = _retry_delay(response, delay_ms)
        current = now()
        if retry_allowed is not None and not retry_allowed(current):
            return None, last_error, saw_timeout
        if deadline - current <= planned_delay:
            return None, "budget", saw_timeout
        wait(planned_delay)
        # The next iteration checks both the budget and replay permission again.

    return None, last_error, saw_timeout


def list_orders(send, wait, now, headers, *, budget_ms=200,
                attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                transport_retries=False):
    deadline = now() + budget_ms
    request_headers = dict(headers)
    items = []
    seen_cursors = set()
    path = "/orders"
    while True:
        response, error, _ = _request(
            send, wait, now, "GET", path, request_headers, None, deadline,
            attempt_timeout_ms, max_attempts, delay_ms, transport_retries)
        if response is None:
            return {"items": items, "complete": False, "error": error}
        page = response["body"]
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return {"items": items, "complete": True, "error": None}
        if cursor in seen_cursors:
            return {"items": items, "complete": False, "error": "cursor"}
        seen_cursors.add(cursor)
        path = "/orders?cursor=" + quote(cursor, safe="")


def create_order(send, wait, now, headers, payload, *, operation_key=None,
                 key_valid_until_ms=None, budget_ms=200,
                 attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                 transport_retries=False):
    deadline = now() + budget_ms
    request_headers = {
        name: value for name, value in headers.items()
        if name.lower() != "idempotency-key"
    }
    if operation_key:
        request_headers["Idempotency-Key"] = operation_key
    request_body = deepcopy(payload)

    def replay_allowed(current):
        return (bool(operation_key) and key_valid_until_ms is not None
                and current < key_valid_until_ms)

    response, _, saw_timeout = _request(
        send, wait, now, "POST", "/orders", request_headers, request_body,
        deadline, attempt_timeout_ms, max_attempts, delay_ms,
        transport_retries, replay_allowed)
    if response is not None:
        return {"outcome": "success", "order_id": response["body"]["id"]}
    return {"outcome": "unknown" if saw_timeout else "failed",
            "order_id": None}
