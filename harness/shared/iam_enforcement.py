"""IAM enforcement contract for ACE-Bench.

LocalStack only checks IAM policies when started with ENFORCE_IAM=1. Without it,
security/permission fault scenarios silently pass. We detect enforcement with a
real-AWS call: a freshly created IAM user with no attached policies is granted an
access key, and a benign API call is attempted with those credentials. Under
enforcement the no-policy principal is implicitly denied (AccessDenied); without
enforcement the call succeeds.
"""

import boto3
from botocore.exceptions import ClientError

_ENDPOINT = "http://localhost:4566"
_CREDS = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
    "region_name": "us-east-1",
}
_PROBE_USER = "ace-enforcement-probe"
_DENY_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}


def _client(service, **creds):
    return boto3.client(service, endpoint_url=_ENDPOINT, **(creds or _CREDS))


def iam_enforcement_active() -> bool:
    """Return True if LocalStack is enforcing IAM (a no-policy principal is denied)."""
    iam = _client("iam")
    try:
        iam.create_user(UserName=_PROBE_USER)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    # Keep the probe user under the AccessKeysPerUser quota across repeated runs.
    try:
        for meta in iam.list_access_keys(UserName=_PROBE_USER).get("AccessKeyMetadata", []):
            iam.delete_access_key(UserName=_PROBE_USER, AccessKeyId=meta["AccessKeyId"])
    except ClientError:
        pass
    key = iam.create_access_key(UserName=_PROBE_USER)["AccessKey"]
    scoped = _client(
        "s3",
        aws_access_key_id=key["AccessKeyId"],
        aws_secret_access_key=key["SecretAccessKey"],
        region_name="us-east-1",
    )
    try:
        scoped.list_buckets()
        return False  # no-policy principal was allowed -> enforcement OFF
    except ClientError as exc:
        return exc.response["Error"]["Code"] in _DENY_CODES


def assert_iam_enforcement() -> None:
    """Raise RuntimeError if IAM enforcement is not active."""
    if not iam_enforcement_active():
        raise RuntimeError(
            "IAM enforcement is OFF. Security/permission scenarios cannot be "
            "scored validly. Restart LocalStack with ENFORCE_IAM=1 and "
            "IAM_SOFT_MODE=0, then re-run."
        )
