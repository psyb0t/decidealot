"""Model aliases select only the expected local native provider."""

from typing import cast

import pytest

from decidealot.constants import LAYA_PROVIDER_NAME, VON_PROVIDER_NAME
from decidealot.errors import UnknownModelError
from decidealot.providers import ModelRouter
from decidealot.settings import ModelName


@pytest.mark.parametrize(
    ("default_model", "requested_model", "expected_provider", "expected_upstream_model"),
    [
        (LAYA_PROVIDER_NAME, "jev-latest", LAYA_PROVIDER_NAME, ""),
        (VON_PROVIDER_NAME, "jev-1", VON_PROVIDER_NAME, "von-1.1"),
        (LAYA_PROVIDER_NAME, "laya", LAYA_PROVIDER_NAME, ""),
        (LAYA_PROVIDER_NAME, "laya-auto", LAYA_PROVIDER_NAME, ""),
        (LAYA_PROVIDER_NAME, "laya-english", LAYA_PROVIDER_NAME, "english"),
        (LAYA_PROVIDER_NAME, "laya-multilingual", LAYA_PROVIDER_NAME, "multilingual"),
        (LAYA_PROVIDER_NAME, "laya-typed-decisions", LAYA_PROVIDER_NAME, "typed-decisions"),
        (LAYA_PROVIDER_NAME, "von", VON_PROVIDER_NAME, "von-1.1"),
        (LAYA_PROVIDER_NAME, "von-1.1.0", VON_PROVIDER_NAME, "von-1.1"),
    ],
)
def test_model_router_resolves_documented_aliases(
    default_model: ModelName,
    requested_model: str,
    expected_provider: str,
    expected_upstream_model: str,
) -> None:
    router = ModelRouter(default_model)

    route = router.resolve(requested_model)

    assert route.provider_name == expected_provider
    assert route.upstream_model == expected_upstream_model


@pytest.mark.parametrize(
    ("requested_model", "expected_public_model"),
    [
        ("laya", "laya"),
        ("laya-latest", "laya"),
        ("laya-english", "laya-english"),
        ("von", "von-1.1"),
        ("von-latest", "von-1.1"),
        ("jev-latest", "laya"),
    ],
)
def test_model_router_reports_the_public_name_of_the_serving_model(
    requested_model: str,
    expected_public_model: str,
) -> None:
    router = ModelRouter(cast(ModelName, LAYA_PROVIDER_NAME))

    assert router.resolve(requested_model).public_model == expected_public_model


@pytest.mark.parametrize(
    "requested_model", ["", "remote-url", "https://example.test", "english", "LAYA"]
)
def test_model_router_rejects_non_local_or_malformed_selectors(requested_model: str) -> None:
    router = ModelRouter(cast(ModelName, LAYA_PROVIDER_NAME))

    with pytest.raises(UnknownModelError):
        router.resolve(requested_model)


def test_every_catalog_name_resolves_to_a_local_provider() -> None:
    router = ModelRouter(cast(ModelName, LAYA_PROVIDER_NAME))

    providers = {name: router.resolve(name).provider_name for name in router.supported_models}

    assert set(providers.values()) == {LAYA_PROVIDER_NAME, VON_PROVIDER_NAME}
