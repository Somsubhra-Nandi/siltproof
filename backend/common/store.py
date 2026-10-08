"""DynamoDB access for the single ``siltproof`` table (plan section 4).

DynamoDB has no float type, so everything written goes through ``to_dynamo``,
which turns floats into Decimals and drops values DynamoDB cannot store.
"""

import decimal
import math

from . import awsclients, config

# Item keys, kept in one place so the Lambdas and the seed script agree.
def bill_pk(bill_id=None):
    return f"BILL#{bill_id or config.bill_id()}"


def drain_sk(drain_id):
    return f"DRAIN#{drain_id}"


def trip_sk(drain_id, trip_no):
    return f"TRIP#{drain_id}#{trip_no}"


def evidence_pk(key):
    return f"EVID#{key}"


def vehicle_pk(vehicle_no):
    return f"VEHICLE#{vehicle_no}"


def dumpsite_pk(dumpsite_id="D1"):
    return f"DUMPSITE#{dumpsite_id}"


def to_dynamo(value):
    """Recursively make a value safe for DynamoDB."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return decimal.Decimal(str(round(value, 7)))
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return {str(k): to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(item) for item in value]
    return value


def from_dynamo(value):
    """Turn Decimals back into ints/floats for JSON responses."""
    if isinstance(value, decimal.Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(item) for item in value]
    return value


def table():
    return awsclients.resource("dynamodb").Table(config.table_name())


def put(item):
    """Write one item. Overwriting is what makes re-ingestion idempotent."""
    table().put_item(Item=to_dynamo(item))
    return item


def get(pk, sk):
    response = table().get_item(Key={"pk": pk, "sk": sk})
    item = response.get("Item")
    return from_dynamo(item) if item else None


def put_many(items):
    """Batch write, which the seed script uses for the bill, drains and trips."""
    handle = table()
    with handle.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
        for item in items:
            batch.put_item(Item=to_dynamo(item))
    return len(items)


def query_pk(pk, sk_prefix=None):
    """Every item under one partition key, optionally filtered by sk prefix."""
    from boto3.dynamodb.conditions import Key

    condition = Key("pk").eq(pk)
    if sk_prefix:
        condition = condition & Key("sk").begins_with(sk_prefix)

    items = []
    kwargs = {"KeyConditionExpression": condition}
    handle = table()

    while True:
        response = handle.query(**kwargs)
        items.extend(from_dynamo(item) for item in response.get("Items", []))
        token = response.get("LastEvaluatedKey")
        if not token:
            return items
        kwargs["ExclusiveStartKey"] = token


def batch_get(keys):
    """Fetch many items by (pk, sk). Returns a {(pk, sk): item} map.

    DynamoDB takes at most 100 keys per call and may return fewer than asked
    for, so this chunks and follows UnprocessedKeys.
    """
    found = {}
    unique = list({(pk, sk) for pk, sk in keys})
    resource = awsclients.resource("dynamodb")
    name = config.table_name()

    for start in range(0, len(unique), 100):
        request = {
            name: {
                "Keys": [{"pk": pk, "sk": sk} for pk, sk in unique[start:start + 100]]
            }
        }

        while request:
            response = resource.batch_get_item(RequestItems=request)
            for item in response.get("Responses", {}).get(name, []):
                found[(item["pk"], item["sk"])] = from_dynamo(item)
            request = response.get("UnprocessedKeys") or None

    return found


def scan_sk(sk, **equals):
    """Every item with this sort key, optionally filtered on attributes.

    Photo evidence lives under one partition per S3 key, so there is no
    partition to query for "all the photos on this bill". The table holds a
    few hundred items for one ward's bill, so a filtered scan is the honest
    MVP answer; a sparse GSI on billId would be the production one.
    """
    from boto3.dynamodb.conditions import Attr

    condition = Attr("sk").eq(sk)
    for name, value in equals.items():
        if value is not None:
            condition = condition & Attr(name).eq(value)

    items = []
    kwargs = {"FilterExpression": condition}
    handle = table()

    while True:
        response = handle.scan(**kwargs)
        items.extend(from_dynamo(item) for item in response.get("Items", []))
        token = response.get("LastEvaluatedKey")
        if not token:
            return items
        kwargs["ExclusiveStartKey"] = token


def update_fields(pk, sk, fields):
    """Merge a few attributes into an existing item."""
    if not fields:
        return None

    names = {f"#f{index}": name for index, name in enumerate(fields)}
    values = {f":v{index}": to_dynamo(value) for index, value in enumerate(fields.values())}
    assignments = ", ".join(
        f"{placeholder} = :v{index}" for index, placeholder in enumerate(names)
    )

    response = table().update_item(
        Key={"pk": pk, "sk": sk},
        UpdateExpression=f"SET {assignments}",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return from_dynamo(response.get("Attributes") or {})
