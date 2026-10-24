def run(send, wait, now, *, repeat_safe, has_inner_retry,
        max_attempts, budget_ms, attempt_timeout_ms, delay_ms):
    deadline = now() + budget_ms
    saw_timeout = False
    retries_allowed = repeat_safe and not has_inner_retry

    for attempt in range(max_attempts):
        remaining = deadline - now()
        if remaining <= 0:
            break

        try:
            status = send(min(attempt_timeout_ms, remaining))
        except TimeoutError:
            saw_timeout = True
            temporary_failure = True
        else:
            if 200 <= status < 300:
                return "success"
            temporary_failure = status in (429, 502, 503, 504)

        if (not temporary_failure
                or not retries_allowed
                or attempt + 1 >= max_attempts):
            break

        remaining = deadline - now()
        if remaining <= delay_ms:
            break

        wait(delay_ms)

    return "unknown" if saw_timeout else "failed"
