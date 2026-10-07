"""Retry with exponential backoff and jitter, for throttled AWS calls.

Bedrock and Textract both throttle hard while a seed is running (plan section 9),
and the ingest Lambda is capped at 3 concurrent executions to keep that rare.
"""

import random
import time

from botocore.exceptions import ClientError

from .jsonlog import log

# Error codes worth trying again.
THROTTLE_CODES = {
    "ThrottlingException",
    "Throttling",
    "TooManyRequestsException",
    "ProvisionedThroughputExceededException",
    "RequestLimitExceeded",
    "ServiceUnavailableException",
    "ServiceUnavailable",
    "InternalServerException",
    "InternalServerError",
    "ModelTimeoutException",
    "ModelNotReadyException",
    "LimitExceededException",
    "SlowDown",
}


def is_throttle(exc):
    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code", "")
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    return code in THROTTLE_CODES or status == 429 or status >= 500


def call_with_retry(fn, *, what, attempts=5, base_delay=1.0, max_delay=20.0, sleep=time.sleep):
    """Call fn(), retrying throttles. Raises the last error if all attempts fail."""
    last = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ClientError as exc:
            last = exc
            if not is_throttle(exc) or attempt == attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay / 2)
            log(
                "retry_after_throttle",
                what=what,
                attempt=attempt,
                of=attempts,
                delay_s=round(delay, 2),
                code=exc.response.get("Error", {}).get("Code"),
            )
            sleep(delay)

    raise last
