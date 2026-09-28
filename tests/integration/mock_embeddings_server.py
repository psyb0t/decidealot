#!/usr/bin/env python3
"""Strict OpenAI-compatible embeddings double for the real CLM checkpoint test."""

import base64
import json
import os
import struct
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

_embedding_dimensions = 4096
_embeddings_path = "/v1/embeddings"
_application_json_content_type = "application/json"
_base64_encoding = "base64"
_authorization_header = "Authorization"
_bearer_prefix = "Bearer "
_host = "0.0.0.0"  # noqa: S104  # nosec B104: test-only server has no published port outside its owned network.
_port = 8081
_vector_values = tuple((index % 29 - 14) / 100.0 for index in range(_embedding_dimensions))
_vector = base64.b64encode(struct.pack(f"<{_embedding_dimensions}f", *_vector_values)).decode()


class EmbeddingsHandler(BaseHTTPRequestHandler):
    """Accept only the configured CLM wire contract and return indexed vectors."""

    server_version = "DecidealotCLMTestEmbeddings/1.0"

    def do_POST(self) -> None:  # noqa: N802
        if self.path != _embeddings_path:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if self.headers.get("Content-Type") != _application_json_content_type:
            self._respond(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content type"})
            return
        if self.headers.get(_authorization_header) != _expected_authorization():
            self._respond(HTTPStatus.UNAUTHORIZED, {"error": "authorization"})
            return
        request = self._request_body()
        if request is None:
            return
        inputs = _valid_inputs(request.get("input"))
        if (
            set(request) != {"model", "input", "encoding_format"}
            or request.get("model") != _expected_model()
            or request.get("encoding_format") != _base64_encoding
            or inputs is None
        ):
            self._respond(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": "request shape"})
            return
        self._respond(
            HTTPStatus.OK,
            {
                "data": [
                    {"object": "embedding", "index": index, "embedding": _vector}
                    for index, _ in enumerate(inputs)
                ],
                "model": _expected_model(),
                "object": "list",
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    def _request_body(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._respond(HTTPStatus.LENGTH_REQUIRED, {"error": "content length"})
            return None
        if content_length < 1:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "empty body"})
            return None
        try:
            parsed: object = json.loads(self.rfile.read(content_length))
        except json.JSONDecodeError:
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "json"})
            return None
        if not isinstance(parsed, dict):
            self._respond(HTTPStatus.BAD_REQUEST, {"error": "object"})
            return None
        return cast(dict[str, object], parsed)

    def _respond(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", _application_json_content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _valid_inputs(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    inputs: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item:
            return None
        inputs.append(item)
    return inputs


def _expected_model() -> str:
    return _required_environment("MOCK_EMBEDDINGS_MODEL")


def _expected_authorization() -> str:
    return f"{_bearer_prefix}{_required_environment('MOCK_EMBEDDINGS_API_KEY')}"


def main() -> None:
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((_host, _port), EmbeddingsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
