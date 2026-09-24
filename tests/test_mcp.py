"""MCP callers must reach the same local decision service as REST callers."""

from typing import Any, cast

from fastapi.testclient import TestClient
from pydantic import SecretStr

from decidealot.app import create_embedded_app
from decidealot.constants import LAYA_PROVIDER_NAME, VON_PROVIDER_NAME
from decidealot.settings import Settings
from tests.conftest import (
    FakeProvider,
    LifecycleSupervisor,
    native_system_one_response,
    system_one_request,
)

_mcp_path = "/mcp"
_jsonrpc_version = "2.0"
_legacy_protocol_version = "2025-11-25"
_initialize_method = "initialize"
_initialized_method = "notifications/initialized"
_tools_list_method = "tools/list"
_tools_call_method = "tools/call"
_mcp_session_id_header = "Mcp-Session-Id"
_mcp_headers = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_client_info = {"name": "decidealot-contract-test", "version": "1"}
_expected_tool_names = {"system_one", "list_models", "unload_models"}
_operator_api_key = SecretStr("operator-secret")
_operator_authorization = {"Authorization": "Bearer operator-secret"}
_request_id = "1d3fb045-4d61-4ecc-b169-cf012a10ea57"


def test_mcp_streamable_http_lists_tools_and_runs_a_system_one_decision() -> None:
    providers = {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1")),
    }
    app = create_embedded_app(Settings(), providers, LifecycleSupervisor())

    with TestClient(app, base_url="http://127.0.0.1:8080", follow_redirects=False) as client:
        initialize_response = client.post(
            _mcp_path,
            headers=_mcp_headers,
            json=_mcp_message(
                _initialize_method,
                {
                    "protocolVersion": _legacy_protocol_version,
                    "capabilities": {},
                    "clientInfo": _client_info,
                },
                request_id=1,
            ),
        )

        assert initialize_response.status_code == 200
        assert initialize_response.is_redirect is False
        session_id = initialize_response.headers[_mcp_session_id_header]
        session_headers = {**_mcp_headers, _mcp_session_id_header: session_id}

        initialized_response = client.post(
            _mcp_path,
            headers=session_headers,
            json=_mcp_notification(_initialized_method),
        )
        assert initialized_response.status_code in {200, 202}

        tools_response = client.post(
            _mcp_path,
            headers=session_headers,
            json=_mcp_message(_tools_list_method, {}, request_id=2),
        )
        assert tools_response.status_code == 200
        tool_names = {
            tool["name"] for tool in cast(dict[str, Any], tools_response.json())["result"]["tools"]
        }
        assert tool_names == _expected_tool_names

        models_response = client.post(
            _mcp_path,
            headers=session_headers,
            json=_mcp_message(
                _tools_call_method,
                {"name": "list_models", "arguments": {}},
                request_id=3,
            ),
        )
        assert models_response.status_code == 200
        model_result = cast(dict[str, Any], models_response.json())["result"]
        assert {model["name"] for model in model_result["structuredContent"]["models"]} >= {
            "laya",
            "von",
        }

        decision_response = client.post(
            _mcp_path,
            headers={**session_headers, "X-Request-Id": _request_id},
            json=_mcp_message(
                _tools_call_method,
                {"name": "system_one", "arguments": system_one_request()},
                request_id=4,
            ),
        )

        unload_response = client.post(
            _mcp_path,
            headers=session_headers,
            json=_mcp_message(
                _tools_call_method,
                {"name": "unload_models", "arguments": {}},
                request_id=5,
            ),
        )

    assert decision_response.status_code == 200
    result = cast(dict[str, Any], decision_response.json())["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["model"] == "laya"
    assert result["structuredContent"]["answers"]["route"]["choice"] == "allow"
    assert unload_response.status_code == 200
    assert cast(dict[str, Any], unload_response.json())["result"]["structuredContent"] == {
        "status": "unloaded",
        "providers": [
            {"name": "laya", "wasLoaded": True},
            {"name": "von", "wasLoaded": False},
        ],
    }
    assert len(providers[LAYA_PROVIDER_NAME].calls) == 1
    assert providers[LAYA_PROVIDER_NAME].calls[0][1] == _request_id


def test_mcp_accepts_a_configured_proxy_host_and_rejects_other_hosts() -> None:
    providers = {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1")),
    }
    app = create_embedded_app(
        Settings(
            api_key=_operator_api_key,
            mcp_allowed_hosts="mcp.example.net,mcp.example.net:*",
            mcp_allowed_origins="https://mcp.example.net",
        ),
        providers,
        LifecycleSupervisor(),
    )

    with TestClient(app, base_url="http://127.0.0.1:8080", follow_redirects=False) as client:
        allowed_response = client.post(
            _mcp_path,
            headers={
                **_mcp_headers,
                **_operator_authorization,
                "Host": "mcp.example.net",
                "Origin": "https://mcp.example.net",
            },
            json=_initialize_message(),
        )
        allowed_session_headers = {
            **_mcp_headers,
            **_operator_authorization,
            "Host": "mcp.example.net",
            "Origin": "https://mcp.example.net",
            _mcp_session_id_header: allowed_response.headers[_mcp_session_id_header],
        }
        allowed_tools_response = client.post(
            _mcp_path,
            headers=allowed_session_headers,
            json=_mcp_message(_tools_list_method, {}, request_id=2),
        )
        wildcard_port_response = client.post(
            _mcp_path,
            headers={
                **_mcp_headers,
                **_operator_authorization,
                "Host": "mcp.example.net:443",
                "Origin": "https://mcp.example.net",
            },
            json=_initialize_message(),
        )
        rejected_response = client.post(
            _mcp_path,
            headers={
                **_mcp_headers,
                **_operator_authorization,
                "Host": "untrusted.example.net",
            },
            json=_initialize_message(),
        )
        rejected_origin_response = client.post(
            _mcp_path,
            headers={
                **_mcp_headers,
                **_operator_authorization,
                "Host": "mcp.example.net",
                "Origin": "https://untrusted.example.net",
            },
            json=_initialize_message(),
        )

    assert allowed_response.status_code == 200
    assert _mcp_session_id_header in allowed_response.headers
    assert allowed_tools_response.status_code == 200
    tool_names = {
        tool["name"]
        for tool in cast(dict[str, Any], allowed_tools_response.json())["result"]["tools"]
    }
    assert tool_names == _expected_tool_names
    assert wildcard_port_response.status_code == 200
    assert _mcp_session_id_header in wildcard_port_response.headers
    assert rejected_response.status_code == 421
    assert rejected_response.text == "Invalid Host header"
    assert rejected_origin_response.status_code == 403
    assert rejected_origin_response.text == "Invalid Origin header"
    assert providers[LAYA_PROVIDER_NAME].calls == []
    assert providers[VON_PROVIDER_NAME].calls == []


def test_mcp_accepts_the_default_docker_service_host() -> None:
    providers = {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1")),
    }
    app = create_embedded_app(Settings(), providers, LifecycleSupervisor())

    with TestClient(app, base_url="http://127.0.0.1:8080", follow_redirects=False) as client:
        response = client.post(
            _mcp_path,
            headers={**_mcp_headers, "Host": "decidealot:8080"},
            json=_initialize_message(),
        )

    assert response.status_code == 200
    assert _mcp_session_id_header in response.headers


def test_mcp_enforces_the_configured_bearer_key_before_creating_a_session() -> None:
    providers = {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1")),
    }
    app = create_embedded_app(
        Settings(api_key=_operator_api_key),
        providers,
        LifecycleSupervisor(),
    )

    with TestClient(app, base_url="http://127.0.0.1:8080", follow_redirects=False) as client:
        missing_key_response = client.post(
            _mcp_path,
            headers=_mcp_headers,
            json=_initialize_message(),
        )
        wrong_key_response = client.post(
            _mcp_path,
            headers={**_mcp_headers, "Authorization": "Bearer wrong-key"},
            json=_initialize_message(),
        )
        authenticated_response = client.post(
            _mcp_path,
            headers={**_mcp_headers, **_operator_authorization},
            json=_initialize_message(),
        )
        session_id = authenticated_response.headers[_mcp_session_id_header]
        unprotected_session_response = client.post(
            _mcp_path,
            headers={**_mcp_headers, _mcp_session_id_header: session_id},
            json=_mcp_message(_tools_list_method, {}, request_id=2),
        )

    assert missing_key_response.status_code == 401
    assert missing_key_response.headers["WWW-Authenticate"] == "Bearer"
    assert missing_key_response.json()["code"] == "UNAUTHORIZED"
    assert wrong_key_response.status_code == 401
    assert authenticated_response.status_code == 200
    assert unprotected_session_response.status_code == 401
    assert providers[LAYA_PROVIDER_NAME].calls == []
    assert providers[VON_PROVIDER_NAME].calls == []


def test_mcp_returns_a_typesafe_tool_error_without_calling_a_provider() -> None:
    providers = {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1")),
    }
    app = create_embedded_app(Settings(), providers, LifecycleSupervisor())

    with TestClient(app, base_url="http://127.0.0.1:8080", follow_redirects=False) as client:
        session_headers = _initialize_session(client)
        response = client.post(
            _mcp_path,
            headers=session_headers,
            json=_mcp_message(
                _tools_call_method,
                {
                    "name": "system_one",
                    "arguments": {
                        "model": "remote-model",
                        "state": "Review this action.",
                        "questions": system_one_request()["questions"],
                    },
                },
                request_id=2,
            ),
        )

    assert response.status_code == 200
    result = cast(dict[str, Any], response.json())["result"]
    assert result["isError"] is True
    validation_error = cast(dict[str, Any], result["content"][0])["text"]
    assert '"loc":["body","model"]' in validation_error
    assert providers[LAYA_PROVIDER_NAME].calls == []
    assert providers[VON_PROVIDER_NAME].calls == []


def test_mcp_rejects_an_oversized_request_before_provider_forwarding() -> None:
    providers = {
        LAYA_PROVIDER_NAME: FakeProvider(native_system_one_response("laya")),
        VON_PROVIDER_NAME: FakeProvider(native_system_one_response("von-1.1")),
    }
    app = create_embedded_app(Settings(max_request_bytes=1024), providers, LifecycleSupervisor())

    with TestClient(app, base_url="http://127.0.0.1:8080", follow_redirects=False) as client:
        response = client.post(
            _mcp_path,
            headers=_mcp_headers,
            content="x" * 1025,
        )

    assert response.status_code == 413
    assert providers[LAYA_PROVIDER_NAME].calls == []
    assert providers[VON_PROVIDER_NAME].calls == []


def _mcp_message(method: str, params: dict[str, object], request_id: int) -> dict[str, object]:
    return {
        "jsonrpc": _jsonrpc_version,
        "id": request_id,
        "method": method,
        "params": params,
    }


def _mcp_notification(method: str) -> dict[str, object]:
    return {"jsonrpc": _jsonrpc_version, "method": method}


def _initialize_message() -> dict[str, object]:
    return _mcp_message(
        _initialize_method,
        {
            "protocolVersion": _legacy_protocol_version,
            "capabilities": {},
            "clientInfo": _client_info,
        },
        request_id=1,
    )


def _initialize_session(client: TestClient) -> dict[str, str]:
    initialize_response = client.post(
        _mcp_path,
        headers=_mcp_headers,
        json=_initialize_message(),
    )
    assert initialize_response.status_code == 200
    session_id = initialize_response.headers[_mcp_session_id_header]
    session_headers = {**_mcp_headers, _mcp_session_id_header: session_id}
    initialized_response = client.post(
        _mcp_path,
        headers=session_headers,
        json=_mcp_notification(_initialized_method),
    )
    assert initialized_response.status_code in {200, 202}
    return session_headers
