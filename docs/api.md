# API

Decidealot serves the two TypeSafe operations, `POST /v1/systemone` and `GET /v1/models`, from local Laya and Von models. Both follow the published TypeSafe schemas. Decidealot owns request authentication, request size limits, model selection, provider readiness, and its own JSON error envelope for failures the TypeSafe contract does not describe.

## Request

Send a JSON object with `model`, `state`, and a `questions` map holding at least one question. All three are required. `state` is the content every question refers to, given as a string, an object, or an array. Each question is keyed by a name you choose, and the answers come back under those same names.

```json
{
  "model": "von",
  "state": "An incoming support request needs routing.",
  "questions": {
    "queue": {
      "type": "choice",
      "instructions": "Which queue should handle this support request?",
      "criteria": {
        "billing": "Payments, invoices, refunds, or account charges.",
        "technical": "A product fault, outage, or integration problem."
      }
    }
  }
}
```

A question is a `choice`, `score`, or `noul` value selected by its `type` field. `choice` requires a `criteria` object whose keys are the valid result labels and whose values explain them. Do not send an `options` field. `score` requires an ordered `criteria` array with at least one entry, where each position is that level's score, starting at zero. `noul` may include `criteria` describing what counts as `true` and what counts as `false`. `instructions` is optional on every type, and any instruction or criterion value may be a string, an object, or an array.

Laya and Von accept narrower values than the official schema does. Decidealot adapts an accepted request before forwarding it, so nothing the official API allows is rejected by a local model over its own shape. A missing `instructions` becomes an empty string, and nested object or array values are rendered as compact JSON text. Choice labels, score-level order, and `state` are forwarded unchanged. Fields Decidealot does not recognize are dropped and never reach a model.

When `DECIDEALOT_API_KEY` is configured, include `Authorization: Bearer <your-key>` on both `POST /v1/systemone` and `GET /v1/models`. `X-Request-Id` accepts UUID or ULID values and is returned in every response. Invalid or absent IDs are replaced with a new UUID.

## Responses

A successful response holds exactly `model`, `answers`, and `usage`. `model` is the public name of the local model that answered, which may differ from the alias you sent. `answers` are keyed by your question names, and each answer carries the fields the official schema defines for its type. Provider-only fields, such as Laya's routing decision and its per-answer action metadata, are stripped at the boundary.

Decidealot does not filter answers based on probability. Consumers choose their own confidence threshold because the right threshold depends on the cost of a false positive and false negative.

## Validation failures

A request that does not satisfy the TypeSafe schema returns `422` with the official validation envelope, before any model is contacted. An unsupported `model` value is reported the same way, against `body.model`.

```json
{
  "detail": [
    {
      "loc": ["body", "questions", "urgency", "score", "criteria"],
      "msg": "List should have at least 1 item after validation, not 0",
      "type": "too_short"
    }
  ]
}
```

A local model can still reject a request Decidealot accepted, for example when a question's options do not fit the model's input budget. A provider `422` that already uses this envelope keeps every entry it reported. A provider that reports a bare message instead is restated as one entry against `body.questions`. Any other provider status becomes a `503`.

## Decidealot error envelope

Failures outside the TypeSafe contract use Decidealot's own envelope:

```json
{
  "code": "PROVIDER_UNAVAILABLE",
  "message": "the selected local provider is unavailable",
  "details": {}
}
```

The possible codes are `UNAUTHORIZED`, `REQUEST_TOO_LARGE`, `PROVIDER_UNAVAILABLE`, `PROVIDER_BUSY`, and `INVALID_REQUEST` on the provider lifecycle endpoints.

## Model listing

`GET /v1/models` returns the official TypeSafe `ModelMetadataList` and requires the configured Bearer token exactly as `POST /v1/systemone` does.

```json
{
  "models": [
    {
      "name": "laya",
      "description": "Laya System One decisions with automatic checkpoint routing.",
      "release_date": "2026-09-23"
    }
  ]
}
```

Every `name` in the list is accepted as `model` on `POST /v1/systemone`. The `jev-*` compatibility aliases are served by the configured default backend, so their description and `release_date` follow whichever model serves them.

## Provider lifecycle

`POST /v1/models/{model}/unload` unloads the local provider selected by a supported model alias. `POST /v1/models/unload` unloads every provider. Both endpoints require the configured Bearer token when `DECIDEALOT_API_KEY` is set. They return `200` when a provider is already unloaded, with `wasLoaded: false`.

An unload request returns `409` with `PROVIDER_BUSY` if the selected provider has an in-flight System One request. The all-provider endpoint is atomic. It unloads none when any provider is busy.

Decidealot starts no model at service startup and keeps at most one local model process resident. A request for another backend waits for an active request to complete, then unloads the resident process before starting the selected one. When `DECIDEALOT_PROVIDER_IDLE_UNLOAD_SECONDS` is positive, Decidealot also unloads the resident provider after that duration without requests. Setting it to `0` disables only timeout-based unloads. A later `POST /v1/systemone` starts the selected provider and waits for its health endpoint before forwarding the request. `/health` indicates that Decidealot can manage provider lifecycle. It does not promise that any provider is already loaded.

## Model aliases

See the [root README](../README.md#pick-a-model) for supported aliases. A caller cannot select a URL, a command, a model filesystem path, or a provider runtime argument.
