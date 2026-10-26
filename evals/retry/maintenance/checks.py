"""Acceptance checks fixed before model generation; no real order service."""

from copy import deepcopy
from inspect import signature
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit


def page(ids, cursor=None):
    return {"items": [{"id": value} for value in ids], "next_cursor": cursor}


def reply(status=200, body=None, *, ms=5, headers=None, write=False, exception=None):
    return dict(status=status, body=body or {}, ms=ms, headers=headers or {},
                write=write, exception=exception)


def listed(ids=(), complete=True, error=None):
    return dict(items=[{"id": value} for value in ids], complete=complete, error=error)


def ordered(outcome, order_id=None):
    return dict(outcome=outcome, order_id=order_id)


CASES = [
    dict(id="list-two-pages", task="list_orders", split="development", replies=[reply(body=page([1], "p2")), reply(body=page([2]))], expected=listed([1, 2]), calls=2),
    dict(id="list-empty-encoded-cursor", task="list_orders", replies=[reply(body=page([], "tenant/a b")), reply(body=page([2]))], expected=listed([2]), calls=2),
    dict(id="list-retry-second-page", task="list_orders", replies=[reply(body=page([1], "p2")), reply(503), reply(body=page([2]))], expected=listed([1, 2]), calls=3),
    dict(id="list-shared-deadline", task="list_orders", policy=dict(budget_ms=180, attempt_timeout_ms=200), replies=[reply(body=page([1], "p2"), ms=150), reply(body=page([2]))], expected=listed([1, 2]), calls=2),
    dict(id="list-partial-http-error", task="list_orders", replies=[reply(body=page([1], "p2")), reply(400)], expected=listed([1], False, "http:400"), calls=2),
    dict(id="list-inner-retry", task="list_orders", policy=dict(transport_retries=True), replies=[reply(503), reply(body=page([1]))], expected=listed([], False, "http:503"), calls=1),
    dict(id="list-retry-after-budget", task="list_orders", replies=[reply(429, headers={"Retry-After": "1"}), reply(body=page([1]))], expected=listed([], False, "budget"), calls=1),
    dict(id="list-retry-after-zero", task="list_orders", replies=[reply(503, headers={"rEtRy-AfTeR": "0"}), reply(body=page([1]))], expected=listed([1]), calls=2),
    dict(id="list-retry-after-within-budget", task="list_orders", policy=dict(budget_ms=2000), replies=[reply(429, headers={"Retry-After": "1"}), reply(body=page([1]))], expected=listed([1]), calls=2),
    dict(id="list-repeated-cursor", task="list_orders", replies=[reply(body=page([1], "p2")), reply(body=page([2], "p2"))], expected=listed([1, 2], False, "cursor"), calls=2),
    dict(id="list-local-exception", task="list_orders", replies=[reply(exception="ValueError")], expected_exception="ValueError", calls=1),
    dict(id="list-zero-budget", task="list_orders", policy=dict(budget_ms=0), replies=[reply(body=page([1]))], expected=listed([], False, "budget"), calls=0),
    dict(id="list-wait-overslept", task="list_orders", wait_extra=250, replies=[reply(503), reply(body=page([1]))], expected=listed([], False, "budget"), calls=1),
    dict(id="list-preserve-duplicate-records", task="list_orders", replies=[reply(body=page([1], "p2")), reply(body=page([1]))], expected=listed([1, 1]), calls=2),
    dict(id="list-invalid-retry-after", task="list_orders", replies=[reply(503, headers={"Retry-After": "tomorrow"}), reply(body=page([1]))], expected=listed([1]), calls=2),
    dict(id="list-deadline-before-next-page", task="list_orders", policy=dict(budget_ms=80), replies=[reply(body=page([1], "p2"), ms=80), reply(body=page([2]))], expected=listed([1], False, "budget"), calls=1),
    dict(id="list-allowed-scheduler-delay", task="list_orders", wait_extra=5, replies=[reply(503), reply(body=page([1]))], expected=listed([1]), calls=2),
    dict(id="list-empty-string-cursor", task="list_orders", replies=[reply(body=page([], "")), reply(body=page([1]))], expected=listed([1]), calls=2),
    dict(id="create-normal", task="create_order", split="development", policy=dict(operation_key="op-1", key_valid_until_ms=2000), headers={"Authorization": "unit-test", "Idempotency-Key": "stale"}, replies=[reply(201, write=True)], expected=ordered("success", "order-1"), calls=1, effects=1),
    dict(id="create-no-key-timeout", task="create_order", headers={"Authorization": "unit-test", "Idempotency-Key": "stale"}, replies=[reply(None, write=True), reply(201, write=True)], expected=ordered("unknown"), calls=1, effects=1),
    dict(id="create-key-timeout-replay", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=2000), replies=[reply(None, write=True), reply(201, write=True)], expected=ordered("success", "order-1"), calls=2, effects=1),
    dict(id="create-key-expires-during-wait", task="create_order", wait_extra=15, policy=dict(operation_key="op-1", key_valid_until_ms=1100), replies=[reply(None, write=True), reply(201, write=True)], expected=ordered("unknown"), calls=1, effects=1),
    dict(id="create-inner-retry", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=2000, transport_retries=True), replies=[reply(503), reply(201, write=True)], expected=ordered("failed"), calls=1),
    dict(id="create-uncertainty-persists", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=2000), replies=[reply(None, write=True), reply(400)], expected=ordered("unknown"), calls=2, effects=1),
    dict(id="create-permanent-error", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=2000), replies=[reply(400), reply(201, write=True)], expected=ordered("failed"), calls=1),
    dict(id="create-zero-budget", task="create_order", policy=dict(budget_ms=0), replies=[reply(201, write=True)], expected=ordered("failed"), calls=0),
    dict(id="create-expired-key-first-call", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=999), replies=[reply(503), reply(201, write=True)], expected=ordered("failed"), calls=1),
    dict(id="create-local-exception", task="create_order", replies=[reply(exception="ValueError")], expected_exception="ValueError", calls=1),
    dict(id="create-no-key-http-error", task="create_order", replies=[reply(503), reply(201, write=True)], expected=ordered("failed"), calls=1),
    dict(id="create-one-attempt-limit", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=2000, max_attempts=1), replies=[reply(503), reply(201, write=True)], expected=ordered("failed"), calls=1),
    dict(id="create-safe-http-retry", task="create_order", policy=dict(operation_key="op-1", key_valid_until_ms=2000), replies=[reply(503), reply(201, write=True)], expected=ordered("success", "order-1"), calls=2, effects=1),
]
for case in CASES:
    case.setdefault("split", "holdout")


def retry_delay(response, base):
    if response and response["status"] in (429, 503):
        value = next((value for key, value in response["headers"].items() if key.lower() == "retry-after"), "")
        if value.strip().isascii() and value.strip().isdigit():
            return max(base, int(value.strip()) * 1000)
    return base


def evaluate(module, case):
    from . import starter
    policy = dict(budget_ms=200, attempt_timeout_ms=80, max_attempts=3,
                  delay_ms=10, transport_retries=False) | case.get("policy", {})
    if case["task"] == "create_order":
        policy = dict(operation_key=None, key_valid_until_ms=None) | policy
    headers = deepcopy(case.get("headers", {"Authorization": "unit-test"}))
    payload = {"sku": "p1", "quantity": 2, "meta": {"client": "web"}}
    original_headers, original_payload = deepcopy(headers), deepcopy(payload)
    start = clock = 1000
    calls = effects = 0
    expected_cursor = None
    last_path = last_end = None
    last_delay = policy["delay_ms"]
    keys, events, errors = {}, [], []

    def reject(message):
        errors.append(message)
        raise ValueError(message)

    def send(method, path, wire_headers, body, timeout_ms):
        nonlocal clock, calls, effects, expected_cursor, last_path, last_end, last_delay
        calls += 1
        events.append(dict(action="send", method=method, path=path, at_ms=clock - start,
                           timeout_ms=timeout_ms, headers=deepcopy(wire_headers), body=deepcopy(body)))
        if calls > 20:
            reject("transport call safety limit exceeded")
        remaining = policy["budget_ms"] - (clock - start)
        if type(timeout_ms) is not int or remaining <= 0 or timeout_ms != min(policy["attempt_timeout_ms"], remaining):
            reject("call outside deadline or incorrect timeout")
        parsed = urlsplit(path)
        cursor = parse_qs(parsed.query, keep_blank_values=True).get("cursor", [None])[0]
        identity = (method, parsed.path, cursor)
        if last_path == identity and clock - last_end < last_delay:
            reject("retry delay does not follow Retry-After/base delay")
        if case["task"] == "list_orders":
            if method != "GET" or parsed.path != "/orders" or cursor != expected_cursor or body is not None:
                reject("incorrect page request, encoding, method or body")
            expected_headers = {key.lower(): value for key, value in original_headers.items()}
        else:
            if method != "POST" or path != "/orders" or body != original_payload:
                reject("incorrect create request or changed payload")
            expected_headers = {key.lower(): value for key, value in original_headers.items() if key.lower() != "idempotency-key"}
            if policy["operation_key"]:
                expected_headers["idempotency-key"] = policy["operation_key"]
        normalized = {key.lower(): value for key, value in wire_headers.items()}
        if case["task"] == "create_order" and sum(key.lower() == "idempotency-key" for key in wire_headers) != int(bool(policy["operation_key"])):
            reject("duplicate or stale idempotency key header")
        if normalized != expected_headers:
            reject("caller headers lost or unrelated idempotency key used")
        item = case["replies"][min(calls - 1, len(case["replies"]) - 1)]
        if item["exception"]:
            raise ValueError("local configuration error")
        order_id = None
        if item["write"]:
            key = normalized.get("idempotency-key")
            valid = key and policy["key_valid_until_ms"] is not None and clock < policy["key_valid_until_ms"]
            if valid and key in keys:
                order_id = keys[key]
            else:
                effects += 1
                order_id = f"order-{effects}"
                if valid:
                    keys[key] = order_id
        timed_out = item["status"] is None or item["ms"] > timeout_ms
        clock += timeout_ms if timed_out else item["ms"]
        last_path, last_end = identity, clock
        response = dict(status=item["status"], headers=deepcopy(item["headers"]), body=deepcopy(item["body"]))
        last_delay = retry_delay(None if timed_out else response, policy["delay_ms"])
        if timed_out:
            raise TimeoutError("response not received")
        if case["task"] == "list_orders" and 200 <= response["status"] < 300:
            expected_cursor = response["body"]["next_cursor"]
        if order_id is not None and 200 <= response["status"] < 300:
            response["body"] = {"id": order_id}
        return response

    def wait(ms):
        nonlocal clock
        events.append(dict(action="wait", duration_ms=ms, at_ms=clock - start))
        if type(ms) is not int or ms != last_delay or clock - start + ms >= policy["budget_ms"]:
            reject("incorrect or unbudgeted wait")
        clock += ms + case.get("wait_extra", 0)

    outcome = None
    exception = None
    try:
        function = getattr(module, case["task"])
        parameters = lambda f: [(p.name, p.kind, p.default) for p in signature(f).parameters.values()]
        if parameters(function) != parameters(getattr(starter, case["task"])):
            errors.append("public signature/defaults changed")
        args = (send, wait, lambda: clock, headers)
        outcome = function(*args, *([payload] if case["task"] == "create_order" else []), **policy)
    except Exception as exc:
        exception = type(exc).__name__
    if exception != case.get("expected_exception"):
        errors.append(f"unexpected exception: {exception!r}")
    if not case.get("expected_exception") and outcome != case["expected"]:
        errors.append(f"expected {case['expected']!r}; got {outcome!r}")
    if calls != case["calls"] or effects != case.get("effects", 0):
        errors.append(f"expected calls/effects {case['calls']}/{case.get('effects', 0)}; got {calls}/{effects}")
    if headers != original_headers or payload != original_payload:
        errors.append("caller-owned data mutated")
    row = dict(id=case["id"], task=case["task"], split=case["split"], passed=not errors,
               calls=calls, side_effects=effects, elapsed_ms=clock - start, errors=errors)
    if errors:
        row["trace"] = events
    return row


def reference_call(send, wait, now, method, path, headers, body, deadline, policy, can_retry):
    """Handwritten self-check helper; not a model condition."""
    unknown = False
    for attempt in range(policy["max_attempts"]):
        remaining = deadline - now()
        if remaining <= 0:
            return None, "budget", unknown
        response = None
        try:
            response = send(method, path, headers, body, min(policy["attempt_timeout_ms"], remaining))
            if 200 <= response["status"] < 300:
                return response, None, unknown
            reason = f"http:{response['status']}"
            temporary = response["status"] in (429, 502, 503, 504)
        except TimeoutError:
            reason, temporary, unknown = "timeout", True, True
        if not temporary or policy["transport_retries"] or attempt + 1 == policy["max_attempts"] or not can_retry():
            return None, reason, unknown
        delay = retry_delay(response, policy["delay_ms"])
        if deadline - now() <= delay:
            return None, "budget", unknown
        wait(delay)
        if now() >= deadline:
            return None, "budget", unknown
        if not can_retry():
            return None, reason, unknown
    raise AssertionError("unreachable")


def reference_list(send, wait, now, headers, *, budget_ms=200,
                   attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                   transport_retries=False):
    policy = dict(budget_ms=budget_ms, attempt_timeout_ms=attempt_timeout_ms,
                  max_attempts=max_attempts, delay_ms=delay_ms, transport_retries=transport_retries)
    deadline, items, cursor, seen = now() + policy["budget_ms"], [], None, set()
    while True:
        if cursor in seen:
            return dict(items=items, complete=False, error="cursor")
        seen.add(cursor)
        path = "/orders" + ("?" + urlencode({"cursor": cursor}) if cursor is not None else "")
        response, error, _ = reference_call(send, wait, now, "GET", path, dict(headers), None, deadline, policy, lambda: True)
        if response is None:
            return dict(items=items, complete=False, error=error)
        items.extend(response["body"]["items"])
        cursor = response["body"]["next_cursor"]
        if cursor is None:
            return dict(items=items, complete=True, error=None)


def reference_create(send, wait, now, headers, payload, *, operation_key=None,
                     key_valid_until_ms=None, budget_ms=200,
                     attempt_timeout_ms=80, max_attempts=3, delay_ms=10,
                     transport_retries=False):
    policy = dict(budget_ms=budget_ms, attempt_timeout_ms=attempt_timeout_ms,
                  max_attempts=max_attempts, delay_ms=delay_ms, transport_retries=transport_retries)
    deadline = now() + policy["budget_ms"]
    wire = {key: value for key, value in headers.items() if key.lower() != "idempotency-key"}
    key, expiry = operation_key, key_valid_until_ms
    if key:
        wire["Idempotency-Key"] = key
    valid = lambda: bool(key) and expiry is not None and now() < expiry
    response, _, unknown = reference_call(send, wait, now, "POST", "/orders", wire, payload, deadline, policy, valid)
    return ordered("success", response["body"]["id"]) if response else ordered("unknown" if unknown else "failed")


def self_check():
    from . import starter
    reference = SimpleNamespace(list_orders=reference_list, create_order=reference_create)
    rows = [evaluate(reference, case) for case in CASES]
    assert all(row["passed"] for row in rows), [row for row in rows if not row["passed"]]
    broken = {case["id"]: evaluate(starter, case)["passed"] for case in CASES}
    for name in ("list-partial-http-error", "list-shared-deadline", "create-no-key-timeout", "create-key-expires-during-wait"):
        assert not broken[name], f"grader accepted known broken caller: {name}"
    print(f"Maintenance self-check: reference {len(CASES)}/{len(CASES)}, broken callers rejected.")
