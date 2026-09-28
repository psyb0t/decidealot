"""CLM projection-head inference over a fixed configured OpenAI embeddings endpoint."""

import base64
import json
import logging
import math
import os
import struct
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any, cast

import httpx

_embedding_dimensions = 4096
_default_host = "127.0.0.1"
_default_port = 8013
_default_timeout_seconds = 120.0
_maximum_logit_scale = 100.0
_default_projection_dimensions = 512
_checkpoint_config_key = "cfg"
_checkpoint_state_head_key = "state_head"
_checkpoint_action_head_key = "action_head"
_checkpoint_logit_scale_key = "logit_scale"
_checkpoint_projection_dimensions_key = "projection_dim"
_checkpoint_width_key = "width"
_checkpoint_depth_key = "depth"
_checkpoint_activation_key = "activation"
_checkpoint_layer_norm_key = "layernorm"
_checkpoint_residual_key = "residual"
_checkpoint_hidden_size_key = "hidden_size"
_question_type_choice = "choice"
_question_type_noul = "noul"
_question_type_score = "score"
_noul_keys = ("false", "true")
_embeddings_path = "/v1/embeddings"
_system_one_path = "/v1/systemone"
_health_path = "/health"
_application_json_content_type = "application/json"
_authorization_header = "Authorization"
_bearer_prefix = "Bearer "

logger = logging.getLogger(__name__)


class CLMEmbeddingError(RuntimeError):
    """The configured embeddings endpoint returned an unusable response."""


class CLMRuntimeError(RuntimeError):
    """The local CLM head could not load or execute."""


class CLMInputError(ValueError):
    """The loopback CLM request is malformed."""


@dataclass(frozen=True)
class EmbeddingBatch:
    """Validated vectors and billable input tokens from one embeddings request."""

    vectors: list[list[float]]
    input_tokens: int


def decode_embedding_response(body: object, expected_count: int) -> EmbeddingBatch:
    """Validate and order OpenAI-compatible base64 or float embeddings by their index."""

    if expected_count < 1:
        raise CLMEmbeddingError("expected at least one embedding")
    if not isinstance(body, Mapping):
        raise CLMEmbeddingError("embeddings response must be an object")
    response_body = cast(Mapping[str, object], body)
    encoded_embeddings = response_body.get("data")
    if not isinstance(encoded_embeddings, list):
        raise CLMEmbeddingError("embeddings response has an unexpected vector count")
    embedding_entries = cast(list[object], encoded_embeddings)
    if len(embedding_entries) != expected_count:
        raise CLMEmbeddingError("embeddings response has an unexpected vector count")

    vectors: list[list[float] | None] = [None] * expected_count
    for raw_embedding in embedding_entries:
        if not isinstance(raw_embedding, Mapping):
            raise CLMEmbeddingError("embeddings response has an invalid vector entry")
        encoded_embedding = cast(Mapping[str, object], raw_embedding)
        index = encoded_embedding.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise CLMEmbeddingError("embeddings response vector index must be an integer")
        if index < 0 or index >= expected_count or vectors[index] is not None:
            raise CLMEmbeddingError(
                "embeddings response vector indexes must be unique and complete"
            )
        vectors[index] = _decode_embedding_vector(encoded_embedding.get("embedding"))

    if any(vector is None for vector in vectors):
        raise CLMEmbeddingError("embeddings response vector indexes are incomplete")
    usage = response_body.get("usage")
    input_tokens = _input_tokens(usage)
    completed_vectors = [vector for vector in vectors if vector is not None]
    return EmbeddingBatch(vectors=completed_vectors, input_tokens=input_tokens)


def _decode_embedding_vector(value: object) -> list[float]:
    if isinstance(value, str):
        try:
            encoded = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise CLMEmbeddingError("embeddings response contains invalid base64") from error
        expected_size = struct.calcsize(f"<{_embedding_dimensions}f")
        if len(encoded) != expected_size:
            raise CLMEmbeddingError("embeddings response has an unexpected vector width")
        values = list(struct.unpack(f"<{_embedding_dimensions}f", encoded))
    elif isinstance(value, list):
        number_values = cast(list[object], value)
        if len(number_values) != _embedding_dimensions:
            raise CLMEmbeddingError("embeddings response has an unexpected vector width")
        if any(
            isinstance(item, bool) or not isinstance(item, int | float) for item in number_values
        ):
            raise CLMEmbeddingError("embeddings response vector must contain numbers")
        values = [float(cast(int | float, item)) for item in number_values]
    else:
        raise CLMEmbeddingError("embeddings response vector must be base64 or a number array")
    if not all(math.isfinite(item) for item in values):
        raise CLMEmbeddingError("embeddings response vector contains a non-finite number")
    return values


def _input_tokens(usage: object) -> int:
    if not isinstance(usage, Mapping):
        return 0
    typed_usage = cast(Mapping[str, object], usage)
    value = typed_usage.get("prompt_tokens")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def to_text(value: object, indentation: int = 0) -> str:
    """Render TypeSafe JSON state and criteria with CLM's training-time layout."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        if not math.isfinite(float(value)):
            raise CLMInputError("state and criteria must not contain non-finite numbers")
        return str(value)
    indentation_text = " " * indentation
    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[str, object], value)
        lines: list[str] = []
        for key_text, nested_value in mapping_value.items():
            if _has_nested_value(nested_value):
                rendered_nested_value = to_text(nested_value, indentation + 2)
                lines.append(f"{indentation_text}{key_text}:\n{rendered_nested_value}")
                continue
            lines.append(f"{indentation_text}{key_text}: {to_text(nested_value)}")
        separator = "\n\n" if indentation == 0 else "\n"
        return separator.join(lines)
    if isinstance(value, list):
        list_value = cast(list[object], value)
        lines = []
        for nested_value in list_value:
            if _has_nested_value(nested_value):
                rendered_nested_value = to_text(nested_value, indentation + 2)
                lines.append(f"{indentation_text}-\n{rendered_nested_value}")
                continue
            lines.append(f"{indentation_text}- {to_text(nested_value)}")
        return "\n".join(lines)
    return json.dumps(value, ensure_ascii=False)


def _has_nested_value(value: object) -> bool:
    if isinstance(value, Mapping):
        return bool(cast(Mapping[str, object], value))
    if isinstance(value, list):
        return bool(cast(list[object], value))
    return False


def state_text(state: object, instructions: object) -> str:
    """Append one question's instructions to the shared state text."""

    rendered_state = to_text(state).strip()
    rendered_instructions = to_text(instructions).strip()
    if rendered_state and rendered_instructions:
        return f"{rendered_state}\n\n{rendered_instructions}"
    return rendered_state or rendered_instructions


def question_candidates(question: Mapping[str, object]) -> tuple[list[str], list[str]]:
    """Return answer keys and the corresponding action-head candidate texts."""

    question_type = question.get("type")
    criteria = question.get("criteria")
    instructions = to_text(question.get("instructions")).strip()
    if question_type == _question_type_choice:
        if not isinstance(criteria, Mapping) or not criteria:
            raise CLMInputError("choice questions need non-empty criteria")
        choice_criteria = cast(Mapping[str, object], criteria)
        keys = list(choice_criteria)
        candidates = [
            to_text(value) if value is not None and value != "" else key
            for key, value in zip(keys, choice_criteria.values(), strict=True)
        ]
        return keys, candidates
    if question_type == _question_type_score:
        if not isinstance(criteria, list) or not criteria:
            raise CLMInputError("score questions need non-empty criteria")
        score_criteria = cast(list[object], criteria)
        keys = [str(index) for index in range(len(score_criteria))]
        return keys, [to_text(value) for value in score_criteria]
    if question_type != _question_type_noul:
        raise CLMInputError("question type is unsupported")
    descriptions: Mapping[str, object] = (
        cast(Mapping[str, object], criteria) if isinstance(criteria, Mapping) else {}
    )
    noul_candidates: list[str] = []
    for key in _noul_keys:
        description = descriptions.get(key)
        if description is None or description == "":
            description = (
                f"Yes. This is true: {instructions}"
                if key == "true"
                else (f"No. This is false: {instructions}")
            )
        noul_candidates.append(f"{key}: {to_text(description)}")
    return list(_noul_keys), noul_candidates


def answer_from_logits(
    question: Mapping[str, object],
    keys: Sequence[str],
    logits: Sequence[float],
) -> dict[str, Any]:
    """Project CLM similarities into the official TypeSafe answer shape."""

    if not keys or len(keys) != len(logits):
        raise CLMInputError("question candidates and logits must have equal non-zero lengths")
    probabilities = _softmax(logits)
    distribution = dict(zip(keys, probabilities, strict=True))
    question_type = question.get("type")
    if question_type == _question_type_noul:
        return {"type": _question_type_noul, "noul": distribution["true"]}
    confidence = _confidence(probabilities)
    if question_type == _question_type_choice:
        selected_index = max(range(len(probabilities)), key=probabilities.__getitem__)
        return {
            "type": _question_type_choice,
            "choice": keys[selected_index],
            "confidence": confidence,
            "probabilities": distribution,
        }
    if question_type != _question_type_score:
        raise CLMInputError("question type is unsupported")
    criteria = question.get("criteria")
    if not isinstance(criteria, list):
        raise CLMInputError("score questions need criteria")
    score_criteria = cast(list[object], criteria)
    return {
        "type": _question_type_score,
        "score": sum(index * probability for index, probability in enumerate(probabilities)),
        "confidence": confidence,
        "legend": {str(index): to_text(value) for index, value in enumerate(score_criteria)},
        "probabilities": distribution,
    }


def _softmax(logits: Sequence[float]) -> list[float]:
    numeric_logits = [float(logit) for logit in logits]
    if not all(math.isfinite(logit) for logit in numeric_logits):
        raise CLMRuntimeError("projection heads returned non-finite logits")
    maximum = max(numeric_logits)
    exponentials = [math.exp(logit - maximum) for logit in numeric_logits]
    total = sum(exponentials)
    if total <= 0 or not math.isfinite(total):
        raise CLMRuntimeError("projection heads returned invalid logits")
    return [exponential / total for exponential in exponentials]


def _confidence(probabilities: Sequence[float]) -> float:
    if len(probabilities) < 2:
        return 1.0
    selected_index = max(range(len(probabilities)), key=probabilities.__getitem__)
    remaining = [
        probability for index, probability in enumerate(probabilities) if index != selected_index
    ]
    return max(0.0, min(1.0, probabilities[selected_index] - sum(remaining) / len(remaining)))


class CLMEngine:
    """One checkpoint and one fixed remote embeddings boundary in a child process."""

    def __init__(
        self,
        checkpoint_path: Path,
        embeddings_url: str,
        embeddings_model: str,
        embeddings_api_key: str | None,
        timeout_seconds: float,
        device: str,
    ) -> None:
        self._checkpoint_path = checkpoint_path
        self._embeddings_url = embeddings_url
        self._embeddings_model = embeddings_model
        self._embeddings_api_key = embeddings_api_key
        self._timeout_seconds = timeout_seconds
        self._device = device
        self._load_lock = threading.Lock()
        self._state_head: Any | None = None
        self._action_head: Any | None = None
        self._scale = 1.0

    def answer(self, payload: Mapping[str, object]) -> dict[str, Any]:
        """Call the configured encoder once, then apply the local projection heads."""

        model = payload.get("model")
        state = payload.get("state")
        questions = payload.get("questions")
        if not isinstance(model, str) or not model:
            raise CLMInputError("model must be a non-empty string")
        if not isinstance(questions, Mapping) or not questions:
            raise CLMInputError("questions must be a non-empty object")
        question_mapping = cast(Mapping[str, object], questions)

        question_entries: list[tuple[str, Mapping[str, object], list[str], list[str]]] = []
        state_texts: list[str] = []
        candidate_texts: list[str] = []
        for question_name, raw_question in question_mapping.items():
            if not isinstance(raw_question, Mapping):
                raise CLMInputError("questions must map names to objects")
            question = cast(Mapping[str, object], raw_question)
            keys, candidates = question_candidates(question)
            question_entries.append((question_name, question, keys, candidates))
            state_texts.append(state_text(state, question.get("instructions")))
            candidate_texts.extend(candidates)

        embeddings = self._request_embeddings([*state_texts, *candidate_texts])
        self._ensure_heads()
        answers = self._answer_embeddings(question_entries, embeddings.vectors)
        return {
            "model": model,
            "answers": answers,
            "usage": {"input_tokens": embeddings.input_tokens, "output_tokens": 0},
        }

    def _request_embeddings(self, texts: list[str]) -> EmbeddingBatch:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout_seconds),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                return request_embeddings(
                    client=client,
                    url=self._embeddings_url,
                    model=self._embeddings_model,
                    texts=texts,
                    api_key=self._embeddings_api_key,
                )
        except httpx.HTTPError as error:
            raise CLMEmbeddingError("configured embeddings endpoint is unavailable") from error

    def _ensure_heads(self) -> None:
        if self._state_head is not None and self._action_head is not None:
            return
        with self._load_lock:
            if self._state_head is not None and self._action_head is not None:
                return
            self._load_heads()

    def _load_heads(self) -> None:
        try:
            torch = cast(Any, import_module("torch"))
            nn = cast(Any, import_module("torch.nn"))
        except ImportError as error:
            raise CLMRuntimeError("CLM requires the image's local Torch runtime") from error
        try:
            checkpoint = torch.load(
                self._checkpoint_path,
                map_location="cpu",
                weights_only=True,
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise CLMRuntimeError("load verified CLM checkpoint") from error
        if not isinstance(checkpoint, Mapping):
            raise CLMRuntimeError("CLM checkpoint must contain a mapping")
        typed_checkpoint = cast(Mapping[str, object], checkpoint)
        config = typed_checkpoint.get(_checkpoint_config_key)
        if not isinstance(config, Mapping):
            raise CLMRuntimeError("CLM checkpoint configuration is invalid")
        typed_config = cast(Mapping[str, object], config)
        width = _positive_integer(typed_config.get(_checkpoint_width_key), _checkpoint_width_key)
        depth = _positive_integer(typed_config.get(_checkpoint_depth_key), _checkpoint_depth_key)
        if depth < 2:
            raise CLMRuntimeError("CLM checkpoint depth must be at least two")
        hidden_size = _positive_integer(
            typed_config.get(_checkpoint_hidden_size_key, _embedding_dimensions),
            _checkpoint_hidden_size_key,
        )
        if hidden_size != _embedding_dimensions:
            raise CLMRuntimeError("CLM checkpoint does not match the configured embedding width")
        projection_dimensions = _positive_integer(
            typed_checkpoint.get(
                _checkpoint_projection_dimensions_key,
                typed_config.get(
                    _checkpoint_projection_dimensions_key,
                    _default_projection_dimensions,
                ),
            ),
            _checkpoint_projection_dimensions_key,
        )
        activation = typed_config.get(_checkpoint_activation_key, "gelu")
        if not isinstance(activation, str):
            raise CLMRuntimeError("CLM checkpoint activation is invalid")
        activation_types = {"gelu": nn.GELU, "relu": nn.ReLU, "silu": nn.SiLU}
        activation_type = activation_types.get(activation)
        if activation_type is None:
            raise CLMRuntimeError("CLM checkpoint activation is unsupported")
        layer_norm = bool(typed_config.get(_checkpoint_layer_norm_key, False))
        residual = bool(typed_config.get(_checkpoint_residual_key, False))
        head_type = _projection_head_type(
            nn=nn,
            width=width,
            depth=depth,
            projection_dimensions=projection_dimensions,
            activation_type=activation_type,
            layer_norm=layer_norm,
            residual=residual,
        )
        state_head = head_type()
        action_head = head_type()
        try:
            state_head.load_state_dict(
                _checkpoint_mapping(typed_checkpoint, _checkpoint_state_head_key)
            )
            action_head.load_state_dict(
                _checkpoint_mapping(typed_checkpoint, _checkpoint_action_head_key)
            )
            state_head.eval().to(self._device)
            action_head.eval().to(self._device)
            scale = float(
                torch.as_tensor(typed_checkpoint[_checkpoint_logit_scale_key]).float().exp()
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            raise CLMRuntimeError("CLM checkpoint weights are invalid") from error
        self._state_head = state_head
        self._action_head = action_head
        self._scale = min(scale, _maximum_logit_scale)
        logger.info("CLM projection heads loaded", extra={"device": self._device})

    def _answer_embeddings(
        self,
        question_entries: Sequence[tuple[str, Mapping[str, object], list[str], list[str]]],
        vectors: list[list[float]],
    ) -> dict[str, Any]:
        try:
            torch = cast(Any, import_module("torch"))
            functional = cast(Any, import_module("torch.nn.functional"))
        except ImportError as error:
            raise CLMRuntimeError("CLM requires the image's local Torch runtime") from error
        state_count = len(question_entries)
        state_vectors = vectors[:state_count]
        candidate_vectors = vectors[state_count:]
        expected_candidate_count = sum(len(candidates) for _, _, _, candidates in question_entries)
        if len(state_vectors) != state_count or len(candidate_vectors) != expected_candidate_count:
            raise CLMRuntimeError("configured embeddings endpoint returned an incomplete batch")
        if self._state_head is None or self._action_head is None:
            raise CLMRuntimeError("CLM projection heads are not loaded")
        try:
            with torch.no_grad():
                state_tensor = torch.tensor(state_vectors, dtype=torch.float32, device=self._device)
                candidate_tensor = torch.tensor(
                    candidate_vectors,
                    dtype=torch.float32,
                    device=self._device,
                )
                projected_states = functional.normalize(self._state_head(state_tensor), dim=-1)
                projected_candidates = functional.normalize(
                    self._action_head(candidate_tensor),
                    dim=-1,
                )
                answers: dict[str, Any] = {}
                candidate_start = 0
                for state_index, (name, question, keys, candidates) in enumerate(question_entries):
                    candidate_end = candidate_start + len(candidates)
                    logits = (
                        self._scale
                        * torch.matmul(
                            projected_candidates[candidate_start:candidate_end],
                            projected_states[state_index],
                        )
                    ).tolist()
                    answers[name] = answer_from_logits(question, keys, logits)
                    candidate_start = candidate_end
        except RuntimeError as error:
            raise CLMRuntimeError("run CLM projection heads") from error
        return answers


def request_embeddings(
    client: httpx.Client,
    url: str,
    model: str,
    texts: Sequence[str],
    api_key: str | None,
) -> EmbeddingBatch:
    """Call an OpenAI-compatible embeddings API with the CLM wire contract."""

    request_body = {
        "model": model,
        "input": list(texts),
        "encoding_format": "base64",
    }
    headers = {"Content-Type": _application_json_content_type}
    if api_key is not None:
        headers[_authorization_header] = f"{_bearer_prefix}{api_key}"
    try:
        response = client.post(url, headers=headers, json=request_body)
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        logger.warning(
            "CLM embeddings request was rejected",
            extra={"status_code": error.response.status_code},
        )
        raise CLMEmbeddingError("configured embeddings endpoint rejected the request") from error
    except httpx.TimeoutException as error:
        logger.warning("CLM embeddings request timed out")
        raise CLMEmbeddingError("configured embeddings endpoint timed out") from error
    except httpx.HTTPError as error:
        logger.warning("CLM embeddings request failed", extra={"error": str(error)})
        raise CLMEmbeddingError("configured embeddings endpoint is unavailable") from error
    try:
        decoded = response.json()
    except ValueError as error:
        raise CLMEmbeddingError("configured embeddings endpoint returned invalid JSON") from error
    return decode_embedding_response(decoded, expected_count=len(texts))


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CLMRuntimeError(f"CLM checkpoint {name} must be a positive integer")
    return value


def _checkpoint_mapping(checkpoint: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = checkpoint.get(key)
    if not isinstance(value, Mapping):
        raise CLMRuntimeError(f"CLM checkpoint is missing {key}")
    return cast(Mapping[str, object], value)


def _projection_head_type(
    nn: Any,
    width: int,
    depth: int,
    projection_dimensions: int,
    activation_type: Any,
    layer_norm: bool,
    residual: bool,
) -> Any:
    class ProjectionHead(nn.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()  # pyright: ignore[reportUnknownMemberType]
            self.inp = nn.Linear(_embedding_dimensions, width)
            self.hidden = nn.ModuleList(nn.Linear(width, width) for _ in range(depth - 2))
            self.norms = nn.ModuleList(
                nn.LayerNorm(width) if layer_norm else nn.Identity() for _ in range(depth - 2)
            )
            self.out = nn.Linear(width, projection_dimensions)
            self.act = activation_type()
            self.residual = residual

        def forward(self, tensor: Any) -> Any:
            transformed = self.act(self.inp(tensor))
            for hidden_layer, normalization_layer in zip(self.hidden, self.norms, strict=True):
                candidate = self.act(normalization_layer(hidden_layer(transformed)))
                transformed = transformed + candidate if self.residual else candidate
            return self.out(transformed)

    return ProjectionHead


def create_application(engine: CLMEngine) -> Any:
    """Build the private loopback HTTP application for one CLM child process."""

    from fastapi import Body, FastAPI, HTTPException

    application = FastAPI()

    @application.get(_health_path)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post(_system_one_path)
    def system_one(payload: Annotated[object, Body()]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise HTTPException(status_code=422, detail="request body must be an object")
        try:
            return engine.answer(cast(Mapping[str, object], payload))
        except CLMInputError as error:
            logger.warning("CLM request rejected", extra={"error": str(error)})
            raise HTTPException(status_code=422, detail=str(error)) from error
        except CLMEmbeddingError as error:
            logger.warning("CLM embeddings request failed", extra={"error": str(error)})
            raise HTTPException(
                status_code=503,
                detail="configured embeddings endpoint is unavailable",
            ) from error
        except CLMRuntimeError as error:
            logger.warning("CLM projection-head request failed", extra={"error": str(error)})
            raise HTTPException(
                status_code=503,
                detail="local CLM provider is unavailable",
            ) from error

    return application


def run_clm(checkpoint_path: Path) -> None:
    """Start CLM with fixed environment supplied only by Decidealot's supervisor."""

    import uvicorn

    embeddings_url = _required_environment("CLM_EMBEDDINGS_URL")
    embeddings_model = _required_environment("CLM_EMBEDDINGS_MODEL")
    timeout_seconds = _environment_float(
        "CLM_EMBEDDINGS_TIMEOUT_SECONDS",
        _default_timeout_seconds,
    )
    engine = CLMEngine(
        checkpoint_path=checkpoint_path,
        embeddings_url=embeddings_url,
        embeddings_model=embeddings_model,
        embeddings_api_key=os.environ.get("CLM_EMBEDDINGS_API_KEY") or None,
        timeout_seconds=timeout_seconds,
        device=os.environ.get("CLM_DEVICE", "cpu"),
    )
    uvicorn.run(
        create_application(engine),
        host=os.environ.get("CLM_HOST", _default_host),
        port=_environment_integer("CLM_PORT", _default_port),
    )


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CLMRuntimeError(f"{name} must be configured")
    return value


def _environment_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise CLMRuntimeError(f"{name} must be a positive number") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise CLMRuntimeError(f"{name} must be a positive number")
    return parsed


def _environment_integer(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise CLMRuntimeError(f"{name} must be an integer") from error
    if parsed < 1 or parsed > 65535:
        raise CLMRuntimeError(f"{name} must be a valid TCP port")
    return parsed
