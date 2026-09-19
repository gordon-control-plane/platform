import json

def truncate_context(messages: list, max_tokens: int = 4000) -> list:
    """
    Intelligent Shunt Middleware: Deterministic Token Optimization.
    Ensures that system messages are pinned and never truncated.
    If the context is too large (approximated here by char length for speed,
    but theoretically using tiktoken), older user/assistant messages are removed.
    """
    if not messages:
        return messages

    # Pin system messages (must never be truncated)
    system_messages = [m for m in messages if m.get("role") == "system"]

    # Process other messages (user/assistant)
    other_messages = [m for m in messages if m.get("role") != "system"]

    # Approximate token count (roughly 4 chars per token)
    def estimate_tokens(msg):
        # Incomplete Token Estimation Schema in Shunt Middleware Dropping Tool Calls (DB Reviewer)
        content_len = len(str(msg.get("content", "")))
        tool_calls = str(msg.get("tool_calls", ""))
        function_call = str(msg.get("function_call", ""))
        return (content_len + len(tool_calls) + len(function_call)) // 4

    sys_tokens = sum(estimate_tokens(m) for m in system_messages)

    # If system messages alone exceed limit (rare), we must pass them anyway,
    # but we can't include any other messages.
    if sys_tokens >= max_tokens:
        return system_messages

    allowed_tokens_for_others = max_tokens - sys_tokens

    # Retain from the most recent (end of list) backwards
    retained_others = []
    current_other_tokens = 0

    for msg in reversed(other_messages):
        msg_tokens = estimate_tokens(msg)
        if current_other_tokens + msg_tokens <= allowed_tokens_for_others:
            retained_others.append(msg)
            current_other_tokens += msg_tokens
        else:
            # Reached capacity, discard older messages
            break

    # Fix O(N^2) List Insertion
    retained_others.reverse()

    # Reassemble: Preserve original message order
    retained_set = {id(m) for m in system_messages + retained_others}
    return [m for m in messages if id(m) in retained_set]

def apply_shunt_middleware(payload: bytes, user: str = None) -> bytes:
    """
    Reads the raw HTTP request payload, applies context truncation,
    and returns the optimized payload.
    """
    try:
        data = json.loads(payload.decode('utf-8'))
        if "messages" in data and isinstance(data["messages"], list):
            data["messages"] = truncate_context(data["messages"], max_tokens=8000)
        if user:
            data["user"] = user
        return json.dumps(data).encode('utf-8')
    except Exception:
        pass

    return payload
