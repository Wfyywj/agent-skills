"""Order client with bounded retries and replay-safe order creation."""

from copy import deepcopy
from urllib.parse import urlencode


_RETRYABLE_STATUSES = frozenset((429, 502, 503, 504))


def _retry_delay(response, delay_ms):
    if response is None or response["status"] not in (429, 503):
        return delay_ms
    for name, value in response["headers"].items():
        if name.lower() == "retry-after":
            value = value.strip()
            if value and all("0" <= digit <= "9" for digit in value):
                # Parse decimal digits without depending on int's string limit.
                seconds = 0
                for digit in value:
                    seconds = seconds * 10 + ord(digit) - ord("0")
                return max(delay_ms, seconds * 1000)
            break
    return delay_ms


def _request(send, wait, now, method, path, headers, body, deadline,
             attempt_timeout_ms, max_attempts, delay_ms, can_replay=None):
    last_error = "budget"
    saw_timeout = False
    for attempt in range(max_attempts):
        # Each send receives its own copies, so retries keep the same request.
        request_headers = dict(headers)
        request_body = deepcopy(body)
        current = now()
        remaining = deadline - current
        if remaining <= 0:
            return None, "budget", saw_timeout
        if attempt and can_replay is not None and not can_replay(current):
            return None, last_error, saw_timeout

        try:
            response = send(method, path, request_headers, request_body,
                            min(attempt_timeout_ms, remaining))
        except TimeoutError:
            response = None
            saw_timeout = True
            last_error = "timeout"
        else:
            status = response["status"]
            if 200 <= status < 300:
                return response, None, saw_timeout
            last_error = "http:" + str(status)
            if status not in _RETRYABLE_STATUSES:
                return None, last_error, saw_timeout

        if attempt + 1 >= max_attempts:
            return None, last_error, saw_timeout

        current = now()
        if can_replay is not None and not can_replay(current):
            return None, last_error, saw_timeout
        pause = _retry_delay(response, delay_ms)
        if deadline - current <= pause:
            return None, "budget", saw_timeout
        wait(pause)
        # The next iteration checks the actual time and key after waiting.

    return None, last_error, saw_timeout


def list_orders(send, wait, now, headers, *, budget_ms=200,
                attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                transport_retries=False):
    deadline = now() + budget_ms
    request_headers = dict(headers)
    attempts = 1 if transport_retries else max_attempts
    items = []
    path = "/orders"
    seen_cursors = set()

    while True:
        response, error, _ = _request(
            send, wait, now, "GET", path, request_headers, None, deadline,
            attempt_timeout_ms, attempts, delay_ms)
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
        path = "/orders?" + urlencode({"cursor": cursor})


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

    def can_replay(current):
        return (bool(operation_key) and key_valid_until_ms is not None
                and current < key_valid_until_ms)

    response, _, saw_timeout = _request(
        send, wait, now, "POST", "/orders", request_headers, request_body,
        deadline, attempt_timeout_ms,
        1 if transport_retries else max_attempts, delay_ms, can_replay)
    if response is not None:
        return {"outcome": "success", "order_id": response["body"]["id"]}
    return {"outcome": "unknown" if saw_timeout else "failed", "order_id": None}
