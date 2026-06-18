"""Shared X-Ray instrumentation for arch01 handlers.

Begins an explicit segment (LocalStack provides no Lambda facade segment),
patches boto3 so downstream calls become subsegments, and emits each finished
segment to LocalStack via PutTraceSegments (the proven emission path on this
build). Handler-facing usage (`@traced(...)` + patch_all) matches real-AWS
X-Ray instrumentation; only the emitter is environment-specific.

LocalStack sets LAMBDA_TASK_ROOT which makes the aws-xray-sdk switch to
LambdaContext (a no-op context that discards explicit segments). We bypass
that by explicitly passing a plain Context() to configure(), which overrides
the auto-detected LambdaContext.
"""
import os
import boto3
from aws_xray_sdk.core import xray_recorder, patch_all
from aws_xray_sdk.core.context import Context
from aws_xray_sdk.core.emitters.udp_emitter import UDPEmitter

_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or "http://localhost.localstack.cloud:4566"
_xray_client = boto3.client(
    "xray", endpoint_url=_ENDPOINT, region_name=os.environ.get("AWS_REGION", "us-east-1")
)


class PutSegmentsEmitter(UDPEmitter):
    """Emit finished entities via the X-Ray API instead of UDP to a daemon."""

    def send_entity(self, entity):
        try:
            self._xray = _xray_client
            self._xray.put_trace_segments(TraceSegmentDocuments=[entity.serialize()])
        except Exception:
            # Never let trace emission break a handler.
            pass


xray_recorder.configure(
    context_missing="LOG_ERROR",
    sampling=False,
    emitter=PutSegmentsEmitter(),
    # Override LambdaContext (which no-ops begin_segment) with a plain Context.
    # LocalStack sets LAMBDA_TASK_ROOT, causing the SDK to auto-select
    # LambdaContext on init. Passing context= here wins over that detection.
    context=Context(),
)
patch_all()


def traced(name):
    """Wrap a Lambda handler so its work runs inside an X-Ray segment."""

    def decorator(fn):
        def wrapper(event, context):
            xray_recorder.begin_segment(name)
            try:
                return fn(event, context)
            except Exception as exc:  # noqa: BLE001 - record then re-raise
                segment = xray_recorder.current_segment()
                if segment is not None:
                    segment.add_exception(exc, None)
                raise
            finally:
                xray_recorder.end_segment()

        return wrapper

    return decorator
