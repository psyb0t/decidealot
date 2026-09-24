#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const MCP_PATH = "/mcp";
const SERVICE_URL_ENV = "DECIDEALOT_URL";
const API_KEY_ENV = "DECIDEALOT_API_KEY";
const TRANSPORT = "http-only";
const HEADER_OPTION = "--header";
const TRANSPORT_OPTION = "--transport";
const AUTHORIZATION_HEADER = "Authorization: Bearer ";

const baseUrl = process.env[SERVICE_URL_ENV];

if (!baseUrl) {
  console.error(
    `[decidealot-mcp] Missing ${SERVICE_URL_ENV}.

Point this bridge at your running Decidealot instance, for example:
  export ${SERVICE_URL_ENV}=http://127.0.0.1:8080

See https://github.com/psyb0t/decidealot`,
  );
  process.exit(1);
}

const endpoint = `${baseUrl.replace(/\/+$/, "")}${MCP_PATH}`;
const apiKey = process.env[API_KEY_ENV];
const proxyEntry = require.resolve("mcp-remote/dist/proxy.js");
const args = [proxyEntry, endpoint, TRANSPORT_OPTION, TRANSPORT];

if (apiKey) {
  args.push(HEADER_OPTION, `${AUTHORIZATION_HEADER}${apiKey}`);
}

args.push(...process.argv.slice(2));

const result = spawnSync(process.execPath, args, { stdio: "inherit" });
process.exit(result.status ?? 1);
