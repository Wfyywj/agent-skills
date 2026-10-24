"""Deterministic acceptance checks; never included in generation prompts."""

POLICY = dict(repeat_safe=True, has_inner_retry=False, max_attempts=3,
              budget_ms=300, attempt_timeout_ms=90, delay_ms=20)

# Replies: (HTTP status or None for timeout, latency_ms, applies_side_effect).
# Expected: (outcome, calls, side_effects). Cases check behavior, not code text.
CASES = [
    dict(id="single-success", split="development", replies=[(200, 5, False)], expected=("success", 1, 0)),
    dict(id="temporary-read", split="development", replies=[(503, 5, False), (200, 5, False)], expected=("success", 2, 0)),
    dict(id="unsafe-write-timeout", split="development", replies=[(None, 0, True), (200, 5, True)], policy=dict(repeat_safe=False), expected=("unknown", 1, 1)),
    dict(id="permanent-error", split="holdout", replies=[(400, 5, False), (200, 5, False)], expected=("failed", 1, 0)),
    dict(id="unlisted-error", split="holdout", replies=[(500, 5, False), (200, 5, False)], expected=("failed", 1, 0)),
    dict(id="attempt-limit", split="holdout", replies=[(503, 5, False)], policy=dict(max_attempts=2), expected=("failed", 2, 0)),
    dict(id="inner-retry-owner", split="holdout", replies=[(503, 5, False), (200, 5, False)], policy=dict(has_inner_retry=True), expected=("failed", 1, 0)),
    dict(id="budget-before-wait", split="holdout", replies=[(503, 80, False), (200, 5, False)], policy=dict(budget_ms=100, delay_ms=30), expected=("failed", 1, 0)),
    dict(id="clip-attempt-timeout", split="holdout", replies=[(503, 40, False), (200, 5, False)], policy=dict(budget_ms=100, delay_ms=30), expected=("success", 2, 0)),
    dict(id="safe-timeout-deduplicated", split="holdout", replies=[(None, 0, True), (200, 5, True)], deduplicate=True, expected=("success", 2, 1)),
    dict(id="uncertainty-persists", split="holdout", replies=[(None, 0, True), (503, 5, False)], policy=dict(max_attempts=2), expected=("unknown", 2, 1)),
    dict(id="no-budget", split="holdout", replies=[(200, 5, False)], policy=dict(budget_ms=0), expected=("failed", 0, 0)),
    dict(id="throttled-read", split="holdout", replies=[(429, 5, False), (200, 5, False)], expected=("success", 2, 0)),
    dict(id="unsafe-temporary-error", split="holdout", replies=[(503, 5, False), (200, 5, False)], policy=dict(repeat_safe=False), expected=("failed", 1, 0)),
    dict(id="created-success", split="holdout", replies=[(201, 5, False)], expected=("success", 1, 0)),
    dict(id="last-2xx-success", split="holdout", replies=[(299, 5, False)], expected=("success", 1, 0)),
    dict(id="bad-gateway", split="holdout", replies=[(502, 5, False), (200, 5, False)], expected=("success", 2, 0)),
    dict(id="gateway-timeout", split="holdout", replies=[(504, 5, False), (200, 5, False)], expected=("success", 2, 0)),
    dict(id="one-attempt-only", split="holdout", replies=[(503, 5, False), (200, 5, False)], policy=dict(max_attempts=1), expected=("failed", 1, 0)),
    dict(id="third-attempt-success", split="holdout", replies=[(503, 5, False), (503, 5, False), (200, 5, False)], expected=("success", 3, 0)),
]


def evaluate(candidate, case):
    policy = POLICY | case.get("policy", {})
    start = clock = 1000
    calls = effects = 0
    last_end = None
    events, errors = [], []

    def reject(message):
        errors.append(message)  # A candidate catching this error cannot erase it.
        raise ValueError(message)

    def send(timeout_ms):
        nonlocal clock, calls, effects, last_end
        calls += 1
        events.append(dict(action="send", at_ms=clock - start, timeout_ms=timeout_ms))
        if calls > 12:
            reject("transport call safety limit exceeded")
        remaining = policy["budget_ms"] - (clock - start)
        if type(timeout_ms) is not int or remaining <= 0 or timeout_ms != min(policy["attempt_timeout_ms"], remaining):
            reject("incorrect per-attempt timeout or exhausted total budget")
        if last_end is not None and clock - last_end != policy["delay_ms"]:
            reject("missing or incorrect wait between attempts")
        status, latency, applied = case["replies"][min(calls - 1, len(case["replies"]) - 1)]
        if applied and (not case.get("deduplicate") or effects == 0):
            effects += 1
        timed_out = status is None or latency > timeout_ms
        clock += timeout_ms if timed_out else latency
        last_end = clock
        if timed_out:
            raise TimeoutError("response not received; execution outcome unknown")
        return status

    def wait(ms):
        nonlocal clock
        events.append(dict(action="wait", at_ms=clock - start, duration_ms=ms))
        if type(ms) is not int or ms != policy["delay_ms"]:
            reject("incorrect retry delay")
        if clock - start + ms >= policy["budget_ms"]:
            reject("waiting leaves no call budget")
        clock += ms

    outcome = None
    try:
        outcome = candidate(send, wait, lambda: clock, **policy)
    except Exception as exc:
        errors.append(f"candidate raised {type(exc).__name__}: {exc}")
    if (outcome, calls, effects) != case["expected"]:
        errors.append(f"expected {case['expected']!r}; got {(outcome, calls, effects)!r}")
    if events and events[-1]["action"] == "wait":
        errors.append("unnecessary final wait")
    if clock - start > policy["budget_ms"]:
        errors.append("total time budget exceeded")
    result = dict(id=case["id"], split=case["split"], passed=not errors,
                  calls=calls, elapsed_ms=clock - start, side_effects=effects, errors=errors)
    if errors:
        result["trace"] = events
    return result


def reference(send, wait, now, *, repeat_safe, has_inner_retry, max_attempts,
              budget_ms, attempt_timeout_ms, delay_ms):
    """Handwritten self-check oracle, never a model baseline or trial result."""
    deadline = now() + budget_ms
    unknown = False
    for attempt in range(max_attempts):
        remaining = deadline - now()
        if remaining <= 0:
            break
        try:
            status = send(min(attempt_timeout_ms, remaining))
            if 200 <= status < 300:
                return "success"
            temporary = status in (429, 502, 503, 504)
        except TimeoutError:
            unknown, temporary = True, True
        if not temporary or not repeat_safe or has_inner_retry:
            break
        if attempt + 1 == max_attempts or deadline - now() <= delay_ms:
            break
        wait(delay_ms)
    return "unknown" if unknown else "failed"


def self_check():
    results = [evaluate(reference, case) for case in CASES]
    assert all(row["passed"] for row in results), results

    def reckless(send, wait, now, **policy):
        # Ignores operation safety, permanent errors and an existing retry owner.
        deadline = now() + policy["budget_ms"]
        unknown = False
        for attempt in range(policy["max_attempts"]):
            if deadline <= now():
                break
            try:
                if 200 <= send(min(policy["attempt_timeout_ms"], deadline - now())) < 300:
                    return "success"
            except TimeoutError:
                unknown = True
            if attempt + 1 == policy["max_attempts"] or deadline - now() <= policy["delay_ms"]:
                break
            wait(policy["delay_ms"])
        return "unknown" if unknown else "failed"

    bad = {case["id"]: evaluate(reckless, case)["passed"] for case in CASES}
    for name in ("unsafe-write-timeout", "permanent-error", "inner-retry-owner"):
        assert not bad[name], f"grader accepted known bad behavior: {name}"
    for candidate in (lambda *args, **kwargs: "success", lambda *args, **kwargs: "failed"):
        assert not evaluate(candidate, CASES[0])["passed"], "grader accepted no execution"
    def fixed_two(send, wait, now, **policy):
        return reference(send, wait, now, **(policy | {"max_attempts": 2}))
    for name in ("one-attempt-only", "third-attempt-success"):
        case = next(case for case in CASES if case["id"] == name)
        assert not evaluate(fixed_two, case)["passed"], f"grader accepted fixed attempt count: {name}"
    print(f"Self-check passed: reference {len(CASES)}/{len(CASES)}; known bad behaviors rejected.")
