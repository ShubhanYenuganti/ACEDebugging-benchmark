import boto3

_ENDPOINT = "http://localhost:4566"
_CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
}


def _client(service: str):
    return boto3.client(service, endpoint_url=_ENDPOINT, **_CREDS)


cf_client = _client("cloudformation")
lambda_client = _client("lambda")
s3_client = _client("s3")
sqs_client = _client("sqs")
sns_client = _client("sns")
iam_client = _client("iam")
logs_client = _client("logs")
apigateway_client = _client("apigateway")


def health_check() -> None:
    try:
        cf_client.list_stacks()
    except Exception as exc:
        raise RuntimeError(
            f"LocalStack is not reachable at {_ENDPOINT}: {exc}"
        ) from exc
