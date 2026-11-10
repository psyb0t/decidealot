# Security

Prefix: SEC.

- **SEC1:** `DECIDEALOT_API_KEY` empty means no authentication. When set, every route except `/health` and the whole MCP app require `Authorization: Bearer <key>`, compared with `hmac.compare_digest` in `require_bearer_token`. Do not add a second token source or exempt another route.
- **SEC2:** MCP keeps DNS rebinding protection on (`TransportSecuritySettings(enable_dns_rebinding_protection=True)` in `app.py`). Defaults stay loopback plus the `decidealot` service name. Never default to a wildcard.
- **SEC3:** `DECIDEALOT_TYPESAFE_API_KEY` and `DECIDEALOT_CLM_EMBEDDINGS_API_KEY` are outbound credentials. They stay `SecretStr`, go only to their fixed endpoint, and never appear in a response, a log line or an MCP tool argument.
- **SEC4:** Logs go through `RedactingFormatter`, which replaces values under keys matching password, token, secret, API key, authorization or cookie. Never log a request body, decision state, criteria or a key value; log provider names, status codes and error types.
- **SEC5:** Images run as non-root `1000:1000`, and the documented `docker run` and Compose recipes keep `--read-only`, `--cap-drop ALL`, `no-new-privileges`, bounded tmpfs mounts and a loopback publish. Do not weaken them in docs or examples.
- **SEC6:** CLM sends rendered state and criteria to its configured embeddings URL and Jev sends the full request to TypeSafe. Docs that mention either say that data leaves the host.
- **SEC7:** No real secret, token or host name in code, tests, docs or examples. Use placeholders such as `your-token-here`, `replace-with-a-real-secret` and `mcp.example.net`.
