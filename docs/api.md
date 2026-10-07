# API

Decidealot runs configured local Laya, Von, and CLM providers or TypeSafe's hosted Jev through one TypeSafe-compatible HTTP contract. Send the state to judge and define the allowed answer shape. Decidealot returns typed results with probabilities.

## Base URL and startup

The examples use a local CPU container at `http://127.0.0.1:8080`. On a fresh model directory, Decidealot downloads and verifies every enabled local bundle before `/health` returns `200`. The default configuration enables Laya and Von. CLM is enabled when its required embeddings URL is configured. That first startup can take minutes. It prepares files only. Neither model nor Torch stays loaded until a `POST /v1/systemone` request selects a local model. Jev enables when a TypeSafe key is configured and needs no local bundle.

Every response includes `X-Request-Id`. Send a UUID or ULID in that header when you need to correlate logs and calls. Decidealot creates a UUID when it is missing or invalid.

```bash
base_url=http://127.0.0.1:8080
curl --fail --show-error "$base_url/health"
```

```json
{
  "status": "ok",
  "providers": ["laya", "von"]
}
```

`providers` lists only configured providers. A CLM-only deployment returns `{"status":"ok","providers":["clm"]}`. A Jev-only deployment returns `{"status":"ok","providers":["jev"]}`.

## Authentication

Authentication is off unless the container has `DECIDEALOT_API_KEY`. When it is set, every public HTTP API or MCP request except `/health` needs this header:

```bash
auth_header="Authorization: Bearer $DECIDEALOT_API_KEY"
curl --fail --show-error "$base_url/v1/models" --header "$auth_header"
```

Missing or wrong credentials return `401`:

```json
{
  "code": "UNAUTHORIZED",
  "message": "a valid bearer token is required",
  "details": {}
}
```

## MCP Streamable HTTP

The container also exposes MCP Streamable HTTP at `http://127.0.0.1:8080/mcp`. It shares the decision service, public model aliases, request validation, body limit, Bearer authentication, and request ID rules with the TypeSafe-compatible HTTP endpoints. Local models use the provider supervisor; hosted Jev does not.

MCP clients differ in configuration syntax, but they need one Streamable HTTP server URL and the same optional Bearer header:

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

When authentication is disabled, omit `headers`. When it is enabled, an absent or wrong token gets the normal HTTP `401` envelope before an MCP session starts. MCP DNS rebinding protection remains enabled. Loopback and the `decidealot` Docker service host work by default. Configure `DECIDEALOT_MCP_ALLOWED_HOSTS` with the exact public Host header and `DECIDEALOT_MCP_ALLOWED_ORIGINS` with the exact browser Origin when a reverse proxy exposes this endpoint. After bearer authentication, an unconfigured Host returns `421`; an unconfigured Origin returns `403`.

| Tool | Input | Structured output |
| --- | --- | --- |
| `system_one` | Required `model`, `state`, and `questions`, exactly as described in [Make decisions](#make-decisions). | The same `model`, `answers`, and `usage` object returned by `POST /v1/systemone`. |
| `system_one_batch` | Required nonempty `requests` list of complete `model`, `state`, and `questions` objects. | The same ordered `{ "results": [...] }` object returned by `POST /v1/systemone/batch`. |
| `list_models` | None. | The same `{ "models": [...] }` catalog returned by `GET /v1/models`. |
| `unload_models` | None. | The same unload status and provider list returned by `POST /v1/models/unload`. |

For a `system_one` call, use this MCP tool argument shape. The `questions` value has the same `choice`, `score`, and `noul` rules as the HTTP endpoint.

```json
{
  "model": "laya-typed-decisions",
  "state": {
    "operation": "delete",
    "reversible": false
  },
  "questions": {
    "handling": {
      "type": "choice",
      "criteria": {
        "allow": "The operation is reversible and authorized.",
        "review": "A human must review the operation first."
      }
    }
  }
}
```

Call `list_models` and `unload_models` with an empty argument object, `{}`. MCP clients perform initialization and retain the `Mcp-Session-Id`.

MCP tool validation, unknown model, provider busy, and provider unavailable failures return `isError: true`. A `system_one` validation error includes the TypeSafe `{"detail":[...]}` body in its text content. No tool accepts a provider URL, executable, filesystem path, model directory, or arbitrary runtime option.

## List selectable models

`GET /v1/models` lists every accepted `model` value. Each `POST /v1/systemone` call names one selector from this catalog.

```bash
curl --fail --show-error "$base_url/v1/models" --header "$auth_header"
```

```json
{
  "models": [
    {
      "name": "laya",
      "description": "Laya System One decisions with automatic checkpoint routing.",
      "release_date": "2026-09-23"
    },
    {
      "name": "laya-auto",
      "description": "Alias for laya with automatic checkpoint routing.",
      "release_date": "2026-09-23"
    },
    {
      "name": "laya-latest",
      "description": "Alias for the current Laya release.",
      "release_date": "2026-09-23"
    },
    {
      "name": "laya-english",
      "description": "Laya English checkpoint for English Latin-script content.",
      "release_date": "2026-09-23"
    },
    {
      "name": "laya-multilingual",
      "description": "Laya multilingual checkpoint for other languages and scripts.",
      "release_date": "2026-09-23"
    },
    {
      "name": "laya-typed-decisions",
      "description": "Laya checkpoint tuned for structured workflow decisions.",
      "release_date": "2026-09-23"
    },
    {
      "name": "von",
      "description": "Von System One decisions served by the local Von 1.1 model.",
      "release_date": "2026-09-22"
    },
    {
      "name": "von-latest",
      "description": "Alias for the current Von release.",
      "release_date": "2026-09-22"
    },
    {
      "name": "von-1.1",
      "description": "Von 1.1 System One decision model.",
      "release_date": "2026-09-22"
    },
    {
      "name": "von-1.1.0",
      "description": "Alias for the Von 1.1 point release.",
      "release_date": "2026-09-22"
    }
  ]
}
```

The default catalog above contains Laya and Von. When CLM is enabled, the response also contains `clm`, `clm-latest`, `clm-0.1`, and `clm-0.1-8b`. Those selectors run the local `Contrastive-LM/CLM-v0.1-8B` projection head. CLM sends rendered decision state and criterion text to the configured OpenAI-compatible embeddings endpoint, which must return Qwen3-8B last-token vectors with exactly 4096 float values for each input. When Jev is enabled, Decidealot adds the models and aliases returned by TypeSafe's authenticated `GET /v1/models`. It caches that catalog for 60 seconds and refreshes it on the next model-listing or hosted-model request after expiry. The listed hosted names are passed through exactly; Decidealot does not invent a `jev` alias. Local selectors win if TypeSafe ever returns a colliding name. If TypeSafe's catalog is unavailable or malformed, model listing and hosted requests return a safe `503`; local decisions still work.

## Make decisions

`POST /v1/systemone` always needs these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `model` | string | One exact selector from `GET /v1/models`. |
| `state` | string, object, or array | The raw thing to classify, score, or judge. |
| `questions` | object | One or more named `choice`, `score`, or `noul` questions. |
| `config` | optional object | Provider-specific request settings. Omitted means native defaults. |

Question names are yours. The response repeats them under `answers`. `instructions` is optional in the wire schema, but supply it to state the question and decision rules explicitly. This matters especially for `noul`: a question name alone is not the proposition to evaluate. Instructions, state, and criterion values can be strings, JSON objects, or JSON arrays. Laya and Von receive nested instructions and criterion values as JSON text without changing labels, score order, or state. CLM preserves their structure and renders it using upstream's context-then-question layout. Jev receives the validated JSON structure with the exact hosted model name you selected.

### Request configuration

`config` is flat because `model` already selects its schema. CLM selectors accept `{"temperature":0.8}`. Temperature defaults to `1` and must be a finite number greater than `0` and at most `100`. It divides the scaled CLM logits before softmax. Lower temperatures sharpen probabilities, not accuracy. Strings, booleans, null, non-object configs, unknown keys, and values outside this range return `422`. Laya, Von, and hosted Jev accept only omitted or empty config; Decidealot strips the empty object before sending TypeSafe its official three-field request.

```json
{
  "model": "clm",
  "config": {"temperature": 0.8},
  "state": "The customer requests a refund for a duplicate payment.",
  "questions": {
    "queue": {
      "type": "choice",
      "instructions": "Route payment or refund questions to billing; software failures to technical.",
      "criteria": {"billing": "Payment or refund question", "technical": "Software failure"}
    }
  }
}
```

The response remains `model`, `answers`, and `usage`. MCP `system_one` accepts the same optional config. Each request in HTTP batch or MCP `system_one_batch` has its own config. All batch configs are checked before executing any decision; invalid settings report `body.requests.<index>.config` and no item executes. Provider or encoder failures during execution still fail the batch without partial results and may happen after earlier items completed.

CLM bounds each rendered state plus instructions and each candidate by `DECIDEALOT_CLM_MAX_TEXT_BYTES` (default `8192` UTF-8 bytes) before contacting its encoder. Over-limit inputs return `422`; Decidealot does not request truncation. Bytes are not tokens. The encoder's context limit and truncation behavior remain deployment settings. CLM caches candidate vectors only, with configurable LRU capacity and TTL. State vectors are always requested again. Cache hits reduce `usage.input_tokens`; it reports encoder usage for actual calls, not the hypothetical uncached workload.

The response shape always contains `model`, `answers`, and `usage`. The numeric values below are examples. A real model call chooses its own answer and probabilities.

### Choice

Use `choice` when the model must select one named result. `criteria` is an object. Its keys are the only valid choice labels.

```bash
curl --fail --show-error "$base_url/v1/systemone" \
  --header 'Content-Type: application/json' \
  --header "$auth_header" \
  --data '{
    "model": "laya-typed-decisions",
    "state": {
      "operation": "delete",
      "resource": "protected-record",
      "reversible": false
    },
    "questions": {
      "handling": {
        "type": "choice",
        "instructions": "Select the required handling.",
        "criteria": {
          "allow": "The operation is reversible and does not affect protected data.",
          "require_review": "The operation is irreversible or affects protected data.",
          "deny": "The operation is prohibited."
        }
      }
    }
  }'
```

```json
{
  "model": "laya-typed-decisions",
  "answers": {
    "handling": {
      "type": "choice",
      "choice": "require_review",
      "confidence": 0.94,
      "probabilities": {
        "allow": 0.02,
        "require_review": 0.94,
        "deny": 0.04
      }
    }
  },
  "usage": {
    "input_tokens": 91,
    "output_tokens": 0
  }
}
```

### Score

Use `score` for an ordered rubric. `criteria` is a nonempty array. Array position is the score, starting at `0`. The answer can fall between those positions because it is an expected score.

```bash
curl --fail --show-error "$base_url/v1/systemone" \
  --header 'Content-Type: application/json' \
  --header "$auth_header" \
  --data '{
    "model": "von-1.1",
    "state": "A customer reports repeated failures after following the documented setup.",
    "questions": {
      "priority": {
        "type": "score",
        "instructions": "Score the response priority.",
        "criteria": [
          "Routine, answer in the normal queue.",
          "Important, answer soon.",
          "Urgent, interrupt the on-call queue."
        ]
      }
    }
  }'
```

```json
{
  "model": "von-1.1",
  "answers": {
    "priority": {
      "type": "score",
      "score": 1.63,
      "confidence": 0.81,
      "legend": {
        "0": "Routine, answer in the normal queue.",
        "1": "Important, answer soon.",
        "2": "Urgent, interrupt the on-call queue."
      },
      "probabilities": {
        "0": 0.08,
        "1": 0.21,
        "2": 0.71
      }
    }
  },
  "usage": {
    "input_tokens": 62,
    "output_tokens": 0
  }
}
```

### Noul

Use `noul` for a true or false judgment. The response contains the probability that the statement is true.

```bash
curl --fail --show-error "$base_url/v1/systemone" \
  --header 'Content-Type: application/json' \
  --header "$auth_header" \
  --data '{
    "model": "laya",
    "state": [
      "The requested change removes an audit record.",
      "The requester has no documented exception."
    ],
    "questions": {
      "needs_review": {
        "type": "noul",
        "instructions": "Is human review required before the change runs?",
        "criteria": {
          "true": "Review is required before the operation.",
          "false": "The operation may run without human review."
        }
      }
    }
  }'
```

```json
{
  "model": "laya",
  "answers": {
    "needs_review": {
      "type": "noul",
      "noul": 0.97
    }
  },
  "usage": {
    "input_tokens": 58,
    "output_tokens": 0
  }
}
```

### More than one question

Put as many questions as you need into one `questions` object. Each may use a different response type and each answer keeps the matching type. This request sends an object as `state`, a `choice`, a `score`, and a `noul` together:

```json
{
  "model": "von",
  "state": {
    "summary": "A request changes access for an account that holds protected records.",
    "requestedBy": "automated-worker",
    "hasApproval": false
  },
  "questions": {
    "route": {
      "type": "choice",
      "criteria": {
        "standard": "Handle through the normal workflow.",
        "review": "Send to a human reviewer."
      }
    },
    "urgency": {
      "type": "score",
      "criteria": ["routine", "important", "urgent"]
    },
    "can_run": {
      "type": "noul",
      "criteria": {
        "true": "The request may run now.",
        "false": "The request must wait."
      }
    }
  }
}
```

## Use the probabilities

Decidealot returns the model result and its probabilities. Your caller applies the policy. For example, it can allow an `allow` choice with a probability of at least `0.95`, queue the rest for review, and store the full response beside its action record.

`choice.confidence` and `score.confidence` are model confidence values. `choice.probabilities` maps each criterion label to its probability. `score.probabilities` maps score positions to their probabilities. `noul` is the probability of true. Choose thresholds from the cost of being wrong in your workflow.

## Batch independent decisions

`POST /v1/systemone/batch` takes `requests`, a nonempty list of complete System One request objects. Each item can choose a different model, state, and set of questions. The same model can appear more than once. Requests to the same underlying model run in input order. Different models can overlap when their execution policy permits it. Decidealot returns one normal System One result per item, in input order even if another model finishes first. By default, item count has no separate cap beyond `DECIDEALOT_MAX_REQUEST_BYTES`; set `DECIDEALOT_MAX_BATCH_REQUESTS` to a positive number to reject larger batches with `422`. Set `DECIDEALOT_MAX_BATCH_CONCURRENCY` to a positive number to cap simultaneous provider calls made by batches without limiting batch length. This is a Decidealot extension, not a TypeSafe endpoint. The single-model endpoint keeps its existing response shape.

```bash
curl --fail --show-error "$base_url/v1/systemone/batch" \
  --header 'Content-Type: application/json' \
  --header "$auth_header" \
  --data '{
    "requests": [
      {
        "model": "laya",
        "state": "A request would delete an audit record.",
        "questions": {"review": {"type": "noul", "instructions": "Is human review needed?"}}
      },
      {
        "model": "von",
        "state": "A support request arrived with no account identifier.",
        "questions": {"queue": {"type": "choice", "criteria": {"standard": "Normal queue", "review": "Needs manual routing"}}}
      }
    ]
  }'
```

```json
{
  "results": [
    {"model": "laya", "answers": {"review": {"type": "noul", "noul": 0.92}}, "usage": {"input_tokens": 21, "output_tokens": 0}},
    {"model": "von-1.1", "answers": {"queue": {"type": "choice", "choice": "review", "confidence": 0.8, "probabilities": {"standard": 0.2, "review": 0.8}}}, "usage": {"input_tokens": 23, "output_tokens": 0}}
  ]
}
```

The numbers above illustrate the response shape, not a guaranteed model decision. Each `model` names the model that answered, which can differ from its requested alias. With one resident slot, Laya and Von wait and switch models rather than run together. CPU deployments with two slots may overlap them; CUDA inference remains serial. CLM and its Qwen encoder share the local serial lane by default. Set `DECIDEALOT_CLM_PARALLEL_WITH_LOCAL_MODELS=true` only when it is safe for CLM to overlap other local models, such as when Qwen runs on another machine. Hosted Jev can overlap local work. If any item fails, the batch returns its existing validation, busy, or unavailable error; it does not return partial results. Later requests for the same model may not be forwarded after one fails. The MCP `system_one_batch` tool takes the identical `{ "requests": [...] }` arguments and returns the identical structured result.

## CLM requests

CLM uses the same request and response contract as Laya and Von. Set `model` to a CLM selector returned by `GET /v1/models`. It does not accept an endpoint URL in a request. The operator configures one fixed embeddings endpoint at startup, and every CLM request goes only there.

```json
{
  "model": "clm",
  "state": {
    "operation": "delete",
    "reversible": false
  },
  "questions": {
    "needs_review": {
      "type": "noul",
      "instructions": "Does this action need human review before it runs?"
    }
  }
}
```

CLM returns the normal TypeSafe answer shape. Its `usage.input_tokens` value is the `usage.prompt_tokens` reported by the configured embeddings endpoint. It has no output tokens.

## Hosted Jev

Set `DECIDEALOT_TYPESAFE_API_KEY` in the server's private environment. It enables Jev automatically unless `DECIDEALOT_JEV_ENABLED=false`. This key is for outbound TypeSafe calls; `DECIDEALOT_API_KEY` independently protects callers of Decidealot. The entire decision state and questions leave your host for TypeSafe. No request can choose another upstream URL.

```json
{
  "model": "jev-latest",
  "state": {"message": "Please review the attached invoice."},
  "questions": {
    "queue": {
      "type": "choice",
      "instructions": {"task": "Select the appropriate queue."},
      "criteria": {"billing": "Invoices and payments", "general": "Other requests"}
    },
    "urgent": {"type": "noul", "instructions": "Does this need an immediate response?"}
  }
}
```

The response has the same `model`, `answers`, and `usage` fields as local decisions. TypeSafe reports which model answered, which can differ from the selected alias. Choice and Noul probabilities come from TypeSafe; Decidealot does not turn them into an allow or deny action. Hosted Jev does not load, unload, or switch a local provider. TypeSafe's published [OpenAPI contract](https://api.typesafe.ai/openapi.json) defines the upstream question and answer shapes.

## Unload loaded runtimes

Decidealot permits one local provider process in memory by default. Set `DECIDEALOT_MAX_RESIDENT_LOCAL_PROVIDERS` to `2` or `3` to keep more model families resident when RAM or VRAM permits. At capacity, selecting another local provider waits for an idle slot, releases the least recently used idle model, Torch runtime, and CUDA context, then starts the requested provider. This resident capacity does not make CUDA inference parallel. A positive `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` also releases idle local providers. `0` turns off only the idle timer. Hosted Jev has no local runtime to unload.

`POST /v1/models/unload` is the one public unload operation. It is idempotent and releases every loaded local provider. If any local provider has an active decision request, it returns `409` and releases none. A Jev request does not block local unload.

```bash
curl --fail --show-error --request POST "$base_url/v1/models/unload" \
  --header "$auth_header"
```

```json
{
  "status": "unloaded",
  "providers": [
    {
      "name": "laya",
      "wasLoaded": true
    },
    {
      "name": "von",
      "wasLoaded": false
    }
  ]
}
```

## Errors

Malformed System One bodies and unsupported model selectors return the TypeSafe-style `422` validation envelope before a provider receives a request.

```bash
curl --show-error "$base_url/v1/systemone" \
  --header 'Content-Type: application/json' \
  --header "$auth_header" \
  --data '{"model":"unknown-model","state":"anything","questions":{"answer":{"type":"noul"}}}'
```

```json
{
  "detail": [
    {
      "loc": ["body", "model"],
      "msg": "Value error, unknown model 'unknown-model'; use a name returned by GET /v1/models",
      "type": "value_error",
      "input": "unknown-model"
    }
  ]
}
```

Failures outside that input contract use Decidealot's envelope:

| Status | Code | When it happens |
| --- | --- | --- |
| `401` | `UNAUTHORIZED` | Authentication is configured but the Bearer token is absent or wrong. |
| `409` | `PROVIDER_BUSY` | An unload was requested while a provider is serving a decision. |
| `413` | `REQUEST_TOO_LARGE` | The JSON body exceeds `DECIDEALOT_MAX_REQUEST_BYTES`. |
| `503` | `PROVIDER_UNAVAILABLE` | Startup preparation failed, a provider cannot start, times out, rejects authentication upstream, or gives an invalid response. |

```json
{
  "code": "PROVIDER_UNAVAILABLE",
  "message": "the selected provider is unavailable",
  "details": {}
}
```

The model catalog restricts API callers to fixed configured providers. Each decision request supplies a model alias, state, and questions; it cannot supply a provider URL or upstream key.
