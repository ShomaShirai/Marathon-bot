import hashlib
import hmac
import time

SLACK_SIGNATURE_VERSION = "v0"
SLACK_SIGNATURE_TOLERANCE_SECONDS = 60 * 5


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> bool:
    if not timestamp or not signature:
        return False

    try:
        request_time = int(timestamp)
    except ValueError:
        return False

    current_time = time.time() if now is None else now
    if abs(current_time - request_time) > SLACK_SIGNATURE_TOLERANCE_SECONDS:
        return False

    base_string = b":".join(
        [
            SLACK_SIGNATURE_VERSION.encode("utf-8"),
            timestamp.encode("utf-8"),
            body,
        ]
    )
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        base_string,
        hashlib.sha256,
    ).hexdigest()
    expected_signature = f"{SLACK_SIGNATURE_VERSION}={digest}"

    return hmac.compare_digest(expected_signature, signature)
