---
title: "Models"
description: "Every Laya, Von, CLM and hosted Jev selector, how they differ, how resident slots and execution lanes schedule them, and CLM caching."
nav_order: 2
---

# Models

Every request must name a selector. `GET /v1/models` returns the selectors the running deployment accepts, so a disabled provider's names are not in it.

## Selectors

| Selector | What it runs | Pick it when |
| --- | --- | --- |
| `laya`, `laya-auto`, `laya-latest` | Laya with automatic checkpoint routing. | State may arrive in more than one language or script. |
| `laya-english` | Laya's English checkpoint. | State is English and uses the Latin script. |
| `laya-multilingual` | Laya's multilingual checkpoint. | State is in another language or script, including short Latin-script text that is not clearly English. |
| `laya-typed-decisions` | Laya's checkpoint tuned for structured workflow decisions. | Your workload looks like repeated policy, routing, triage, or approval decisions. Validate it on your own cases first. |
| `von`, `von-latest`, `von-1.1`, `von-1.1.0` | The local English-only Von 1.1 model. | You want Von's independent result for a short, well-posed decision, or want to compare it with Laya before standardizing a workflow. |
| `clm`, `clm-latest`, `clm-0.1`, `clm-0.1-8b` | The local CLM v0.1 projection head over one configured Qwen3-8B embeddings endpoint. | You have a trusted embeddings service that emits CLM-compatible 4096-wide last-token Qwen3-8B vectors. |
| Names returned by TypeSafe, such as `jev-latest` | TypeSafe's hosted Jev API. | You have a TypeSafe API key and intend to send the state and questions to TypeSafe. |

The `model` field of a local answer is the public name Decidealot assigns: `laya` for the automatic selectors, the selector itself for a named Laya checkpoint, `von-1.1` for every Von selector and `clm-0.1-8b` for every CLM selector. A hosted answer keeps the model name TypeSafe reports, which can differ from the alias you sent.

## What differs

Laya is one model family with three checkpoints. Its automatic selectors choose the English or multilingual checkpoint from the input script and a language heuristic. Use `laya-multilingual` for known non-English short Latin-script messages. `laya-typed-decisions` targets repeated structured decision work.

Von is a separate English-only decision model for short questions with clear criteria.

CLM is a local 75 MB projection head, not a text encoder. It calls one configured OpenAI-compatible `/v1/embeddings` URL and requires its configured model to return Qwen3-8B last-token vectors with exactly 4096 float values. See [CLM](#clm) below.

Jev runs at TypeSafe. Decidealot sends the complete `state` and `questions` to TypeSafe's fixed HTTPS API. See [Hosted Jev](#hosted-jev) below.

All providers take the same TypeSafe `state` and `questions` shape and return typed `choice`, `score`, and `noul` answers with probabilities. Your application applies the threshold and action that follow.

## Which providers are enabled

| Provider | Enabled when | Startup download |
| --- | --- | --- |
| Laya | `DECIDEALOT_LAYA_ENABLED=true` (default) | The pinned `convaiinnovations/laya` revision into `/models/laya`. |
| Von | `DECIDEALOT_VON_ENABLED=true` (default) | The pinned `wfzyx/von` revision into `/models/von`. |
| CLM | `DECIDEALOT_CLM_ENABLED=auto` (default) and `DECIDEALOT_CLM_EMBEDDINGS_URL` is set, or `DECIDEALOT_CLM_ENABLED=true` | Only `CLM_v0.1-8B.pt` from the pinned `Contrastive-LM/CLM-v0.1-8B` revision into `/models/clm`, checked against its SHA-256. |
| Jev | `DECIDEALOT_JEV_ENABLED=auto` (default) and `DECIDEALOT_TYPESAFE_API_KEY` is set, or `DECIDEALOT_JEV_ENABLED=true` | None. |

At least one provider must be enabled or the service refuses to start. Every enabled local bundle is downloaded and checked before the server starts listening. Later starts reuse the files already in `/models` and download only what is missing. After that check each provider runs with Hugging Face offline mode turned on.

## Scheduling and memory

Decidealot keeps one local provider resident by default. Set `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS=2` or `3` only when the host has enough RAM or VRAM to hold that many processes. At the limit, a request for another local provider waits for an idle slot, stops the least recently used idle provider and starts the one it needs.

Resident slots decide what stays loaded. A separate set of rules decides what runs at the same time:

- Requests to one local provider run one at a time, in arrival order. All Laya selectors count as one provider. Requests to one hosted model name are serialized the same way.
- On the CUDA image, local inference runs one request at a time across all local models, even with several resident slots.
- CLM and its Qwen embeddings calls get the local lane to themselves by default, including on CPU. Set `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS=true` only when that workload can safely overlap other local models, such as when Qwen runs on another machine.
- Hosted Jev overlaps local work. A Jev call uses HTTPS and does not load, switch, or unload a local provider.

A batch can name more local model families than the resident limit. Those calls wait for a slot and switch models as needed.

`DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` (default `600`) stops a local provider that has had no request for that long, releasing its model weights, Torch allocations, worker threads and CUDA context. `0` turns off only the idle timer. `POST /v1/models/unload` stops every loaded local provider at once; see [Unload loaded runtimes](api.md#unload-loaded-runtimes).

## CLM

CLM evaluates requests through one OpenAI-compatible embeddings endpoint that the operator configures with `DECIDEALOT_CLM_EMBEDDINGS_URL`. Requests cannot choose another URL. The endpoint receives rendered decision state and criterion text, so configure a service you control or trust.

The configured model (`DECIDEALOT_CLM_EMBEDDINGS_MODEL`, default `qwen3-8b`) must emit Qwen3-8B last-token embeddings with exactly 4096 float values per input. Decidealot rejects any other vector width before the projection head runs. A quantized Qwen encoder is not equivalent to upstream's full-precision encoder, and matching vector width alone does not establish equivalent accuracy. The upstream reference uses Qwen3-8B last-token pooling with a 2048-token context. Quantization, tokenizer, pooling, and context changes can alter decisions even when the vector width is correct.

CLM normalizes encoder vectors before applying its projection heads. It caches candidate vectors, never raw decision state, in a process-local LRU keyed by hashed text:

- `DECIDEALOT_CLM_CANDIDATE_CACHE_ENTRIES` sets the capacity, `0` to `4096`, default `1024`. `0` disables the cache. At `4096` entries the vectors take roughly 64 MiB plus indexing overhead.
- `DECIDEALOT_CLM_CANDIDATE_CACHE_TTL_SECONDS` sets the lifetime, above `0` and at most `86400`, default `600`.
- Expiry, LRU eviction and unloading CLM discard cached vectors. Restart or unload CLM when you change encoder weights behind the same URL, so cached candidates cannot mix encoder revisions.

Every request still embeds its state. `usage.input_tokens` reports only the tokens the embeddings calls actually made, taken from the endpoint's `usage.prompt_tokens`, so cache hits lower it.

`DECIDEALOT_CLM_MAX_TEXT_BYTES` (default `8192`) limits each rendered state plus instructions, and each candidate, before CLM contacts the encoder. Over-limit inputs return `422`. The limit is in UTF-8 bytes, not tokens, and cannot detect truncation done by the external server. Configure your encoder to reject input beyond its context instead of silently truncating it, and test that behavior.

CLM accepts an optional `temperature` in request `config`; see [Request configuration](api.md#request-configuration).

## Hosted Jev

Set `DECIDEALOT_TYPESAFE_API_KEY` in a private environment file or secret store. Jev enables automatically when the key is present, unless `DECIDEALOT_JEV_ENABLED=false`. This key is only for outbound TypeSafe calls; `DECIDEALOT_API_KEY` separately protects callers of Decidealot.

Decidealot reads TypeSafe's authenticated catalog from `https://api.typesafe.ai/v1/models`, caches it for 60 seconds, and refreshes it on the next model-listing or hosted-model request after expiry. New hosted model names need no Decidealot release. Hosted names pass through exactly; Decidealot adds no `jev` alias. Local selectors win if TypeSafe ever returns a colliding name.

Every Jev decision sends its complete state, instructions and criteria to `https://api.typesafe.ai/v1/systemone`. TypeSafe charges for its API calls. If the catalog is unavailable or malformed, model listing and hosted requests return `503`; local decisions keep working.

To run Jev alone, set `DECIDEALOT_LAYA_ENABLED=false` and `DECIDEALOT_VON_ENABLED=false`, leave CLM unconfigured, and supply the key. That configuration downloads nothing and needs no `/models` mount. [Deployment](deployment.md#jev-only-deployment) has the command.
