# Windows Edge browser tools

The platform can use a local Windows Edge browser for AI-driven execution from test case management. This is separate from the fixed-step UI actuator. Docker remains the default for other deployments.

Install Node.js and Microsoft Edge, then install the pinned official MCP package in a runtime directory of your choice:

```powershell
npm.cmd install --prefix <runtime-directory> --save-exact @playwright/mcp@0.0.80
```

Set these values in `local/.env` using absolute paths:

```dotenv
SGUI_BROWSER_MODE=windows-edge
SGUI_BROWSER_NODE=<absolute-path-to-node.exe>
SGUI_BROWSER_MCP_CLI=<runtime-directory>/node_modules/@playwright/mcp/cli.js
SGUI_SCREENSHOT_HOST_ROOT=<project-directory>/data/playwright-screenshots
```

Create `local/playwright-mcp-windows.local.json`. Replace the output path with an absolute path on your machine. Each MCP session uses an isolated test context, not your personal browser profile. Keep certificate validation enabled.

```json
{
  "browser": {
    "browserName": "chromium",
    "isolated": true,
    "launchOptions": { "channel": "msedge", "headless": false },
    "contextOptions": { "viewport": { "width": 1440, "height": 900 } }
  },
  "outputDir": "<project-directory>/data/playwright-screenshots",
  "server": {
    "host": "127.0.0.1",
    "port": 8932,
    "allowedHosts": ["localhost:8932", "127.0.0.1:8932", "host.docker.internal:8932"]
  }
}
```

Start the local service:

```powershell
powershell -ExecutionPolicy Bypass -File .\local\Manage-BrowserTools.ps1 -Action Start
```

In the platform's remote MCP settings, set **SGUI Browser Tools** to `http://host.docker.internal:8932/mcp` with transport `streamable-http`, then test connectivity. It should report 24 tools for the pinned version. The service listens only on the local computer; no firewall exposure or unrestricted host wildcard is required. The Docker backend reaches it through Docker Desktop's host gateway.

`Manage-Workbench.ps1` also starts, stops, and reports this browser service when the mode is enabled. Edge opens on the first browser tool call and uses an isolated test context. Temporary data stays under `data/browser-mcp-tmp`. Do not close the test browser while a case is running. The script keeps its own process record and will not stop unrelated Edge windows. After restarting this service, create a new LLM conversation or start a new execution; an existing conversation may still reference an expired MCP session.

Screenshots and logs are in `data/playwright-screenshots` and `data/browser-mcp-logs`. A relative screenshot filename is also saved in the shared directory. The platform upload tool resolves relative filenames under `/tmp/playwright-output` and maps Windows absolute paths using `SGUI_SCREENSHOT_HOST_ROOT`. This setting must match the browser's `outputDir`; after changing it, recreate the MCP container with the local Compose file. Uploads outside this shared directory are rejected. The local Compose deployment mounts the upload tool and path resolver from `MCP/`.

In test case management, click **Execute → Start execution**, then open **LLM Chat** for live tool results. In Windows Edge mode, an independent visible Edge window displays the real browser operations. Leave this window untouched during the test. Screenshots are attached to the case by step number. Case execution does not automatically retry failed tool calls; three consecutive failures of the same tool or an 80-call budget stops the run as blocked. A successfully opened page alone is not a passed case.

For Docker-only deployments, omit `SGUI_BROWSER_MODE` or set it to `docker` and keep the browser MCP URL as `http://playwright-mcp:8931/mcp`. A site's access policy can still block either browser environment; successful page access does not establish that every business case passes.
