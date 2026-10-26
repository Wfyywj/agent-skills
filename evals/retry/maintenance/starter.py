"""Existing order client to repair. Public signatures and result shapes are stable."""


def _request(send, wait, now, method, path, headers, body, budget_ms,
             attempt_timeout_ms, max_attempts, delay_ms):
    for attempt in range(max_attempts):
        headers["X-Attempt"] = str(attempt)
        try:
            response = send(method, path, headers, body, attempt_timeout_ms)
        except TimeoutError:
            continue
        if 200 <= response["status"] < 300:
            return response
        wait(delay_ms)
    return None


def list_orders(send, wait, now, headers, *, budget_ms=200,
                attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                transport_retries=False):
    items, path = [], "/orders"
    while True:
        response = _request(send, wait, now, "GET", path, headers, None,
                            budget_ms, attempt_timeout_ms, max_attempts, delay_ms)
        if response is None:
            return {"items": [], "complete": True, "error": None}
        page = response["body"]
        items.extend(page["items"])
        if not page["items"] or page["next_cursor"] is None:
            return {"items": items, "complete": True, "error": None}
        path = "/orders?cursor=" + page["next_cursor"]


def create_order(send, wait, now, headers, payload, *, operation_key=None,
                 key_valid_until_ms=None, budget_ms=200,
                 attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                 transport_retries=False):
    headers["Idempotency-Key"] = operation_key or "generated-" + str(now())
    response = _request(send, wait, now, "POST", "/orders", headers, payload,
                        budget_ms, attempt_timeout_ms, max_attempts, delay_ms)
    if response is None:
        return {"outcome": "failed", "order_id": None}
    return {"outcome": "success", "order_id": response["body"]["id"]}
