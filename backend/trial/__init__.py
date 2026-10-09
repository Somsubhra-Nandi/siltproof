"""Judge self-service trials (docs/JUDGE-TRIAL-API.md).

A trial is an isolated evidence case with its own ID, access token, S3 prefix
and DynamoDB partition. It never reads or writes Bill B1. The B1 rules in
``common.rules`` are reused, never edited: ``analysis`` adapts them and adds
the prerequisite checks that turn missing evidence into NOT_EVALUATED.
"""
