"""Own, check, and stop only Decidealot's fixed local model subprocesses."""

import asyncio
import logging
import os

# Needed to own the fixed local provider commands. None run through a shell or come
# from a request.
import subprocess  # nosec B404
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from decidealot.constants import (
    LAYA_HEALTH_URL,
    LAYA_PORT,
    LAYA_PROVIDER_NAME,
    LAYA_VENV_PYTHON,
    LOCAL_PROVIDER_ENTRYPOINT,
    MAX_IDLE_REAPER_INTERVAL_SECONDS,
    MIN_IDLE_REAPER_INTERVAL_SECONDS,
    MODEL_DATA_DIRECTORY,
    PROCESS_STOP_TIMEOUT_SECONDS,
    PROVIDER_ENTRYPOINT_PREPARE_ACTION,
    VON_BACKEND,
    VON_HEALTH_URL,
    VON_PORT,
    VON_PROVIDER_NAME,
    VON_VENV_PYTHON,
)
from decidealot.errors import ProviderBusyError, ProviderUnavailableError
from decidealot.settings import Settings

logger = logging.getLogger(__name__)
_loopback_address = "127.0.0.1"
_health_poll_seconds = 0.25


@dataclass(frozen=True)
class ProviderSpec:
    """An immutable process specification for one native model server."""

    name: str
    health_url: str
    command: tuple[str, ...]
    environment: Mapping[str, str]
    prepare_command: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ProviderUnloadResult:
    """The observable outcome of an idempotent provider unload operation."""

    provider_name: str
    was_loaded: bool


class ProviderSupervisor:
    """Lifecycle owner for child processes spawned by this service instance."""

    def __init__(self, settings: Settings, specs: Sequence[ProviderSpec] | None = None) -> None:
        self._settings = settings
        self._specs = tuple(specs) if specs is not None else self._default_specs()
        self._specs_by_name = {spec.name: spec for spec in self._specs}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._active_requests = {spec.name: 0 for spec in self._specs}
        self._last_used_at: dict[str, float] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._provider_switch_condition = asyncio.Condition(self._lifecycle_lock)
        self._startup_lock = asyncio.Lock()
        self._idle_reaper_task: asyncio.Task[None] | None = None
        self._started = False

    @property
    def ready(self) -> bool:
        return self._started

    @property
    def provider_specs(self) -> tuple[ProviderSpec, ...]:
        """Expose the immutable configured commands for diagnostics and tests."""

        return self._specs

    async def start(self) -> None:
        """Download missing bundles before declaring lazy provider lifecycle ready."""

        async with self._startup_lock:
            async with self._provider_switch_condition:
                if self._started:
                    return
            await self._prepare_provider_bundles()
            async with self._provider_switch_condition:
                self._started = True
                self._start_idle_reaper_locked()
        logger.info(
            "local provider lifecycle ready", extra={"providers": list(self._specs_by_name)}
        )

    async def stop(self) -> None:
        """Stop only the processes created by this supervisor."""

        async with self._provider_switch_condition:
            self._started = False
            idle_reaper_task = self._idle_reaper_task
            self._idle_reaper_task = None
            self._provider_switch_condition.notify_all()
        if idle_reaper_task is not None:
            idle_reaper_task.cancel()
            try:
                await idle_reaper_task
            except asyncio.CancelledError:
                pass
        async with self._provider_switch_condition:
            for provider_name in tuple(self._processes):
                await self._stop_provider_locked(provider_name, reason="service_shutdown")

    @asynccontextmanager
    async def acquire(self, provider_name: str) -> AsyncGenerator[None, None]:
        """Keep one provider live until its caller has finished using it."""

        async with self._provider_switch_condition:
            if not self._started:
                raise ProviderUnavailableError("local providers are not ready")
            spec = self._require_spec(provider_name)
            while self._has_active_other_provider(provider_name):
                await self._provider_switch_condition.wait()
                if not self._started:
                    raise ProviderUnavailableError("local providers are not ready")
            if not self._started:
                raise ProviderUnavailableError("local providers are not ready")
            await self._unload_other_idle_providers_locked(provider_name)
            await self._start_provider_locked(spec)
            self._active_requests[provider_name] += 1
        try:
            yield
        finally:
            async with self._provider_switch_condition:
                self._active_requests[provider_name] -= 1
                self._last_used_at[provider_name] = asyncio.get_running_loop().time()
                self._provider_switch_condition.notify_all()

    async def unload_provider(self, provider_name: str) -> ProviderUnloadResult:
        """Stop one idle provider, releasing its full Torch runtime."""

        async with self._provider_switch_condition:
            self._require_spec(provider_name)
            if self._active_requests[provider_name] > 0:
                raise ProviderBusyError("the selected local provider is processing a request")
            was_loaded = provider_name in self._processes
            if was_loaded:
                await self._stop_provider_locked(provider_name, reason="explicit_unload")
            return ProviderUnloadResult(provider_name=provider_name, was_loaded=was_loaded)

    async def unload_all(self) -> tuple[ProviderUnloadResult, ...]:
        """Stop every idle provider, or reject the whole request while one is busy."""

        async with self._provider_switch_condition:
            busy_providers = [
                provider_name
                for provider_name, active_requests in self._active_requests.items()
                if active_requests > 0
            ]
            if busy_providers:
                raise ProviderBusyError("a local provider is processing a request")
            results: list[ProviderUnloadResult] = []
            for spec in self._specs:
                was_loaded = spec.name in self._processes
                if was_loaded:
                    await self._stop_provider_locked(spec.name, reason="explicit_unload_all")
                results.append(ProviderUnloadResult(provider_name=spec.name, was_loaded=was_loaded))
            return tuple(results)

    async def _unload_idle_providers(self) -> tuple[ProviderUnloadResult, ...]:
        if self._settings.provider_idle_unload_seconds == 0:
            return ()
        now = asyncio.get_running_loop().time()
        async with self._provider_switch_condition:
            if not self._started:
                return ()
            results: list[ProviderUnloadResult] = []
            for spec in self._specs:
                provider_name = spec.name
                last_used_at = self._last_used_at.get(provider_name, now)
                is_idle = now - last_used_at >= self._settings.provider_idle_unload_seconds
                if (
                    provider_name in self._processes
                    and self._active_requests[provider_name] == 0
                    and is_idle
                ):
                    await self._stop_provider_locked(provider_name, reason="idle_timeout")
                    results.append(
                        ProviderUnloadResult(provider_name=provider_name, was_loaded=True)
                    )
            return tuple(results)

    async def _idle_reaper(self) -> None:
        interval = min(
            max(
                self._settings.provider_idle_unload_seconds / 10,
                MIN_IDLE_REAPER_INTERVAL_SECONDS,
            ),
            MAX_IDLE_REAPER_INTERVAL_SECONDS,
        )
        while self._started:
            await asyncio.sleep(interval)
            results = await self._unload_idle_providers()
            if results:
                logger.info(
                    "idle local providers unloaded",
                    extra={"providers": [result.provider_name for result in results]},
                )

    def _start_idle_reaper_locked(self) -> None:
        if self._settings.provider_idle_unload_seconds == 0:
            return
        self._idle_reaper_task = asyncio.create_task(
            self._idle_reaper(), name="decidealot-provider-idle-reaper"
        )

    def _has_active_other_provider(self, selected_provider_name: str) -> bool:
        return any(
            provider_name != selected_provider_name and active_requests > 0
            for provider_name, active_requests in self._active_requests.items()
        )

    async def _unload_other_idle_providers_locked(self, selected_provider_name: str) -> None:
        for provider_name in tuple(self._processes):
            if provider_name == selected_provider_name:
                continue
            if self._active_requests[provider_name] > 0:
                raise RuntimeError("cannot replace an active local provider")
            await self._stop_provider_locked(provider_name, reason="model_switch")

    async def _start_provider_locked(self, spec: ProviderSpec) -> None:
        process = self._processes.get(spec.name)
        if process is not None and process.poll() is None:
            return
        if process is not None:
            self._processes.pop(spec.name)
        self._start_process(spec)
        await self._wait_for_health(spec)
        self._last_used_at[spec.name] = asyncio.get_running_loop().time()

    async def _stop_provider_locked(self, provider_name: str, reason: str) -> None:
        process = self._processes.pop(provider_name, None)
        if process is None:
            return
        if process.poll() is not None:
            return
        logger.info(
            "stopping local provider and releasing Torch runtime",
            extra={"provider": provider_name, "pid": process.pid, "reason": reason},
        )
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "local provider did not stop after terminate",
                extra={"provider": provider_name, "pid": process.pid},
            )
            process.kill()
            await asyncio.to_thread(process.wait)

    def _require_spec(self, provider_name: str) -> ProviderSpec:
        spec = self._specs_by_name.get(provider_name)
        if spec is None:
            raise ProviderUnavailableError("the selected local provider is not configured")
        return spec

    async def _prepare_provider_bundles(self) -> None:
        for spec in self._specs:
            if spec.prepare_command is None:
                continue
            environment = {**os.environ, **spec.environment}
            logger.info("preparing local provider bundle", extra={"provider": spec.name})
            try:
                process = await asyncio.create_subprocess_exec(
                    *spec.prepare_command,
                    env=environment,
                    close_fds=True,
                )
            except OSError as error:
                logger.error(
                    "local provider preparation failed to start",
                    extra={"provider": spec.name, "error": str(error)},
                )
                raise ProviderUnavailableError(f"prepare {spec.name} provider bundle") from error
            return_code = await process.wait()
            if return_code != 0:
                logger.error(
                    "local provider bundle preparation failed",
                    extra={"provider": spec.name, "return_code": return_code},
                )
                raise ProviderUnavailableError(f"prepare {spec.name} provider bundle")
            logger.info("local provider bundle prepared", extra={"provider": spec.name})

    def _start_process(self, spec: ProviderSpec) -> None:
        environment = {**os.environ, **spec.environment}
        try:
            # ProviderSpec commands are fixed by this service and execute without a shell.
            process = subprocess.Popen(  # noqa: S603  # nosec B603
                spec.command,
                env=environment,
                close_fds=True,
            )
        except OSError as error:
            logger.error(
                "local provider failed to start", extra={"provider": spec.name, "error": str(error)}
            )
            raise ProviderUnavailableError(f"start {spec.name} provider") from error
        self._processes[spec.name] = process
        logger.info("local provider started", extra={"provider": spec.name, "pid": process.pid})

    async def _wait_for_health(self, spec: ProviderSpec) -> None:
        deadline = asyncio.get_running_loop().time() + self._settings.provider_start_timeout_seconds
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_health_poll_seconds), trust_env=False
        ) as client:
            while asyncio.get_running_loop().time() < deadline:
                process = self._processes[spec.name]
                if process.poll() is not None:
                    raise ProviderUnavailableError(
                        f"{spec.name} provider stopped before becoming ready"
                    )
                try:
                    response = await client.get(spec.health_url)
                except httpx.HTTPError:
                    await asyncio.sleep(_health_poll_seconds)
                    continue
                if response.is_success:
                    logger.debug(
                        "local provider health check succeeded", extra={"provider": spec.name}
                    )
                    return
                await asyncio.sleep(_health_poll_seconds)
        raise ProviderUnavailableError(
            f"{spec.name} provider did not become ready before the timeout"
        )

    def _default_specs(self) -> tuple[ProviderSpec, ...]:
        laya_data_dir = MODEL_DATA_DIRECTORY / LAYA_PROVIDER_NAME
        von_data_dir = MODEL_DATA_DIRECTORY / VON_PROVIDER_NAME
        common_environment = {"LOG_SCOPE_SERVICE": "decidealot"}
        laya_environment = {
            **common_environment,
            "HF_HOME": str(laya_data_dir),
            "LAYA_HOST": _loopback_address,
            "LAYA_PORT": str(LAYA_PORT),
            "LAYA_DEVICE": self._settings.device,
            "LAYA_LOG_LEVEL": self._settings.log_level.lower(),
        }
        von_environment = {
            **common_environment,
            "HF_HOME": str(von_data_dir),
            "VON_BACKEND": VON_BACKEND,
            "VON_DEVICE": self._settings.device,
            "VON_HOST": _loopback_address,
            "VON_PORT": str(VON_PORT),
        }
        return (
            ProviderSpec(
                name=LAYA_PROVIDER_NAME,
                health_url=LAYA_HEALTH_URL,
                command=(
                    LAYA_VENV_PYTHON,
                    LOCAL_PROVIDER_ENTRYPOINT,
                    LAYA_PROVIDER_NAME,
                ),
                environment=laya_environment,
                prepare_command=(
                    LAYA_VENV_PYTHON,
                    LOCAL_PROVIDER_ENTRYPOINT,
                    PROVIDER_ENTRYPOINT_PREPARE_ACTION,
                    LAYA_PROVIDER_NAME,
                ),
            ),
            ProviderSpec(
                name=VON_PROVIDER_NAME,
                health_url=VON_HEALTH_URL,
                command=(
                    VON_VENV_PYTHON,
                    LOCAL_PROVIDER_ENTRYPOINT,
                    VON_PROVIDER_NAME,
                ),
                environment=von_environment,
                prepare_command=(
                    VON_VENV_PYTHON,
                    LOCAL_PROVIDER_ENTRYPOINT,
                    PROVIDER_ENTRYPOINT_PREPARE_ACTION,
                    VON_PROVIDER_NAME,
                ),
            ),
        )


class DisabledProviderSupervisor:
    """A ready lifecycle implementation used by router tests and embeddings."""

    ready = True

    async def start(self) -> None:
        """Do nothing because callers supplied their own provider boundary."""

    async def stop(self) -> None:
        """Do nothing because this instance owns no subprocesses."""

    @asynccontextmanager
    async def acquire(self, provider_name: str) -> AsyncGenerator[None, None]:
        """Allow embedded callers to use the provider lifecycle they already own."""

        del provider_name
        yield

    async def unload_provider(self, provider_name: str) -> ProviderUnloadResult:
        """Report an idempotent no-op because this instance owns no provider process."""

        return ProviderUnloadResult(provider_name=provider_name, was_loaded=False)

    async def unload_all(self) -> tuple[ProviderUnloadResult, ...]:
        """Report an idempotent no-op because this instance owns no provider process."""

        return ()
