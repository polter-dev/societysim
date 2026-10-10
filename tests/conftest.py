"""Shared pytest hooks. Tests marked `live` call paid APIs and are opt-in."""

import os

import pytest

pytest_plugins = ["pytester"]

LIVE_ENV = "SOCIETYSIM_LIVE"


def pytest_collection_modifyitems(config, items):
    if os.environ.get(LIVE_ENV) == "1":
        return
    skip = pytest.mark.skip(reason=f"live test: set {LIVE_ENV}=1 to run (spends real money)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
