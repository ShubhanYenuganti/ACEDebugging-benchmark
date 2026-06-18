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
      "X-Ray GetTraceSummaries: list recent trace summaries over a time window. Returns per-trace metadata: id, duration, response_time, has_error, has_fault, has_throttle, http_status, entry_service. Use to find which requests had errors or high latency; then call ace_get_trace for the full segment tree. Defaults: last 30 min, up to 100 traces.",
    inputSchema: {
      type: "object",
      properties: {
        window_minutes: { type: "number" },
        filter_expression: { type: "string" },
        only_errors: { type: "boolean" },
      },
      required: [],
    },
    async handler({ window_minutes = 30, filter_expression, only_errors = false } = {}) {
      const clampedWindow = Math.min(Math.max(1, window_minutes ?? 30), 1440);
      const endTime = new Date();
      const startTime = new Date(endTime.getTime() - clampedWindow * 60 * 1000);
      const effectiveFilter = filter_expression ?? (only_errors ? "fault = true OR error = true" : undefined);
      try {
        const res = await xrayClient.send(new GetTraceSummariesCommand({
          StartTime: startTime,
          EndTime: endTime,
          Sampling: false,
          FilterExpression: effectiveFilter,
        }));
        const traces = (res.TraceSummaries ?? []).map((s) => ({
          id: s.Id,
          duration: s.Duration,
          response_time: s.ResponseTime,
          has_error: s.HasError ?? false,
          has_fault: s.HasFault ?? false,
          has_throttle: s.HasThrottle ?? false,
          http_status: s.Http?.HttpStatus ?? null,
          entry_service: s.EntryPoint?.Name ?? null,
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
        if (!trace) return { error: "trace not found", error_type: "NOT_FOUND" };
        const segments = (trace.Segments ?? []).map((seg) => {
          let doc = {};
          try { doc = JSON.parse(seg.Document ?? "{}"); } catch { doc = {}; }
          const subsegments = (doc.subsegments ?? []).map((sub) => ({
            name: sub.name,
            namespace: sub.namespace ?? null,
            error: sub.error ?? false,
            fault: sub.fault ?? false,
            http_status: sub.http?.response?.status ?? null,
            aws_operation: sub.aws?.operation ?? null,
          }));
          return {
            id: doc.id,
            name: doc.name,
            start_time: doc.start_time,
            end_time: doc.end_time,
            error: doc.error ?? false,
            fault: doc.fault ?? false,
            throttle: doc.throttle ?? false,
            http_status: doc.http?.response?.status ?? null,
            duration: doc.end_time && doc.start_time ? doc.end_time - doc.start_time : null,
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
