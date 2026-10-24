def run(send, wait, now, *, repeat_safe, has_inner_retry,
        max_attempts, budget_ms, attempt_timeout_ms, delay_ms):
    deadline = now() + budget_ms
    attempt_limit = (
        max_attempts if repeat_safe and not has_inner_retry else 1
    )
    attempts = 0
    saw_timeout = False
    temporary_statuses = (429, 502, 503, 504)

    while attempts < attempt_limit:
        remaining = deadline - now()
        if remaining <= 0:
            break

        if attempts > 0:
            # 等待后必须仍有调用预算，否则不做无用等待。
            if remaining <= delay_ms:
                break
            wait(delay_ms)
            remaining = deadline - now()
            if remaining <= 0:
                break

        attempts += 1
        try:
            status = send(min(attempt_timeout_ms, remaining))
        except TimeoutError:
            saw_timeout = True
        else:
            if 200 <= status < 300:
                return "success"
            if status not in temporary_statuses:
                break

    return "unknown" if saw_timeout else "failed"
