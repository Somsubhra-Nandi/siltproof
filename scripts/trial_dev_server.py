"""Local, offline backend for the judge-trial screens. Never reaches AWS.

    .venv/bin/python scripts/trial_dev_server.py            # API on :3001
    cd frontend && VITE_API_BASE_URL=http://127.0.0.1:3001 npm run dev
    open http://localhost:5173/trial.html

What runs where:

* a moto server on 127.0.0.1:5005 stands in for S3 and DynamoDB, so the
  browser really POSTs files to a presigned policy and the API really reads
  them back;
* the real API Lambda handler (backend/api/app.py) serves the routes;
* MOCK_AWS=1, so Textract and Bedrock answer from fixtures labelled
  "OFFLINE MOCK", and the analysis demotes anything computed from them;
* processing runs on a background thread after a short delay, so the
  QUEUED / PROCESSING states and the UI's polling are exercised the way the
  deployed async Lambda invoke would.

Everything lives in memory and disappears when the process stops.
"""

import argparse
import json
import os
import pathlib
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

BUCKET = "siltproof-dev-evidence"
TABLE = "siltproof-dev"


def configure(moto_port):
    os.environ.update({
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "ap-south-1",
        "AWS_REGION": "ap-south-1",
        "AWS_ENDPOINT_URL": f"http://127.0.0.1:{moto_port}",
        "MOCK_AWS": "1",
        "TABLE_NAME": TABLE,
        "EVIDENCE_BUCKET": BUCKET,
    })
    os.environ.pop("TRIAL_PROCESSOR_FUNCTION", None)


def start_moto(port):
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()

    import boto3

    s3 = boto3.client("s3", region_name="ap-south-1")
    s3.create_bucket(Bucket=BUCKET,
                     CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": ["*"], "AllowedMethods": ["GET", "PUT", "POST", "HEAD"],
        "AllowedHeaders": ["*"]}]})
    boto3.client("dynamodb", region_name="ap-south-1").create_table(
        TableName=TABLE,
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                              {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                   {"AttributeName": "sk", "KeyType": "RANGE"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return server


def delayed_dispatch(delay):
    """Replace inline processing with a delayed background run."""
    from trial import process

    def dispatch(trial_id, evidence_id):
        def later():
            time.sleep(delay)
            process.handle_event({"trialProcess": {"trialId": trial_id,
                                                   "evidenceId": evidence_id}})

        threading.Thread(target=later, daemon=True).start()
        return "dev-thread"

    process.dispatch = dispatch


def route_table():
    from api import app

    keys = list(app.ROUTES) + list(app.trial_routes.ROUTES)
    table = []
    for key in keys:
        method, path = key.split(" ", 1)
        names = re.findall(r"{(\w+)}", path)
        pattern = "^" + re.sub(r"{\w+}", r"([^/]+)", path) + "$"
        table.append((method, re.compile(pattern), names, key))
    return table


class Handler(BaseHTTPRequestHandler):
    routes = []

    def log_message(self, fmt, *args):
        sys.stderr.write("api  %s\n" % (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _handle(self):
        from api import app

        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        for method, pattern, names, key in self.routes:
            match = pattern.match(path)
            if method == self.command and match:
                event = {
                    "routeKey": key,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "pathParameters": dict(zip(names, match.groups())) or None,
                    "queryStringParameters": None,
                    "body": body,
                }
                result = app.lambda_handler(event, None)
                break
        else:
            result = {"statusCode": 404, "headers": {},
                      "body": json.dumps({"error": f"No route {self.command} {path}"})}

        payload = result["body"].encode("utf-8")
        self.send_response(result["statusCode"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = _handle


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--moto-port", type=int, default=5005)
    parser.add_argument("--delay", type=float, default=1.5,
                        help="seconds before background processing starts")
    args = parser.parse_args()

    configure(args.moto_port)
    moto = start_moto(args.moto_port)
    delayed_dispatch(args.delay)
    Handler.routes = route_table()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"trial dev API on http://127.0.0.1:{args.port} "
          f"(moto S3/DynamoDB on :{args.moto_port}, MOCK_AWS=1, nothing reaches AWS)",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        moto.stop()


if __name__ == "__main__":
    main()
