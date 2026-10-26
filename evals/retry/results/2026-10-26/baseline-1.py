"""Order client with bounded retries and a shared pagination deadline."""

from copy import deepcopy
from urllib.parse import urlencode


_RETRYABLE_STATUSES = frozenset((429, 502, 503, 504))


def _retry_delay(response, delay_ms):
    if response["status"] not in (429, 503):
        return delay_ms
    for name, value in response["headers"].items():
        if name.lower() != "retry-after":
            continue
        value = value.strip()
        if not value:
            return delay_ms
        seconds = 0
        for digit in value:
            if not "0" <= digit <= "9":
                return delay_ms
            seconds = seconds * 10 + ord(digit) - ord("0")
        return max(delay_ms, seconds * 1000)
    return delay_ms


def _request(send, wait, now, method, path, headers, body, deadline,
             attempt_timeout_ms, max_attempts, delay_ms,
             transport_retries=False, retry_allowed=None):
    attempt_limit = 1 if transport_retries else max_attempts
    last_error = "budget"
    had_timeout = False
    for attempt in range(attempt_limit):
        request_headers = dict(headers)
        request_body = deepcopy(body)
        current = now()
        remaining = deadline - current
        if remaining <= 0:
            return None, "budget", had_timeout
        if attempt and retry_allowed is not None and not retry_allowed(current):
            return None, last_error, had_timeout
        try:
            response = send(method, path, request_headers, request_body,
                            min(attempt_timeout_ms, remaining))
        except TimeoutError:
            had_timeout = True
            last_error = "timeout"
            retry_delay = delay_ms
        else:
            status = response["status"]
            if 200 <= status < 300:
                return response, None, had_timeout
            last_error = "http:" + str(status)
            if status not in _RETRYABLE_STATUSES:
                return None, last_error, had_timeout
            retry_delay = _retry_delay(response, delay_ms)

        if attempt + 1 == attempt_limit:
            return None, last_error, had_timeout
        current = now()
        if retry_allowed is not None and not retry_allowed(current):
            return None, last_error, had_timeout
        if deadline - current <= retry_delay:
            return None, "budget", had_timeout
        wait(retry_delay)
    return None, last_error, had_timeout


def list_orders(send, wait, now, headers, *, budget_ms=200,
                attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                transport_retries=False):
    deadline = now() + budget_ms
    request_headers = dict(headers)
    items = []
    path = "/orders"
    seen_cursors = set()
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

    def retry_allowed(current):
        return (bool(operation_key) and key_valid_until_ms is not None
                and current < key_valid_until_ms)

    response, _, had_timeout = _request(
        send, wait, now, "POST", "/orders", request_headers, request_body,
        deadline, attempt_timeout_ms, max_attempts, delay_ms,
        transport_retries, retry_allowed)
    if response is not None:
        return {"outcome": "success", "order_id": response["body"]["id"]}
    return {"outcome": "unknown" if had_timeout else "failed", "order_id": None}
