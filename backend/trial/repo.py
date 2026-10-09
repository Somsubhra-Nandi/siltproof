"""DynamoDB access for trials, in the existing single table.

Layout (docs/JUDGE-TRIAL-API.md section 14):

    pk TRIAL#{trialId}   sk META            the trial, its details and counters
    pk TRIAL#{trialId}   sk EVID#{evidId}   one item per file
    pk TRIAL#{trialId}   sk RESULT#LATEST   the latest analysis
    pk QUOTA#{yyyy-mm-dd} sk {kind}         global daily counters

Every counter that protects money or storage is changed with a conditional
update, so two concurrent requests can never both take the last unit.
Nothing here can address a BILL#, EVID#{s3 key}, DUMPSITE# or VEHICLE# item:
every key is built from a validated trial or evidence ID.
"""

import datetime
import time

from botocore.exceptions import ClientError

from common import store

from . import ids, limits

META = "META"
RESULT = "RESULT#LATEST"


class LimitReached(Exception):
    """A conditional reservation was refused."""

    def __init__(self, what):
        super().__init__(what)
        self.what = what


class StateConflict(Exception):
    """An evidence item was not in the state a transition expected."""


def trial_pk(trial_id):
    if not ids.valid_trial_id(trial_id):
        raise ValueError("bad trial id")
    return f"TRIAL#{trial_id}"


def evidence_sk(evidence_id):
    if not ids.valid_evidence_id(evidence_id):
        raise ValueError("bad evidence id")
    return f"EVID#{evidence_id}"


def now_epoch():
    return int(time.time())


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _conditional_failed(exc):
    return (
        isinstance(exc, ClientError)
        and exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
    )


# ------------------------------------------------------------------ trials
def create_trial(item):
    store.table().put_item(
        Item=store.to_dynamo(item),
        ConditionExpression="attribute_not_exists(pk)",
    )
    return item


def get_trial(trial_id):
    return store.get(trial_pk(trial_id), META)


def update_trial(trial_id, fields, remove=()):
    names, values, sets = {}, {}, []
    for index, (name, value) in enumerate(fields.items()):
        names[f"#s{index}"] = name
        values[f":s{index}"] = store.to_dynamo(value)
        sets.append(f"#s{index} = :s{index}")
    removes = []
    for index, name in enumerate(remove):
        names[f"#r{index}"] = name
        removes.append(f"#r{index}")

    expression = ""
    if sets:
        expression += "SET " + ", ".join(sets)
    if removes:
        expression += " REMOVE " + ", ".join(removes)
    if not expression:
        return get_trial(trial_id)

    kwargs = {
        "Key": {"pk": trial_pk(trial_id), "sk": META},
        "UpdateExpression": expression.strip(),
        "ExpressionAttributeNames": names,
        "ConditionExpression": "attribute_exists(pk)",
        "ReturnValues": "ALL_NEW",
    }
    if values:
        kwargs["ExpressionAttributeValues"] = values
    response = store.table().update_item(**kwargs)
    return store.from_dynamo(response.get("Attributes") or {})


def reserve_file(trial_id, group, size_bytes):
    """Take one file slot and size_bytes of the trial's byte budget, atomically."""
    spec = limits.GROUPS[group]
    try:
        store.table().update_item(
            Key={"pk": trial_pk(trial_id), "sk": META},
            UpdateExpression=(
                "SET fileCount = fileCount + :one, bytesReserved = bytesReserved + :size, "
                "groupFiles.#g = groupFiles.#g + :one"
            ),
            ConditionExpression=(
                "attribute_exists(pk) AND fileCount < :maxFiles "
                "AND bytesReserved <= :byteRoom AND groupFiles.#g < :groupMax"
            ),
            ExpressionAttributeNames={"#g": group},
            ExpressionAttributeValues={
                ":one": 1,
                ":size": int(size_bytes),
                ":maxFiles": limits.MAX_FILES_PER_TRIAL,
                ":byteRoom": limits.MAX_BYTES_PER_TRIAL - int(size_bytes),
                ":groupMax": spec["maxFiles"],
            },
        )
    except ClientError as exc:
        if _conditional_failed(exc):
            raise LimitReached("files") from exc
        raise


def release_file(trial_id, group, size_bytes):
    """Give back a slot when a file is rejected or deleted. Never below zero."""
    try:
        store.table().update_item(
            Key={"pk": trial_pk(trial_id), "sk": META},
            UpdateExpression=(
                "SET fileCount = fileCount - :one, bytesReserved = bytesReserved - :size, "
                "groupFiles.#g = groupFiles.#g - :one"
            ),
            ConditionExpression=(
                "attribute_exists(pk) AND fileCount >= :one AND bytesReserved >= :size "
                "AND groupFiles.#g >= :one"
            ),
            ExpressionAttributeNames={"#g": group},
            ExpressionAttributeValues={":one": 1, ":size": int(size_bytes)},
        )
    except ClientError as exc:
        if not _conditional_failed(exc):
            raise


def reserve_trial_counter(trial_id, field, limit):
    """Add one to a per-trial counter if it is below limit. Returns the new value."""
    if int(limit) <= 0:
        raise LimitReached(field)
    try:
        response = store.table().update_item(
            Key={"pk": trial_pk(trial_id), "sk": META},
            UpdateExpression="SET #c = if_not_exists(#c, :zero) + :one",
            ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(#c) OR #c < :limit)",
            ExpressionAttributeNames={"#c": field},
            ExpressionAttributeValues={":zero": 0, ":one": 1, ":limit": int(limit)},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as exc:
        if _conditional_failed(exc):
            raise LimitReached(field) from exc
        raise
    return int(response["Attributes"][field])


def reserve_daily(kind, limit, *, today=None):
    """Take one unit of a global daily allowance (UTC day), or raise LimitReached.

    The reservation happens before the protected call and is never refunded:
    a call that fails may still have been billed.
    """
    if int(limit) <= 0:
        raise LimitReached(f"daily:{kind}")
    day = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    try:
        response = store.table().update_item(
            Key={"pk": f"QUOTA#{day}", "sk": kind},
            UpdateExpression="SET #n = if_not_exists(#n, :zero) + :one, #t = :ttl",
            ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
            ExpressionAttributeNames={"#n": "count", "#t": "ttl"},
            ExpressionAttributeValues={
                ":zero": 0,
                ":one": 1,
                ":limit": int(limit),
                ":ttl": now_epoch() + limits.QUOTA_TTL_S,
            },
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as exc:
        if _conditional_failed(exc):
            raise LimitReached(f"daily:{kind}") from exc
        raise
    return int(response["Attributes"]["count"])


def daily_count(kind, *, today=None):
    day = today or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    item = store.get(f"QUOTA#{day}", kind)
    return int((item or {}).get("count") or 0)


# ---------------------------------------------------------------- evidence
def put_evidence(trial_id, item):
    item = {**item, "pk": trial_pk(trial_id), "sk": evidence_sk(item["evidenceId"])}
    store.put(item)
    return item


def get_evidence(trial_id, evidence_id):
    return store.get(trial_pk(trial_id), evidence_sk(evidence_id))


def list_evidence(trial_id):
    items = store.query_pk(trial_pk(trial_id), "EVID#")
    items.sort(key=lambda item: (item.get("createdAt") or "", item.get("evidenceId") or ""))
    return items


def update_evidence(trial_id, evidence_id, fields, *, expect_states=None, remove=()):
    """Merge fields into an evidence item, optionally only from given states."""
    names, values, sets = {}, {}, []
    for index, (name, value) in enumerate(fields.items()):
        names[f"#s{index}"] = name
        values[f":s{index}"] = store.to_dynamo(value)
        sets.append(f"#s{index} = :s{index}")
    removes = []
    for index, name in enumerate(remove):
        names[f"#r{index}"] = name
        removes.append(f"#r{index}")

    condition = "attribute_exists(pk)"
    if expect_states:
        names["#state"] = "state"
        placeholders = []
        for index, state in enumerate(expect_states):
            values[f":e{index}"] = state
            placeholders.append(f":e{index}")
        condition += f" AND #state IN ({', '.join(placeholders)})"

    expression = "SET " + ", ".join(sets)
    if removes:
        expression += " REMOVE " + ", ".join(removes)

    try:
        response = store.table().update_item(
            Key={"pk": trial_pk(trial_id), "sk": evidence_sk(evidence_id)},
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if _conditional_failed(exc):
            raise StateConflict(evidence_id) from exc
        raise
    return store.from_dynamo(response.get("Attributes") or {})


def claim_for_processing(trial_id, evidence_id):
    """QUEUED (or PROCESSING with an expired lease) -> PROCESSING, atomically.

    Returns the claimed item, or None when another invocation owns it or it
    is already finished. This is what makes a duplicate Lambda invocation a
    no-op instead of a second billable model call.
    """
    now = now_epoch()
    try:
        response = store.table().update_item(
            Key={"pk": trial_pk(trial_id), "sk": evidence_sk(evidence_id)},
            UpdateExpression="SET #state = :processing, leaseUntil = :lease, updatedAt = :at",
            ConditionExpression=(
                "attribute_exists(pk) AND (#state = :queued OR "
                "(#state = :processing AND leaseUntil < :now))"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":processing": "PROCESSING",
                ":queued": "QUEUED",
                ":lease": now + limits.PROCESSING_LEASE_S,
                ":now": now,
                ":at": now_iso(),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if _conditional_failed(exc):
            return None
        raise
    return store.from_dynamo(response.get("Attributes") or {})


def delete_evidence_item(trial_id, evidence_id):
    store.table().delete_item(Key={"pk": trial_pk(trial_id), "sk": evidence_sk(evidence_id)})


# ------------------------------------------------------------------ results
def put_result(trial_id, result, expires_epoch):
    store.put({"pk": trial_pk(trial_id), "sk": RESULT, "ttl": expires_epoch, **result})


def get_result(trial_id):
    item = store.get(trial_pk(trial_id), RESULT)
    if item:
        for name in ("pk", "sk", "ttl"):
            item.pop(name, None)
    return item


# ------------------------------------------------------------------- delete
def delete_trial_items(trial_id):
    """Every item in the trial's partition. Returns how many were deleted."""
    items = store.query_pk(trial_pk(trial_id))
    handle = store.table()
    with handle.batch_writer() as batch:
        for item in items:
            batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    return len(items)
