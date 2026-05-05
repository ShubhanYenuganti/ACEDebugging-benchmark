import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { probeTools } from "./tools/probe.js";
import { probeExtendedTools } from "./tools/probe_extended.js";
import { observeTools } from "./tools/observe.js";
import { observeExtendedTools } from "./tools/observe_extended.js";
import { scoreTools } from "./tools/score.js";

const server = new McpServer({
  name: "ace-bench-diagnostic-mcp",
  version: "1.0.0",
});

for (const tool of [...probeTools, ...probeExtendedTools, ...observeTools, ...observeExtendedTools, ...scoreTools]) {
  server.tool(
    tool.name,
    tool.description,
    {},
    async (args) => {
      process.stderr.write(
        JSON.stringify({ tool: tool.name, timestamp: new Date().toISOString() }) + "\n"
      );
      const result = await tool.handler(args);
      return { content: [{ type: "text", text: JSON.stringify(result) }] };
    }
  );
}

const transport = new StdioServerTransport();
await server.connect(transport);
