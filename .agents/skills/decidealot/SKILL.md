---
name: "decidealot"
description: "Run local Laya or Von typed classification, scoring, and true-or-false decisions through Decidealot REST or MCP. Use when a user needs a bounded decision with probabilities, wants to deploy the Docker image, inspect local model aliases, or release model memory."
homepage: "https://github.com/psyb0t/decidealot"
metadata:
  openclaw:
    primaryEnv: "DECIDEALOT_URL"
    requires:
      bins:
        - curl
permissions:
  network: "outbound HTTP only to the user-provided DECIDEALOT_URL. Requests can contain user data, so use a trusted self-hosted endpoint."
  shell: "curl requests to the user-provided DECIDEALOT_URL. Docker is needed only when the user explicitly asks to deploy Decidealot."
  filesystem: "no ordinary filesystem access. Deployment creates only the user-selected narrow model directory."
user-invocable: true
---

# Decidealot

Decidealot runs Laya and Von locally. It turns supplied state into typed `choice`, `score`, or `noul` results with probabilities. The caller applies the threshold and any action that follows.

Use a running Decidealot endpoint. Clone or build this repository only for Decidealot development work.

Read [references/setup.md](references/setup.md) before deploying a container or configuring REST, MCP, or the OpenClaw bridge.

## Security & safety

- Treat a decision as evidence, not authority. A local model cannot grant permission to delete, send, deploy, trade, or alter external state.
- Keep authorization, ownership, irreversible-action checks, and final thresholds in the caller. For costly mistakes, route uncertainty to human review.
- Send user data only to the endpoint the user named. Never search workspace files for `DECIDEALOT_API_KEY` or create an unauthenticated public endpoint.
- Every decision request needs an explicit local `model` selector from `GET /v1/models`.
- `unload_models` evicts the loaded model and Torch runtime. Use it only when the user asks to free memory.

## When to use

1. Get `DECIDEALOT_URL` from the user or their environment. Default local deployment is `http://127.0.0.1:8080`.
2. Call `GET /health`, then `GET /v1/models` before choosing a selector you have not already been given.
3. Put the raw subject in `state`. Define bounded questions in `questions`.
4. Read the typed answer and its probabilities. Report them plainly. The caller, not the model, selects the next action.
5. Use REST for application requests and machine-readable errors. Use MCP at `/mcp` when an MCP client already supports Streamable HTTP. Use the stdio bridge only for a client that cannot connect to remote MCP.

`choice` selects one named criterion. `score` returns an expected position in an ordered criterion list. `noul` returns the probability that a true-or-false statement is true.

## When not to use

Use Decidealot for bounded classification, scoring, and true-or-false decisions. Use a language model for prose, code generation, or open-ended advice. Use the OpenClaw bridge when an MCP client needs local stdio.

## REST and MCP

REST uses `POST /v1/systemone`, `GET /v1/models`, and `POST /v1/models/unload`. MCP is at `/mcp` and exposes `system_one`, `list_models`, and `unload_models` with the same input and result shapes. Read [references/setup.md](references/setup.md) for exact deployment, authentication, REST, MCP, and bridge commands. Read the public [API guide](https://github.com/psyb0t/decidealot/blob/main/docs/api.md) before constructing an unfamiliar question type.

## Completion

Complete a decision task only after the request reached the intended endpoint, the response names the requested model, and the answer plus probabilities have been reported. For deployment, also prove `GET /health` returns `200`.
