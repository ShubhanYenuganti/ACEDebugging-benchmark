import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { probeTools } from "./tools/probe.js";
import { probeExtendedTools } from "./tools/probe_extended.js";
import { observeTools } from "./tools/observe.js";
import { observeExtendedTools } from "./tools/observe_extended.js";
import { scoreTools } from "./tools/score.js";

const server = new McpServer({
  name: "ace-bench-diagnostic-mcp",
  version: "1.0.0",
});

function jsonSchemaPropToZod(prop) {
  if (!prop || typeof prop !== "object") return z.any();
  const t = prop.type;
  if (Array.isArray(t)) {
    return z.any();
  }
  switch (t) {
    case "string": {
      if (Array.isArray(prop.enum) && prop.enum.length > 0) {
        return z.enum(prop.enum);
      }
      return z.string();
    }
    case "number":
    case "integer":
      return z.number();
    case "boolean":
      return z.boolean();
    case "array":
      return z.array(prop.items ? jsonSchemaPropToZod(prop.items) : z.any());
    case "object":
      return z.record(z.any());
    default:
      return z.any();
  }
}

function buildZodShape(inputSchema) {
  const properties = inputSchema?.properties ?? {};
  const required = new Set(inputSchema?.required ?? []);
  const shape = {};
  for (const [key, prop] of Object.entries(properties)) {
    const base = jsonSchemaPropToZod(prop);
    shape[key] = required.has(key) ? base : base.optional();
  }
  return shape;
}

for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...scoreTools]) {
  const shape = buildZodShape(tool.inputSchema);
  server.tool(
    tool.name,
    tool.description,
    shape,
    async (args) => {
      process.stderr.write(
        JSON.stringify({ tool: tool.name, timestamp: new Date().toISOString() }) + "\n"
      );
      const result = await tool.handler(args ?? {});
      return { content: [{ type: "text", text: JSON.stringify(result) }] };
    }
  );
}

const transport = new StdioServerTransport();
await server.connect(transport);
