import { CloudTrailClient, LookupEventsCommand } from "@aws-sdk/client-cloudtrail";
import {
  XRayClient, GetTraceSummariesCommand, BatchGetTracesCommand,
} from "@aws-sdk/client-xray";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const cloudTrailClient = new CloudTrailClient(awsConfig);
const xrayClient = new XRayClient(awsConfig);

export const observeTracingTools = [
  {
    name: "ace_lookup_events",
    description:
      "CloudTrail LookupEvents: recent API-call history over a window, surfacing each call's resources and any error_code/error_message. Optional single filter: event_name OR resource_name (CloudTrail allows one lookup attribute; event_name takes precedence). Defaults: last 60 min, 50 events. Use to see what the system actually did (which calls ran, against which resources).",
    inputSchema: {
      type: "object",
      properties: {
        window_minutes: { type: "number" },
        max_results: { type: "number" },
        event_name: { type: "string" },
        resource_name: { type: "string" },
      },
      required: [],
    },
    async handler({ window_minutes = 60, max_results = 50, event_name, resource_name } = {}) {
      const clampedWindow = Math.min(Math.max(1, window_minutes ?? 60), 1440);
      const clampedMax = Math.min(Math.max(1, max_results ?? 50), 100);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clampedWindow * 60 * 1000);
      const lookupAttributes = [];
      if (event_name) {
        lookupAttributes.push({ AttributeKey: "EventName", AttributeValue: event_name });
      } else if (resource_name) {
        lookupAttributes.push({ AttributeKey: "ResourceName", AttributeValue: resource_name });
      }
      try {
        const res = await cloudTrailClient.send(
          new LookupEventsCommand({
            StartTime: startTime,
            EndTime: endTime,
            MaxResults: clampedMax,
            LookupAttributes: lookupAttributes.length ? lookupAttributes : undefined,
          })
        );
        const events = (res.Events ?? []).map((e) => {
          let detail = {};
          try { detail = JSON.parse(e.CloudTrailEvent ?? "{}"); } catch { detail = {}; }
          return {
            event_name: e.EventName,
            event_time: e.EventTime,
            event_source: e.EventSource,
            username: e.Username,
            error_code: detail.errorCode ?? null,
            error_message: detail.errorMessage ?? null,
            resources: (e.Resources ?? []).map((r) => ({ type: r.ResourceType, name: r.ResourceName })),
          };
        });
        return { events, count: events.length, window_minutes: clampedWindow };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "CLOUDTRAIL_ERROR" };
      }
    },
  },
  {
    name: "ace_get_trace_summaries",
    description:
      "X-Ray GetTraceSummaries: list recent trace summaries over a window (id, duration, response_time, http_status, entry_service), to find requests to inspect; then call ace_get_trace for the full segment tree. Defaults: last 60 min. Returns nothing unless scenario handlers are X-Ray-instrumented. NOTE: error/fault/throttle flags are NOT reliably populated at the summary level on LocalStack, and the optional filter_expression / only_errors server-side filter is NOT supported on LocalStack (it returns an error) — to find failures, list traces by window here and inspect each via ace_get_trace, whose subsegment-level error/fault flags are accurate.",
    inputSchema: {
      type: "object",
      properties: {
        window_minutes: { type: "number" },
        filter_expression: { type: "string" },
        only_errors: { type: "boolean" },
      },
      required: [],
    },
    async handler({ window_minutes = 60, filter_expression, only_errors = false } = {}) {
      const clampedWindow = Math.min(Math.max(1, window_minutes ?? 60), 1440);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clampedWindow * 60 * 1000);
      const effectiveFilter = filter_expression ?? (only_errors ? "error = true OR fault = true" : undefined);
      try {
        const res = await xrayClient.send(new GetTraceSummariesCommand({
          StartTime: startTime,
          EndTime: endTime,
          Sampling: false,
          FilterExpression: effectiveFilter,
        }));
        const traces = (res.TraceSummaries ?? []).map((s) => ({
          id: s.Id,
          duration: s.Duration ?? null,
          response_time: s.ResponseTime ?? null,
          has_error: s.HasError ?? false,
          has_fault: s.HasFault ?? false,
          has_throttle: s.HasThrottle ?? false,
          http_status: s.Http?.HttpStatus ?? null,
          entry_service: (s.ServiceIds ?? [])[0]?.Name ?? null,
        }));
        return { traces, count: traces.length, window_minutes: clampedWindow };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
  {
    name: "ace_get_trace",
    description:
      "X-Ray BatchGetTraces: fetch the full segment tree for a single trace by ID. Returns each segment's metadata (name, duration, error/fault/throttle flags, http_status) and its subsegments (aws_operation, namespace, error, fault). Use after ace_get_trace_summaries to drill into a specific failing trace.",
    inputSchema: {
      type: "object",
      properties: {
        trace_id: { type: "string" },
      },
      required: ["trace_id"],
    },
    async handler({ trace_id } = {}) {
      if (!trace_id) return { error: "trace_id is required", error_type: "INVALID_INPUT" };
      try {
        const res = await xrayClient.send(new BatchGetTracesCommand({ TraceIds: [trace_id] }));
        const trace = (res.Traces ?? [])[0];
        if (!trace) return { trace_id, segments: [] };
        const segments = (trace.Segments ?? []).map((seg) => {
          let doc = {};
          try { doc = JSON.parse(seg.Document ?? "{}"); } catch { doc = {}; }
          const subsegments = (doc.subsegments ?? []).map((sub) => ({
            name: sub.name ?? null,
            namespace: sub.namespace ?? null,
            error: !!sub.error,
            fault: !!sub.fault,
            http_status: sub.http?.response?.status ?? null,
            aws_operation: sub.aws?.operation ?? null,
          }));
          const duration = (typeof doc.end_time === "number" && typeof doc.start_time === "number")
            ? +(doc.end_time - doc.start_time).toFixed(3) : null;
          return {
            name: doc.name ?? null,
            origin: doc.origin ?? null,
            error: !!doc.error,
            fault: !!doc.fault,
            throttle: !!doc.throttle,
            http_status: doc.http?.response?.status ?? null,
            duration,
            subsegments,
          };
        });
        return { trace_id, segments };
      } catch (err) {
        return { error: err.message, error_type: err.name ?? "XRAY_ERROR" };
      }
    },
  },
];
