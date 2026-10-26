"""Order client with bounded retries and a shared per-operation deadline."""

from copy import deepcopy
from urllib.parse import urlencode


_RETRYABLE_STATUSES = {429, 502, 503, 504}


def _retry_delay(response, delay_ms):
    if response is None or response["status"] not in (429, 503):
        return delay_ms
    for name, value in response["headers"].items():
        if name.lower() != "retry-after":
            continue
        value = value.strip()
        if not value or any(char < "0" or char > "9" for char in value):
            return delay_ms
        seconds = 0
        for char in value:
            seconds = seconds * 10 + ord(char) - ord("0")
        return max(delay_ms, seconds * 1000)
    return delay_ms


def _request(send, wait, now, method, path, headers, body, deadline_ms,
             attempt_timeout_ms, max_attempts, delay_ms,
             transport_retries=False, may_retry=None):
    attempts = 1 if transport_retries else max_attempts
    body_snapshot = deepcopy(body)
    had_timeout = False
    last_error = "budget"

    for attempt in range(attempts):
        request_headers = dict(headers)
        request_body = deepcopy(body_snapshot)
        current = now()
        remaining = deadline_ms - current
        if remaining <= 0:
            return None, "budget", had_timeout
        if attempt and may_retry is not None and not may_retry(current):
            return None, last_error, had_timeout

        response = None
        try:
            response = send(method, path, request_headers, request_body,
                            min(attempt_timeout_ms, remaining))
        except TimeoutError:
            had_timeout = True
            last_error = "timeout"
        else:
            status = response["status"]
            if 200 <= status < 300:
                return response, None, had_timeout
            last_error = "http:" + str(status)
            if status not in _RETRYABLE_STATUSES:
                return None, last_error, had_timeout

        if attempt + 1 == attempts:
            return None, last_error, had_timeout

        planned_delay = _retry_delay(response, delay_ms)
        current = now()
        if may_retry is not None and not may_retry(current):
            return None, last_error, had_timeout
        if deadline_ms - current <= planned_delay:
            return None, "budget", had_timeout
        wait(planned_delay)
        # The next iteration checks both limits again after scheduling delays.

    return None, last_error, had_timeout


def list_orders(send, wait, now, headers, *, budget_ms=200,
                attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                transport_retries=False):
    deadline_ms = now() + budget_ms
    request_headers = dict(headers)
    items = []
    path = "/orders"
    seen_cursors = set()

    while True:
        response, error, _ = _request(
            send, wait, now, "GET", path, request_headers, None, deadline_ms,
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
        path = "/orders?" + urlencode({"cursor": cursor})


def create_order(send, wait, now, headers, payload, *, operation_key=None,
                 key_valid_until_ms=None, budget_ms=200,
                 attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                 transport_retries=False):
    deadline_ms = now() + budget_ms
    request_headers = {
        name: value for name, value in headers.items()
        if name.lower() != "idempotency-key"
    }
    if operation_key:
        request_headers["Idempotency-Key"] = operation_key

    def may_retry(current):
        return (bool(operation_key) and key_valid_until_ms is not None
                and current < key_valid_until_ms)

    response, _, had_timeout = _request(
        send, wait, now, "POST", "/orders", request_headers, payload,
        deadline_ms, attempt_timeout_ms, max_attempts, delay_ms,
        transport_retries, may_retry)
    if response is not None:
        return {"outcome": "success", "order_id": response["body"]["id"]}
    return {"outcome": "unknown" if had_timeout else "failed", "order_id": None}
