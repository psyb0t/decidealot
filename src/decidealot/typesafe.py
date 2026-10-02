"""The TypeSafe System One contract plus Decidealot's batch envelope.

System One models mirror the published TypeSafe OpenAPI document. The separate
batch models wrap those requests and results without changing the official
single-request contract. Native provider-only fields never reach a caller.
"""

import logging
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Literal, cast

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ValidationError

from decidealot.errors import ProviderUnavailableError, TypeSafeValidationError

logger = logging.getLogger(__name__)

JSONValue = str | dict[str, Any] | list[Any]
OptionalJSONValue = JSONValue | None

BODY_LOCATION = "body"
MODEL_LOCATION = (BODY_LOCATION, "model")
QUESTIONS_LOCATION = (BODY_LOCATION, "questions")

_detail_fields = ("loc", "msg", "type", "input", "ctx")
_value_error_type = "value_error"
_value_error_prefix = "Value error, "
_invalid_provider_response_message = "the selected provider returned an invalid response"
_provider_rejected_request_message = "the selected provider rejected the request"


class NoulCriteria(BaseModel):
    """Criteria defining what counts as a yes or no answer."""

    true: OptionalJSONValue = None
    false: OptionalJSONValue = None


class NoulQuestion(BaseModel):
    """A yes/no question or statement, answered with the probability of yes."""

    type: Literal["noul"]
    instructions: OptionalJSONValue = None
    criteria: NoulCriteria | None = None


class ChoiceQuestion(BaseModel):
    """A question that selects one option from the choices in criteria."""

    type: Literal["choice"]
    instructions: OptionalJSONValue = None
    criteria: dict[str, OptionalJSONValue]


class ScoreQuestion(BaseModel):
    """A question that assigns a score using an ordered rubric."""

    type: Literal["score"]
    instructions: OptionalJSONValue = None
    criteria: list[JSONValue] = Field(min_length=1)


Question = Annotated[NoulQuestion | ChoiceQuestion | ScoreQuestion, Field(discriminator="type")]


class SystemOneRequest(BaseModel):
    """Content and named questions to evaluate together using one model."""

    state: JSONValue
    model: str
    questions: dict[str, Question] = Field(min_length=1)


class SystemOneBatchRequest(BaseModel):
    """Independent System One requests evaluated as one batch."""

    requests: list[SystemOneRequest] = Field(min_length=1)


class NoulAnswer(BaseModel):
    """The probability of a yes answer or a true statement."""

    type: Literal["noul"]
    noul: float


class ChoiceAnswer(BaseModel):
    """The selected choice, confidence, and probabilities for a choice question."""

    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: dict[str, float]


class ScoreAnswer(BaseModel):
    """An expected score with its rubric, confidence, and score-level probabilities."""

    type: Literal["score"]
    score: float
    confidence: float
    legend: dict[str, JSONValue]
    probabilities: dict[str, float]


Answer = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer, Field(discriminator="type")]


class Usage(BaseModel):
    """Token usage for the request."""

    input_tokens: int
    output_tokens: int


class SystemOneResponse(BaseModel):
    """Answers grouped by question name, with the model used and token usage."""

    model: str
    answers: dict[str, Answer] = Field(min_length=1)
    usage: Usage


class SystemOneBatchResponse(BaseModel):
    """One result for each request, preserving input order."""

    results: list[SystemOneResponse]


class ModelMetadata(BaseModel):
    """A model or model alias available to the authenticated account."""

    name: str
    description: str
    release_date: str


class ModelMetadataList(BaseModel):
    """Models and aliases available to the authenticated account."""

    models: list[ModelMetadata]


class _ValidationErrorDetail(BaseModel):
    """The required fields of one official validation error entry."""

    loc: list[str | int]
    msg: str
    type: str


class _HTTPValidationError(BaseModel):
    """The official envelope returned with HTTP status 422."""

    detail: list[_ValidationErrorDetail]


def parse_system_one_request(body: object) -> SystemOneRequest:
    """Validate an untrusted body against the official System One request schema.

    Raises TypeSafeValidationError carrying the official detail entries when the
    body is not an object, misses a required field, or holds a malformed question.
    """

    try:
        return SystemOneRequest.model_validate(body)
    except ValidationError as error:
        raise TypeSafeValidationError(
            validation_detail(error.errors(), (BODY_LOCATION,))
        ) from error


def parse_system_one_batch_request(
    body: object, max_batch_requests: int = 0
) -> SystemOneBatchRequest:
    """Validate every batch item before any provider receives work."""

    try:
        batch = SystemOneBatchRequest.model_validate(body)
    except ValidationError as error:
        raise TypeSafeValidationError(
            validation_detail(error.errors(), (BODY_LOCATION,))
        ) from error
    if max_batch_requests and len(batch.requests) > max_batch_requests:
        raise TypeSafeValidationError(
            [
                {
                    "loc": [BODY_LOCATION, "requests"],
                    "msg": f"List should have at most {max_batch_requests} items",
                    "type": "too_long",
                }
            ]
        )
    return batch


def validation_detail(
    errors: Iterable[Mapping[str, Any]],
    location_prefix: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Keep only the official ValidationError fields of each reported failure."""

    return [_validation_detail_entry(error, location_prefix) for error in errors]


def unknown_model_detail(requested_model: str) -> list[dict[str, Any]]:
    """Describe an unsupported model selector as an official validation failure."""

    return [
        {
            "loc": list(MODEL_LOCATION),
            "msg": (
                f"{_value_error_prefix}unknown model {requested_model!r}; "
                "use a name returned by GET /v1/models"
            ),
            "type": _value_error_type,
            "input": requested_model,
        }
    ]


def validation_error_content(detail: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Wrap detail entries in the official HTTPValidationError envelope."""

    return {"detail": jsonable_encoder(list(detail))}


def project_system_one_response(body: object, model_name: str | None) -> dict[str, Any]:
    """Reduce a native provider result to the official model, answers, and usage.

    Local providers report the public Decidealot name. Hosted providers keep the
    upstream model that actually answered, which may differ from the request alias.
    Provider-only fields such as routing decisions and per-answer action metadata
    are dropped.

    Raises ProviderUnavailableError when the provider result cannot satisfy the
    official response schema.
    """

    answered = _as_json_object(body)
    if answered is None:
        logger.warning("provider response is not an object")
        raise ProviderUnavailableError(_invalid_provider_response_message)
    try:
        response_body = {**answered, "model": model_name} if model_name is not None else answered
        response = SystemOneResponse.model_validate(response_body)
    except ValidationError as error:
        logger.warning(
            "provider response does not match the official schema",
            extra={"error_count": error.error_count()},
        )
        raise ProviderUnavailableError(_invalid_provider_response_message) from error
    return response.model_dump(mode="json")


def provider_validation_content(body: object) -> dict[str, Any]:
    """Keep a provider 422 body that is already official, otherwise restate it.

    A provider that answers with the documented envelope keeps every detail entry it
    reported. Native providers that report a bare message instead are restated as one
    official detail entry against the questions map they rejected.
    """

    reported = _as_json_object(body)
    if reported is not None and _is_official_validation_envelope(reported):
        return dict(reported)
    logger.info(
        "restating a provider validation failure",
        extra={"reason": "provider_detail_not_official"},
    )
    return validation_error_content(
        [
            {
                "loc": list(QUESTIONS_LOCATION),
                "msg": f"{_value_error_prefix}{_provider_message(reported)}",
                "type": _value_error_type,
            }
        ]
    )


def _is_official_validation_envelope(body: Mapping[str, Any]) -> bool:
    try:
        _HTTPValidationError.model_validate(body)
    except ValidationError:
        return False
    return True


def _as_json_object(body: object) -> Mapping[str, Any] | None:
    """Narrow a decoded provider body to a JSON object, or None when it is not one."""

    if isinstance(body, Mapping):
        return cast(Mapping[str, Any], body)
    return None


def _provider_message(reported: Mapping[str, Any] | None) -> str:
    if reported is None:
        return _provider_rejected_request_message
    detail = reported.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    return _provider_rejected_request_message


def _validation_detail_entry(
    error: Mapping[str, Any],
    location_prefix: tuple[str, ...],
) -> dict[str, Any]:
    entry: dict[str, Any] = {"loc": [*location_prefix, *error["loc"]]}
    for field in _detail_fields:
        if field != "loc" and field in error:
            entry[field] = error[field]
    return entry
