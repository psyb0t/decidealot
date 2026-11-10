# Agent surface

Prefix: AGT. Covers `.agents/skills/`, `.agents/plugins/`, the plugin manifests and `server.json`.

- **AGT1:** `.agents/skills/decidealot/` is the published skill and `.agents/plugins/decidealot/` is the OpenClaw stdio bridge. CI publishes exactly these names to ClawHub (`skill_names` and `plugin_names` in `pipeline.yml`). Renaming a directory updates those lists.
- **AGT2:** The bridge (`bin/cli.js`) only appends `/mcp` to `DECIDEALOT_URL` and forwards to `mcp-remote` with an optional bearer header. Keep `mcp-remote` pinned exactly in `package.json`.
- **AGT3:** The skill and bridge README describe the same tools, selectors and settings as `docs/`. A change to REST, MCP, selectors or configuration updates `SKILL.md`, `references/setup.md` and the bridge README in the same change.
- **AGT4:** `server.json` describes the OCI image and its Streamable HTTP URL. CI rewrites its `version` and image tag from the release tag; leave `version` at `0.0.0` in the repository.
- **AGT5:** Skill text never asks the agent to act on a decision without the caller's threshold.
