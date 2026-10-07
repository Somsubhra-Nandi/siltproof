import json


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    request_context = event.get("requestContext", {})
    http = request_context.get("http", {})

    path = event.get("rawPath", "/")
    method = http.get("method", "GET")

    if path == "/health" and method == "GET":
        return response(
            200,
            {
                "service": "siltproof-api",
                "status": "ok",
            },
        )

    return response(
        404,
        {
            "message": "Not found",
        },
    )
