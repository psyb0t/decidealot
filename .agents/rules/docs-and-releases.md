# Docs and releases

Prefix: DOC.

- **DOC1:** `docs/` is the user documentation and `README.md` is the front door. Reference material lives in `docs/`; the README keeps a summary and links to the site with full `https://psyb0t.github.io/decidealot/...` URLs, because the README is mirrored to Docker Hub.
- **DOC2:** Every page has front matter with a unique `title` and a `description` of 50 to 170 characters. Keep `docs/` flat. Link pages by relative `.md` path; link files outside `docs/` by full `https://github.com/psyb0t/decidealot/blob/main/...` URL. Wrap code blocks that contain `{{` or `{%` in `raw` tags.
- **DOC3:** After a docs change, run `make docs` and the site link check, and fix what they report.
- **DOC4:** Prose: no em or en dashes, "directory" never "folder", plain words instead of filler vocabulary, straight quotes. Match the existing voice.
- **DOC5:** Public text never names local scratch directories, agent tooling or session details.
- **DOC6:** A release bumps `version` in `pyproject.toml` and `.agents/.codex-plugin/plugin.json`, adds a `CHANGELOG.md` entry in the existing style, and tags `vX.Y.Z` on the `main` commit. CI promotes the images built for that commit and stamps the version into `server.json` and the plugin `package.json` when it publishes. Do not bump versions or write CHANGELOG entries unless asked.
- **DOC7:** Commits and tags never carry `Closes #N`, `Fixes #N` or `Resolves #N` unless the owner asked to close that issue.
