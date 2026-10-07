"""Shared fixtures. Every test runs offline.

Textract, Bedrock and Location are mocked through MOCK_AWS=1, which returns
canned API-shaped responses that the real parsers parse. S3 and DynamoDB are
mocked with moto, so the ingest Lambda's own boto3 calls run unchanged.
"""

import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "data"))

BUCKET = "siltproof-test-evidence"
TABLE = "siltproof-test"


@pytest.fixture(autouse=True)
def offline_env(monkeypatch, tmp_path):
    """No real credentials, no real region, no real calls."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("MOCK_AWS", "1")
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("EVIDENCE_BUCKET", BUCKET)
    monkeypatch.setenv("BILL_ID", "B1")
    monkeypatch.delenv("MOCK_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("FORCE_REINGEST", raising=False)

    from common import awsclients

    awsclients.reset_cache()
    yield
    awsclients.reset_cache()


@pytest.fixture
def aws(offline_env):
    """A moto-backed S3 bucket and DynamoDB table, wired to the env above."""
    from moto import mock_aws as moto_mock

    with moto_mock():
        import boto3

        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
        )

        dynamodb = boto3.client("dynamodb", region_name="ap-south-1")
        dynamodb.create_table(
            TableName=TABLE,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        from common import awsclients

        awsclients.reset_cache()
        yield {"s3": s3, "dynamodb": dynamodb, "bucket": BUCKET, "table": TABLE}
        awsclients.reset_cache()


@pytest.fixture
def fixture_response():
    """Load a provider fixture by name."""
    def load(name):
        path = REPO / "backend" / "common" / "fixtures" / f"{name}.json"
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    return load


def s3_event(bucket, *keys):
    """An S3 ObjectCreated event for the ingest handler."""
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": "ObjectCreated:Put",
                "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
            }
            for key in keys
        ]
    }
