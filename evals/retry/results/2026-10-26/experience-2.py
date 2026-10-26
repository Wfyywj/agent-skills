"""Order client with shared deadlines and limited, safe retries."""

from urllib.parse import quote


_RETRYABLE_STATUSES = frozenset((429, 502, 503, 504))


def _retry_delay(response, delay_ms):
    if response is None or response["status"] not in (429, 503):
        return delay_ms
    for name, value in response["headers"].items():
        if name.lower() != "retry-after":
            continue
        value = value.strip()
        if not value:
            return delay_ms
        seconds = 0
        for digit in value:
            if digit < "0" or digit > "9":
                return delay_ms
            seconds = seconds * 10 + ord(digit) - ord("0")
        return max(delay_ms, seconds * 1000)
    return delay_ms


def _request(send, wait, now, method, path, headers, body, deadline_ms,
             attempt_timeout_ms, max_attempts, delay_ms, can_retry=None):
    saw_timeout = False
    last_error = "budget"
    for attempt in range(max_attempts):
        current_ms = now()
        remaining_ms = deadline_ms - current_ms
        if remaining_ms <= 0:
            return None, "budget", saw_timeout
        if attempt and can_retry is not None and not can_retry(current_ms):
            return None, last_error, saw_timeout

        response = None
        try:
            response = send(method, path, dict(headers), body,
                            min(attempt_timeout_ms, remaining_ms))
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

        if attempt + 1 >= max_attempts:
            return None, last_error, saw_timeout
        pause_ms = _retry_delay(response, delay_ms)
        current_ms = now()
        if can_retry is not None and not can_retry(current_ms):
            return None, last_error, saw_timeout
        if deadline_ms - current_ms <= pause_ms:
            return None, "budget", saw_timeout
        wait(pause_ms)
        # The next iteration rechecks both the deadline and replay validity.

    return None, last_error, saw_timeout


def list_orders(send, wait, now, headers, *, budget_ms=200,
                attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                transport_retries=False):
    deadline_ms = now() + budget_ms
    items = []
    path = "/orders"
    seen_cursors = set()
    attempts = 1 if transport_retries else max_attempts
    while True:
        response, error, _ = _request(
            send, wait, now, "GET", path, headers, None, deadline_ms,
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
        path = "/orders?cursor=" + quote(cursor, safe="")


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

    def can_retry(current_ms):
        return (bool(operation_key) and key_valid_until_ms is not None
                and current_ms < key_valid_until_ms)

    attempts = 1 if transport_retries else max_attempts
    response, _, saw_timeout = _request(
        send, wait, now, "POST", "/orders", request_headers, payload,
        deadline_ms, attempt_timeout_ms, attempts, delay_ms, can_retry)
    if response is not None:
        return {"outcome": "success", "order_id": response["body"]["id"]}
    return {"outcome": "unknown" if saw_timeout else "failed", "order_id": None}
