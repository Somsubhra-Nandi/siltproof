import json


def lambda_handler(event, context):
    records = event.get("Records", [])

    for record in records:
        bucket = record.get("s3", {}).get("bucket", {}).get("name")
        key = record.get("s3", {}).get("object", {}).get("key")

        print(
            json.dumps(
                {
                    "event": "evidence_received",
                    "bucket": bucket,
                    "key": key,
                }
            )
        )

    return {
        "processed": len(records),
    }
