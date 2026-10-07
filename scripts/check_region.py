#!/usr/bin/env python3
"""Check that one region can run everything SiltProof needs.

Makes one minimal live call each to Bedrock, Textract and Amazon Location and
prints PASS/FAIL per service. Day 1 morning task (plan section 7).

    python scripts/check_region.py ap-south-1
    python scripts/check_region.py ap-south-1 --model-id in.anthropic.claude-sonnet-5

Costs a fraction of a cent: one tiny Bedrock completion, one Textract page and
one route calculation. It creates no AWS resources.
"""

import argparse
import io
import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageDraw

DEFAULT_MODEL_ID = "in.anthropic.claude-opus-5"

# Two points about 6 km apart in Mumbai, for the route calculation.
ROUTE_ORIGIN = [72.8777, 19.0760]       # [lon, lat]
ROUTE_DESTINATION = [72.8479, 19.1197]


def slip_image_bytes():
    """A small PNG that looks enough like a weighbridge slip for one query."""
    image = Image.new("RGB", (480, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 30), "SAMPLE DATA - WEIGHBRIDGE", fill="black")
    draw.text((20, 70), "VEHICLE NO: MH 01 AB 1234", fill="black")
    draw.text((20, 110), "NET WEIGHT: 9.80 T", fill="black")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def check_bedrock(region, model_id):
    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [
            {"role": "user", "content": "Reply with the single word: ready"}
        ],
    }
    result = client.invoke_model(modelId=model_id, body=json.dumps(body))
    payload = json.loads(result["body"].read())

    text = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    ).strip()

    return f"model={model_id} stop_reason={payload.get('stop_reason')} said={text!r}"


def check_textract(region):
    client = boto3.client("textract", region_name=region)
    result = client.analyze_document(
        Document={"Bytes": slip_image_bytes()},
        FeatureTypes=["QUERIES"],
        QueriesConfig={
            "Queries": [{"Text": "What is the net weight?", "Alias": "NET_WEIGHT"}]
        },
    )

    answers = [
        block.get("Text")
        for block in result.get("Blocks", [])
        if block.get("BlockType") == "QUERY_RESULT"
    ]
    return f"queries=1 answer={answers[0] if answers else None!r}"


def check_location(region):
    """Uses the resource-free geo-routes API, so nothing has to be created."""
    client = boto3.client("geo-routes", region_name=region)
    result = client.calculate_routes(
        Origin=ROUTE_ORIGIN,
        Destination=ROUTE_DESTINATION,
        TravelMode="Car",
    )

    routes = result.get("Routes", [])
    if not routes:
        raise RuntimeError("route calculation returned no routes")

    summary = routes[0].get("Summary", {})
    distance = summary.get("Distance")
    duration = summary.get("Duration")
    return f"routes={len(routes)} distance_m={distance} duration_s={duration}"


def run(label, fn):
    try:
        detail = fn()
    except (ClientError, BotoCoreError, RuntimeError, KeyError, ValueError) as exc:
        print(f"FAIL  {label:<10} {type(exc).__name__}: {exc}")
        return False

    print(f"PASS  {label:<10} {detail}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("region", help="AWS region to test, e.g. ap-south-1")
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help=f"Bedrock model or inference profile id (default: {DEFAULT_MODEL_ID})",
    )
    args = parser.parse_args()

    print(f"Checking region {args.region}\n")

    results = [
        run("bedrock", lambda: check_bedrock(args.region, args.model_id)),
        run("textract", lambda: check_textract(args.region)),
        run("location", lambda: check_location(args.region)),
    ]

    if all(results):
        print(f"\n{args.region} works for SiltProof.")
        return 0

    print(f"\n{args.region} is not usable as-is. Fix the FAILs or try us-east-1.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
