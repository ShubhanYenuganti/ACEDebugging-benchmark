const HARNESS_API_KEY = process.env.HARNESS_API_KEY ?? "";

function checkKey(provided) {
  if (!provided || provided !== HARNESS_API_KEY) {
    return { error: "unauthorized", message: "score tools require harness_api_key" };
  }
  return null;
}

export const scoreTools = [
  {
    name: "ace_verify_fix",
    description: "Trigger verify loop for a run (harness use only)",
    inputSchema: {
      type: "object",
      properties: {
        run_id: { type: "string" },
        harness_api_key: { type: "string" },
      },
      required: ["run_id", "harness_api_key"],
    },
    async handler({ run_id, harness_api_key }) {
      const authErr = checkKey(harness_api_key);
      if (authErr) return authErr;
      return { status: "not_implemented" };
    },
  },
  {
    name: "ace_score_run",
    description: "Score a completed run (harness use only)",
    inputSchema: {
      type: "object",
      properties: {
        run_id: { type: "string" },
        harness_api_key: { type: "string" },
      },
      required: ["run_id", "harness_api_key"],
    },
    async handler({ run_id, harness_api_key }) {
      const authErr = checkKey(harness_api_key);
      if (authErr) return authErr;
      return { status: "not_implemented" };
    },
  },
];
