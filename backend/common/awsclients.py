"""boto3 clients, cached per process, with adaptive retries switched on."""

import boto3
from botocore.config import Config

from . import config

_CACHE = {}

_RETRY_CONFIG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=55,
)


def client(service, region_name=None):
    key = (service, region_name or config.region())
    if key not in _CACHE:
        _CACHE[key] = boto3.client(service, region_name=key[1], config=_RETRY_CONFIG)
    return _CACHE[key]


def resource(service, region_name=None):
    key = ("resource:" + service, region_name or config.region())
    if key not in _CACHE:
        _CACHE[key] = boto3.resource(service, region_name=key[1], config=_RETRY_CONFIG)
    return _CACHE[key]


def reset_cache():
    """Tests swap credentials and regions between cases."""
    _CACHE.clear()
