# API

Decidealot runs local Laya and Von decision models through the TypeSafe System One HTTP contract. You send the thing that needs judging as `state`, define the allowed answer shape, and get a typed answer back. It does not host TypeSafe Jev and it does not accept Jev model names.

## Base URL and startup

The examples use a local CPU container at `http://127.0.0.1:8080`. On a fresh model directory, Decidealot downloads and verifies both Laya and Von bundles before `/health` returns `200`. That first startup can take minutes. It prepares files only. Neither model nor Torch stays loaded until a `POST /v1/systemone` request selects a model.

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

## Authentication

Authentication is off unless the container has `DECIDEALOT_API_KEY`. When it is set, every public API call except `/health` needs this header:

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

## List selectable models

`GET /v1/models` lists every accepted `model` value. Use one of these names in each `POST /v1/systemone` call. There is no default model.

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

## Make decisions

`POST /v1/systemone` always needs these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `model` | string | One exact selector from `GET /v1/models`. |
| `state` | string, object, or array | The raw thing to classify, score, or judge. |
| `questions` | object | One or more named `choice`, `score`, or `noul` questions. |

Question names are yours. The response repeats them under `answers`. `instructions` is optional for all question types. It, `state`, and criterion values can be strings, JSON objects, or JSON arrays. Decidealot converts nested instruction and criterion values to text for local model runtimes without changing the choice labels, score order, or `state`.

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

Use `noul` for a true or false judgment. The response is the probability that the statement is true. It does not include `choice`, `score`, `confidence`, `legend`, or a per-label probability map.

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

Decidealot returns the model result. It does not silently allow, deny, filter, or rewrite it. Your caller owns the policy. For example, a caller could allow only an `allow` choice with a probability of at least `0.95`, queue the rest for review, and store the full response beside its own action record.

`choice.confidence` and `score.confidence` are model confidence values. `choice.probabilities` maps each criterion label to its probability. `score.probabilities` maps score positions to their probabilities. `noul` is the probability of true. Do not treat an example threshold as universal. Set it from the cost of being wrong in your workflow.

## Unload loaded runtimes

Decidealot permits at most one provider process in memory. Selecting Laya after Von, or the reverse, waits for an active request to finish, releases the old model and Torch runtime, then starts the requested provider. A positive `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` also releases an idle provider. `0` turns off only the idle timer.

`POST /v1/models/unload` is the one public unload operation. It is idempotent and releases every loaded provider. If any provider has an active decision request, it returns `409` and releases none.

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

Malformed System One bodies and unknown model selectors return the TypeSafe-style `422` validation envelope before a provider receives a request. `jev`, `jev-latest`, and every other Jev selector are unknown because Decidealot runs only local Laya and Von models.

```bash
curl --show-error "$base_url/v1/systemone" \
  --header 'Content-Type: application/json' \
  --header "$auth_header" \
  --data '{"model":"jev","state":"anything","questions":{"answer":{"type":"noul"}}}'
```

```json
{
  "detail": [
    {
      "loc": ["body", "model"],
      "msg": "Value error, unknown model 'jev'; use a name returned by GET /v1/models",
      "type": "value_error",
      "input": "jev"
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
| `503` | `PROVIDER_UNAVAILABLE` | Startup preparation failed, a provider cannot start, times out, or gives an invalid response. |

```json
{
  "code": "PROVIDER_UNAVAILABLE",
  "message": "the selected local provider is unavailable",
  "details": {}
}
```

The service never accepts a model URL, command, filesystem path, or runtime argument from an API caller. It selects only the fixed local Laya and Von providers named by the model catalog.
