# Docs and releases

Prefix: DOC.

- **DOC1:** `docs/` is the user documentation and `README.md` is the front door. Reference material lives in `docs/`; the README keeps a summary and links to the site with full `https://psyb0t.github.io/decidealot/...` URLs, because the README is mirrored to Docker Hub.
- **DOC2:** Keep `docs/` flat. Wrap code blocks that contain `{{` or `{%` in `raw` tags.
- **DOC3:** After a docs change, run `make docs` and the site link check, and fix what they report.
- **DOC6:** A release bumps `version` in `pyproject.toml` and `.agents/.codex-plugin/plugin.json` and tags `vX.Y.Z` on the `main` commit. CI promotes the images built for that commit and stamps the version into `server.json` and the plugin `package.json` when it publishes.
