from __future__ import annotations

import shutil

import pytest

from dpcp import settings as settings_mod
from dpcp.descriptor import load_all
from dpcp.settings import ROOT

needs_opa = pytest.mark.skipif(shutil.which("opa") is None, reason="OPA not installed")


@pytest.fixture
def settings():
    return settings_mod.load()


@pytest.fixture
def products():
    return {d.id: d for d in load_all(ROOT / "products")}


@pytest.fixture
def wh(settings):
    from dpcp.warehouse import Warehouse, bootstrap

    w = Warehouse(settings)
    bootstrap(w)
    yield w
    w.close()
