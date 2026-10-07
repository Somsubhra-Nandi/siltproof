"""Upload the whole demo dataset and write its DynamoDB records.

Plan section 7 (Day 1 evening): push photos/, slips/, traces/ and bill.json to
the evidence bucket (which triggers the ingest Lambda), then write BILL#,
DRAIN#, TRIP#, VEHICLE# and DUMPSITE# items.

Run after `sam deploy`, with the stack outputs in .env.
"""

# TODO Day 1 evening: S3 upload + DynamoDB writes.
