"""
functional_test.py - Fuzzy Movie Search
corpus/arch_02_fuzzy_movie_search/

Verifies that a movie document can travel from the ingest function URL through Kinesis and Firehose into Elasticsearch, then be returned by the search function URL.

Output format: ASSERT pass|fail [name]: [message]
Primary assertions: no suffix - all must pass for a run to score
Secondary assertions: _secondary suffix - tracked for regression check
Exit code: always 0
"""

import json
import sys
import time
import uuid
from urllib import error, parse, request

import boto3

from harness.shared.functional_test_helpers import emit_fail, emit_pass, finalize

ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
STACK_NAME = "ace-bench-stack"
WAIT_TIMEOUT_SECONDS = 180


def client(service):
    return boto3.client(service, endpoint_url=ENDPOINT, region_name=REGION, **CREDS)


def get_stack_output(key):
    cfn = client("cloudformation")
    stacks = cfn.describe_stacks(StackName=STACK_NAME).get("Stacks", [])
    if not stacks:
        raise RuntimeError(f"Stack not found: {STACK_NAME}")
    for output in stacks[0].get("Outputs", []):
        if output.get("OutputKey") == key:
            return output.get("OutputValue")
    raise KeyError(f"Missing CloudFormation output: {key}")


def http_request(url, method="GET", payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return resp.status, parsed
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp else ""
        try:
            parsed = json.loads(raw) if raw else {"raw": raw}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def function_url_candidates(url):
    return [url]


def assert_ingest_function_accepts_document(ingest_url, movie):
    last = None
    for url in function_url_candidates(ingest_url):
        status, payload = http_request(url, method="POST", payload=movie)
        last = (status, payload)
        if status in {200, 202}:
            emit_pass("ingest_function_accepts_document", f"status={status}")
            return True
    emit_fail("ingest_function_accepts_document", f"last={last}")
    return False


def elasticsearch_search(endpoint, query):
    url = f"http://{endpoint}/movies/_search"
    payload = {
        "query": {
            "multi_match": {
                "fields": ["title", "directors", "actors"],
                "query": query,
                "fuzziness": "AUTO",
                "type": "best_fields",
            }
        }
    }
    return http_request(url, method="POST", payload=payload)


def wait_for_elasticsearch_hit(endpoint, title_fragment):
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last_payload = None
    while time.time() < deadline:
        status, payload = elasticsearch_search(endpoint, title_fragment)
        last_payload = payload
        if status == 200:
            hits = payload.get("hits", {}).get("hits", []) if isinstance(payload, dict) else []
            for hit in hits:
                source = hit.get("_source", {})
                if title_fragment in source.get("title", ""):
                    return True, payload
        time.sleep(5)
    return False, last_payload


def assert_document_indexed_in_elasticsearch(endpoint, title):
    found, payload = wait_for_elasticsearch_hit(endpoint, title)
    if found:
        emit_pass("document_indexed_in_elasticsearch", "movie document appeared in Elasticsearch")
    else:
        emit_fail("document_indexed_in_elasticsearch", f"last_payload={payload}")


def assert_search_function_returns_document(search_url, title):
    encoded = parse.urlencode({"q": title})
    deadline = time.time() + WAIT_TIMEOUT_SECONDS
    last = None
    while time.time() < deadline:
        status, payload = http_request(f"{search_url}?{encoded}")
        last = (status, payload)
        if status == 200 and isinstance(payload, list):
            if any(title in item.get("title", "") for item in payload):
                emit_pass("search_function_returns_document", "search function returned indexed movie")
                return
        time.sleep(5)
    emit_fail("search_function_returns_document", f"last={last}")


def assert_stream_active_secondary(stream_name):
    kinesis = client("kinesis")
    status = kinesis.describe_stream(StreamName=stream_name)["StreamDescription"]["StreamStatus"]
    if status == "ACTIVE":
        emit_pass("stream_active_secondary", f"status={status}")
    else:
        emit_fail("stream_active_secondary", f"status={status}")


def assert_firehose_active_secondary(stream_name):
    firehose = client("firehose")
    status = firehose.describe_delivery_stream(DeliveryStreamName=stream_name)["DeliveryStreamDescription"]["DeliveryStreamStatus"]
    if status == "ACTIVE":
        emit_pass("firehose_active_secondary", f"status={status}")
    else:
        emit_fail("firehose_active_secondary", f"status={status}")


def assert_website_bucket_exists_secondary(bucket_name):
    s3 = client("s3")
    try:
        s3.head_bucket(Bucket=bucket_name)
        emit_pass("website_bucket_exists_secondary", f"bucket={bucket_name}")
    except Exception as exc:  # pylint: disable=broad-except
        emit_fail("website_bucket_exists_secondary", str(exc))


if __name__ == "__main__":
    try:
        ingest_url = get_stack_output("IngestLambdaUrl")
        search_url = get_stack_output("SearchLambdaUrl")
        elasticsearch_endpoint = get_stack_output("ElasticsearchEndpoint")
        ingest_stream_name = get_stack_output("IngestStreamName")
        firehose_stream_name = get_stack_output("FirehoseStreamName")
        website_bucket_name = get_stack_output("WebsiteBucketName")

        suffix = str(uuid.uuid4())[:8]
        title = f"ACE Tarantino Test {suffix}"
        movie = {
            "title": title,
            "directors": ["Quentin Tarantino"],
            "actors": ["Uma Thurman"],
            "year": 1994,
            "genres": ["Crime"],
        }

        accepted = assert_ingest_function_accepts_document(ingest_url, movie)
        if accepted:
            assert_document_indexed_in_elasticsearch(elasticsearch_endpoint, title)
            assert_search_function_returns_document(search_url, title)
        else:
            emit_fail("document_indexed_in_elasticsearch", "ingest setup failed")
            emit_fail("search_function_returns_document", "ingest setup failed")

        assert_stream_active_secondary(ingest_stream_name)
        assert_firehose_active_secondary(firehose_stream_name)
        assert_website_bucket_exists_secondary(website_bucket_name)
    except Exception as exc:  # pylint: disable=broad-except
        emit_fail("test_harness_exception", str(exc))

    finalize()
    sys.exit(0)
