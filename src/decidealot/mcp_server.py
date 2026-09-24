"""Version 2 MCP tools for local TypeSafe-compatible decisions."""

import json
from collections.abc import Awaitable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError

from decidealot import __version__
from decidealot.constants import MCP_SERVER_NAME, REQUEST_ID_HEADER
from decidealot.decisions import DecisionService
from decidealot.errors import DecidealotError, TypeSafeValidationError
from decidealot.security import request_id_from_header
from decidealot.typesafe import JSONValue, validation_error_content

_system_one_tool_name = "system_one"
_list_models_tool_name = "list_models"
_unload_models_tool_name = "unload_models"
_server_description = "Run local typed System One decisions through Decidealot."
_server_instructions = "Use system_one for a model, state, and named typed questions."


def create_mcp_server(decisions: DecisionService) -> MCPServer[object]:
    """Create the version 2 MCP tool surface over the shared decision service."""

    server = MCPServer(
        name=MCP_SERVER_NAME,
        title="Decidealot",
        description=_server_description,
        instructions=_server_instructions,
        version=__version__,
    )

    @server.tool(
        name=_system_one_tool_name,
        description="Run one local TypeSafe-compatible typed decision.",
        structured_output=True,
    )
    async def system_one(
        model: str,
        state: JSONValue,
        questions: dict[str, Any],
        context: Context[object, object],
    ) -> dict[str, Any]:
        """Run the selected local model against one state and named typed questions."""

        return await _run_tool(
            decisions.system_one(
                {"model": model, "state": state, "questions": questions},
                _request_id(context),
            )
        )

    @server.tool(
        name=_list_models_tool_name,
        description="List the local model aliases accepted by system_one.",
        structured_output=True,
    )
    async def list_models() -> dict[str, Any]:
        """List the same model catalog exposed by GET /v1/models."""

        return decisions.model_catalog()

    @server.tool(
        name=_unload_models_tool_name,
        description="Unload every idle local model and release its runtime memory.",
        structured_output=True,
    )
    async def unload_models() -> dict[str, Any]:
        """Unload the same providers exposed by POST /v1/models/unload."""

        return await _run_tool(decisions.unload_all())

    return server


async def _run_tool(operation: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        return await operation
    except TypeSafeValidationError as error:
        raise ToolError(_validation_message(error)) from error
    except DecidealotError as error:
        raise ToolError(str(error)) from error


def _request_id(context: Context[object, object]) -> str:
    headers = context.headers
    request_header = headers.get(REQUEST_ID_HEADER) if headers is not None else None
    return request_id_from_header(request_header)


def _validation_message(error: TypeSafeValidationError) -> str:
    return json.dumps(
        validation_error_content(error.detail), separators=(",", ":"), ensure_ascii=False
    )
