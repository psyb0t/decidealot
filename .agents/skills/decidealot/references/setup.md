# Run and connect to Decidealot

Decidealot ships as the `psyb0t/decidealot` Docker image. It downloads its pinned Laya and Von bundles during startup into the host directory mounted at `/models`. Build from source only for development work.

## Start the CPU image

Choose one narrow host directory for model files. The command passes the current host UID and GID into the container, so a directory created by the current user is writable without image-specific ownership.

```bash
model_directory="$HOME/.local/share/decidealot/models"
runtime_uid=$(id -u)
runtime_gid=$(id -g)
mkdir --parents "$model_directory"

docker run --detach --name decidealot --init --restart unless-stopped \
  --user "$runtime_uid:$runtime_gid" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --mount type=bind,source="$model_directory",target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:latest
```

The first start downloads model files and can take several minutes. Wait for this to return successfully before using the API:

```bash
curl --fail --show-error http://127.0.0.1:8080/health
```

Use an immutable `vX.Y.Z` image tag for a lasting deployment. The full hardening and CUDA recipes are in [Deployment](https://github.com/psyb0t/decidealot/blob/main/docs/deployment.md).

## Authentication

Set `DECIDEALOT_API_KEY` when a request crosses any network boundary. It protects REST and MCP except `/health`.

```bash
export DECIDEALOT_URL=http://127.0.0.1:8080
export DECIDEALOT_API_KEY=your-token-here
curl --fail --show-error "$DECIDEALOT_URL/v1/models" --header "Authorization: Bearer $DECIDEALOT_API_KEY"
```

Do not place a real token in a tracked file, prompt, or command history. Omit the header only when the container has no API key and listens only on loopback.

## REST

List selectable aliases, then send raw state plus a bounded question shape:

```bash
curl --fail --show-error "$DECIDEALOT_URL/v1/models" --header "Authorization: Bearer $DECIDEALOT_API_KEY"

curl --fail --show-error "$DECIDEALOT_URL/v1/systemone" \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer $DECIDEALOT_API_KEY" \
  --data '{
    "model": "laya-typed-decisions",
    "state": "An operation would permanently remove protected data.",
    "questions": {
      "handling": {
        "type": "choice",
        "criteria": {
          "allow": "The operation is reversible and authorized.",
          "review": "A human must review the operation first."
        }
      }
    }
  }'
```

Use `GET /v1/models` to learn the available Laya and Von aliases. Use `POST /v1/models/unload` to release the loaded runtime only when the user asks to free memory. The full request and response contract is in [API](https://github.com/psyb0t/decidealot/blob/main/docs/api.md).

## MCP

Connect an MCP client to `http://127.0.0.1:8080/mcp`.

```json
{
  "mcpServers": {
    "decidealot": {
      "url": "http://127.0.0.1:8080/mcp",
      "headers": {
        "Authorization": "Bearer your-token-here"
      }
    }
  }
}
```

The available tools are `system_one`, `list_models`, and `unload_models`. `system_one` takes the same `model`, `state`, and `questions` object as REST and returns the same structured result. `list_models` and `unload_models` take `{}`. The MCP client owns protocol initialization and the session header.

MCP accepts loopback `Host` headers. Connect a client on the same host, or use a tunnel that preserves a loopback `Host` header. The full tool contract and error behavior are in [MCP Streamable HTTP](https://github.com/psyb0t/decidealot/blob/main/docs/api.md#mcp-streamable-http).

## OpenClaw bridge

Install the bridge only when the MCP client requires local stdio. It connects to a Decidealot container you already run.

```bash
openclaw plugins install clawhub:@psyb0t/decidealot
export DECIDEALOT_URL=http://127.0.0.1:8080
export DECIDEALOT_API_KEY=your-token-here
```

The bridge appends `/mcp` and forwards stdio traffic to the running container.
