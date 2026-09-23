"""The process supervisor must start, route to, and stop owned providers."""

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from decidealot.errors import ProviderBusyError, ProviderUnavailableError
from decidealot.providers import HTTPProviderClient, ProviderResponse
from decidealot.settings import Settings
from decidealot.supervisor import ProviderSpec, ProviderSupervisor

_loopback_host = "127.0.0.1"
_request_id = "1d3fb045-4d61-4ecc-b169-cf012a10ea57"
_first_provider_name = "fixture-one"
_second_provider_name = "fixture-two"
_fake_provider_source = """
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        request = json.loads(self.rfile.read(content_length))
        body = json.dumps(
            {
                'model': request['model'],
                'answers': {'result': {'value': 'allow', 'probability': 0.99}},
            }
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return

port = int(__import__('os').environ['FIXTURE_PROVIDER_PORT'])
ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
"""


def _provider_spec(provider_name: str, port: int) -> ProviderSpec:
    return ProviderSpec(
        name=provider_name,
        health_url=f"http://{_loopback_host}:{port}/health",
        command=(sys.executable, "-c", _fake_provider_source),
        environment={"FIXTURE_PROVIDER_PORT": str(port)},
    )


@pytest_asyncio.fixture
async def running_supervisor(
    tmp_path: Path, unused_tcp_port: int
) -> AsyncIterator[tuple[ProviderSupervisor, str]]:
    endpoint = f"http://{_loopback_host}:{unused_tcp_port}/v1/systemone"
    settings = Settings(
        model_data_dir=tmp_path / "models",
        provider_start_timeout_seconds=3,
    )
    supervisor = ProviderSupervisor(
        settings,
        specs=(_provider_spec(_first_provider_name, unused_tcp_port),),
    )
    await supervisor.start()
    try:
        yield supervisor, endpoint
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_starts_no_provider_until_the_selected_provider_is_acquired(
    running_supervisor: tuple[ProviderSupervisor, str],
) -> None:
    supervisor, endpoint = running_supervisor

    async with httpx.AsyncClient(trust_env=False) as client:
        provider = HTTPProviderClient(_first_provider_name, endpoint, client)
        unloaded_before_use = await supervisor.unload_provider(_first_provider_name)
        async with supervisor.acquire(_first_provider_name):
            response = await provider.forward({"model": "fixture", "questions": {}}, _request_id)
        unloaded_after_use = await supervisor.unload_provider(_first_provider_name)

    assert supervisor.ready
    assert not unloaded_before_use.was_loaded
    assert response.status_code == 200
    assert unloaded_after_use.was_loaded
    assert response.body == {
        "model": "fixture",
        "answers": {"result": {"value": "allow", "probability": 0.99}},
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_unload_releases_process_and_next_use_reloads_it(
    running_supervisor: tuple[ProviderSupervisor, str],
) -> None:
    supervisor, endpoint = running_supervisor

    async with httpx.AsyncClient(trust_env=False) as client:
        provider = HTTPProviderClient(_first_provider_name, endpoint, client)
        async with supervisor.acquire(_first_provider_name):
            await provider.forward({"model": "fixture", "questions": {}}, _request_id)
        unload_result = await supervisor.unload_provider(_first_provider_name)

        with pytest.raises(ProviderUnavailableError):
            await provider.forward({"model": "fixture", "questions": {}}, _request_id)

        async with supervisor.acquire(_first_provider_name):
            response = await provider.forward({"model": "fixture", "questions": {}}, _request_id)

    assert unload_result.was_loaded
    assert supervisor.ready
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_does_not_unload_an_active_provider(
    running_supervisor: tuple[ProviderSupervisor, str],
) -> None:
    supervisor, endpoint = running_supervisor

    async with httpx.AsyncClient(trust_env=False) as client:
        provider = HTTPProviderClient(_first_provider_name, endpoint, client)
        async with supervisor.acquire(_first_provider_name):
            with pytest.raises(ProviderBusyError):
                await supervisor.unload_provider(_first_provider_name)
            response = await provider.forward({"model": "fixture", "questions": {}}, _request_id)

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_automatically_unloads_idle_provider_and_reloads_it(
    tmp_path: Path, unused_tcp_port: int
) -> None:
    endpoint = f"http://{_loopback_host}:{unused_tcp_port}/v1/systemone"
    settings = Settings(
        model_data_dir=tmp_path / "models",
        provider_idle_unload_seconds=0.1,
        provider_start_timeout_seconds=3,
    )
    supervisor = ProviderSupervisor(
        settings,
        specs=(
            ProviderSpec(
                name=_first_provider_name,
                health_url=f"http://{_loopback_host}:{unused_tcp_port}/health",
                command=(sys.executable, "-c", _fake_provider_source),
                environment={"FIXTURE_PROVIDER_PORT": str(unused_tcp_port)},
            ),
        ),
    )
    await supervisor.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            provider = HTTPProviderClient(_first_provider_name, endpoint, client)
            async with supervisor.acquire(_first_provider_name):
                await provider.forward({"model": "fixture", "questions": {}}, _request_id)
            await asyncio.sleep(0.25)
            with pytest.raises(ProviderUnavailableError):
                await provider.forward({"model": "fixture", "questions": {}}, _request_id)

            async with supervisor.acquire(_first_provider_name):
                response = await provider.forward(
                    {"model": "fixture", "questions": {}}, _request_id
                )
    finally:
        await supervisor.stop()

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_does_not_automatically_unload_an_active_provider(
    tmp_path: Path, unused_tcp_port: int
) -> None:
    endpoint = f"http://{_loopback_host}:{unused_tcp_port}/v1/systemone"
    settings = Settings(
        model_data_dir=tmp_path / "models",
        provider_idle_unload_seconds=0.1,
        provider_start_timeout_seconds=3,
    )
    supervisor = ProviderSupervisor(
        settings,
        specs=(
            ProviderSpec(
                name=_first_provider_name,
                health_url=f"http://{_loopback_host}:{unused_tcp_port}/health",
                command=(sys.executable, "-c", _fake_provider_source),
                environment={"FIXTURE_PROVIDER_PORT": str(unused_tcp_port)},
            ),
        ),
    )
    await supervisor.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            provider = HTTPProviderClient(_first_provider_name, endpoint, client)
            async with supervisor.acquire(_first_provider_name):
                await asyncio.sleep(0.25)
                response = await provider.forward(
                    {"model": "fixture", "questions": {}}, _request_id
                )
    finally:
        await supervisor.stop()

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_replaces_resident_provider_for_the_next_model(
    tmp_path: Path, unused_tcp_port_factory: Callable[[], int]
) -> None:
    first_port = unused_tcp_port_factory()
    second_port = unused_tcp_port_factory()
    first_endpoint = f"http://{_loopback_host}:{first_port}/v1/systemone"
    second_endpoint = f"http://{_loopback_host}:{second_port}/v1/systemone"
    supervisor = ProviderSupervisor(
        Settings(
            model_data_dir=tmp_path / "models",
            provider_idle_unload_seconds=0,
            provider_start_timeout_seconds=3,
        ),
        specs=(
            _provider_spec(_first_provider_name, first_port),
            _provider_spec(_second_provider_name, second_port),
        ),
    )
    await supervisor.start()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            first_provider = HTTPProviderClient(_first_provider_name, first_endpoint, client)
            second_provider = HTTPProviderClient(_second_provider_name, second_endpoint, client)
            async with supervisor.acquire(_first_provider_name):
                first_response = await first_provider.forward(
                    {"model": "first", "questions": {}}, _request_id
                )
            async with supervisor.acquire(_second_provider_name):
                second_response = await second_provider.forward(
                    {"model": "second", "questions": {}}, _request_id
                )
            with pytest.raises(ProviderUnavailableError):
                await first_provider.forward({"model": "first", "questions": {}}, _request_id)
            first_unload = await supervisor.unload_provider(_first_provider_name)
            second_unload = await supervisor.unload_provider(_second_provider_name)
    finally:
        await supervisor.stop()

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert not first_unload.was_loaded
    assert second_unload.was_loaded


@pytest.mark.asyncio
@pytest.mark.integration
async def test_supervisor_waits_for_an_active_provider_before_switching_models(
    tmp_path: Path, unused_tcp_port_factory: Callable[[], int]
) -> None:
    first_port = unused_tcp_port_factory()
    second_port = unused_tcp_port_factory()
    second_endpoint = f"http://{_loopback_host}:{second_port}/v1/systemone"
    supervisor = ProviderSupervisor(
        Settings(model_data_dir=tmp_path / "models", provider_start_timeout_seconds=3),
        specs=(
            _provider_spec(_first_provider_name, first_port),
            _provider_spec(_second_provider_name, second_port),
        ),
    )
    await supervisor.start()
    switch_started = asyncio.Event()
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            second_provider = HTTPProviderClient(_second_provider_name, second_endpoint, client)

            async def request_second_provider() -> ProviderResponse:
                switch_started.set()
                async with supervisor.acquire(_second_provider_name):
                    return await second_provider.forward(
                        {"model": "second", "questions": {}}, _request_id
                    )

            async with supervisor.acquire(_first_provider_name):
                switch_task = asyncio.create_task(request_second_provider())
                await switch_started.wait()
                await asyncio.sleep(0.1)
                assert not switch_task.done()
            second_response = await switch_task
    finally:
        await supervisor.stop()

    assert second_response.status_code == 200
