import { CloudTrailClient, LookupEventsCommand } from "@aws-sdk/client-cloudtrail";

const awsConfig = {
  endpoint: process.env.LOCALSTACK_ENDPOINT ?? "http://localhost:4566",
  region: "us-east-1",
  credentials: { accessKeyId: "test", secretAccessKey: "test" },
};

const cloudTrailClient = new CloudTrailClient(awsConfig);

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
];
