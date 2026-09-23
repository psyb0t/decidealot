# Deployment

Decidealot is a Docker image. Run a released image tag, keep model data outside the container layer, and publish the HTTP port only where you intend to serve it.

## CPU deployment

This is the normal setup. It binds the API to the local machine, stores model bundles in a named volume, drops every Linux capability, blocks privilege escalation, uses a read-only root filesystem, bounds temporary storage, and limits logs and resources.

```bash
docker volume create decidealot-model-data

docker run --detach --name decidealot --init --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=volume,source=decidealot-model-data,target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.1.0
```

`GET /health` means the controller is ready to start a provider. It deliberately does not download or load model weights. The first `POST /v1/systemone` for each backend fetches its pinned model bundle and can take several minutes. Later calls reuse the saved files.

## Configuration file

Download the release's [`.env.example`](../.env.example) to a private file, set only the values you need, and give it to Docker with `--env-file`. Do not commit that file or put a real key into a command history.

```bash
mkdir -p "$HOME/.config/decidealot"
curl --fail --location --output "$HOME/.config/decidealot/decidealot.env" \
  https://raw.githubusercontent.com/psyb0t/decidealot/v0.1.0/.env.example
chmod 600 "$HOME/.config/decidealot/decidealot.env"

docker run --detach --name decidealot --init --restart unless-stopped \
  --env-file "$HOME/.config/decidealot/decidealot.env" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=volume,source=decidealot-model-data,target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.1.0
```

The relevant variables are:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECIDEALOT_API_KEY` | empty | Optional Bearer token for every API endpoint. |
| `DECIDEALOT_DEFAULT_MODEL` | `laya` | Backend selected by `jev-*` compatibility aliases. |
| `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` | `600` | Seconds without a request before automatic unload. `0` disables timeout-based unload. |
| `DECIDEALOT_MODEL_DATA_DIR` | `/models` | Parent directory for the provider bundles. |
| `DECIDEALOT_LAYA_MODEL_DIR` | empty | Optional absolute Laya directory inside the container. |
| `DECIDEALOT_VON_MODEL_DIR` | empty | Optional absolute Von directory inside the container. |
| `DECIDEALOT_MAX_REQUEST_BYTES` | `1048576` | Maximum JSON request size. |

## CUDA deployment

CUDA uses the separately tagged amd64 image. Install NVIDIA Container Toolkit on the host first, then use `--gpus all` and the same hardening options as the CPU command.

```bash
docker run --detach --name decidealot --init --restart unless-stopped \
  --gpus all \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 512 --memory 8g --cpus 4 \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /var/run:rw,noexec,nosuid,size=8m \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=5 \
  --mount type=volume,source=decidealot-model-data,target=/models \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.1.0-cuda
```

The image owns its device selection. Do not set `DECIDEALOT_DEVICE` or `DECIDEALOT_IMAGE_VARIANT`. A CPU or CUDA mismatch fails before provider startup instead of pretending to work.

## Host-visible model directories

The named volume is less fragile. Use bind mounts only when you need direct access to the downloaded bundles, such as a managed storage path or an offline transfer.

```bash
install_root=/srv/decidealot-models
sudo install --directory --owner=10001 --group=10001 "$install_root/laya" "$install_root/von"

docker run --detach --name decidealot --init --restart unless-stopped \
  --env DECIDEALOT_LAYA_MODEL_DIR=/local-models/laya \
  --env DECIDEALOT_VON_MODEL_DIR=/local-models/von \
  --mount type=bind,source="$install_root/laya",target=/local-models/laya \
  --mount type=bind,source="$install_root/von",target=/local-models/von \
  --publish 127.0.0.1:8080:8080 \
  psyb0t/decidealot:v0.1.0
```

The first selected request writes missing official model files to the mounted directory. A complete bundle is reused. After preparation, the provider turns on Hugging Face and Transformers offline mode, so a missing file fails rather than being silently fetched at request time. Never mount `/`, `/home`, `/root`, Docker socket, or a broad parent directory.

## Authentication and exposure

Loopback with no token is fine for one host. Before a reverse proxy, tunnel, tailnet, or public interface is involved, set `DECIDEALOT_API_KEY` in the private environment file and send it as a Bearer token:

```bash
export DECIDEALOT_API_KEY=REPLACE_ME

curl --fail http://127.0.0.1:8080/v1/models \
  --oauth2-bearer "$DECIDEALOT_API_KEY"
```

Use TLS at the edge. Do not expose an unauthenticated model API to the internet.

## Lifecycle and upgrades

The service keeps at most one provider resident. Another provider request stops the idle one first. `POST /v1/models/{model}/unload` stops one provider, and `POST /v1/models/unload` stops both. Both are idempotent and return `409` while their target has an active decision request.

Use a versioned image tag for upgrades. Pull it, recreate the container with the same model volume and configuration file, then check `/health`. Do not replace the model volume unless you want to download model files again.

```bash
docker pull psyb0t/decidealot:v0.1.0
docker stop decidealot
docker rm decidealot
```

Then run the CPU or CUDA command again with the desired release tag. `docker stop` and `docker rm` above target only the explicitly named Decidealot container.
