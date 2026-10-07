"""One-line JSON logs, so CloudWatch Logs Insights can query them."""

import json
import sys
import time


def log(event, **fields):
    record = {"event": event, "ts": round(time.time(), 3)}
    record.update(fields)
    try:
        line = json.dumps(record, default=str)
    except (TypeError, ValueError):
        line = json.dumps({"event": "log_serialise_failed", "original": event})
    print(line, file=sys.stdout, flush=True)
