"""Server-generated identifiers and access tokens."""

import base64
import hashlib
import hmac
import re
import secrets

TRIAL_ID = re.compile(r"tr_[a-z2-7]{26}")
EVIDENCE_ID = re.compile(r"ev_[a-z2-7]{20}")


def _base32(nbytes, length):
    raw = base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").lower()
    return raw.rstrip("=")[:length]


def new_trial_id():
    return "tr_" + _base32(17, 26)       # 130 bits drawn, 128+ kept


def new_evidence_id():
    return "ev_" + _base32(13, 20)       # 100 bits kept


def new_analysis_id():
    return "an_" + _base32(10, 16)


def new_token():
    """256 random bits, URL-safe. Returned once; only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def token_matches(token, stored_hash):
    if not token or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), str(stored_hash))


def valid_trial_id(value):
    return isinstance(value, str) and TRIAL_ID.fullmatch(value) is not None


def valid_evidence_id(value):
    return isinstance(value, str) and EVIDENCE_ID.fullmatch(value) is not None
