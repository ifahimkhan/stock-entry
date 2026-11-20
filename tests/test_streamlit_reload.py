"""streamlit_app._drop_stale_package: after a redeploy, the old stock_entry modules are forgotten."""
from __future__ import annotations

import ast
import hashlib
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def load_drop_stale_package():
    """The function alone: importing streamlit_app would start the whole Streamlit page."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    [func] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_drop_stale_package"]
    namespace = {"hashlib": hashlib, "sys": sys, "PACKAGE_DIR": APP.parent / "stock_entry"}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(APP), "exec"), namespace)
    return namespace["_drop_stale_package"]


@pytest.fixture
def fake_package(monkeypatch: pytest.MonkeyPatch):
    """Stand-in stock_entry modules in sys.modules (the real ones are put back afterwards)."""
    for name in [n for n in sys.modules if n == "stock_entry" or n.startswith("stock_entry.")]:
        monkeypatch.delitem(sys.modules, name)
    package = types.ModuleType("stock_entry")
    monkeypatch.setitem(sys.modules, "stock_entry", package)
    monkeypatch.setitem(sys.modules, "stock_entry.bridge", types.ModuleType("stock_entry.bridge"))
    monkeypatch.setitem(sys.modules, "stock_entry_other", types.ModuleType("stock_entry_other"))
    return package


def test_modules_loaded_from_older_code_are_dropped(fake_package: types.ModuleType) -> None:
    fake_package._loaded_fingerprint = "old"
    fingerprint = load_drop_stale_package()()
    assert len(fingerprint) == 12
    assert "stock_entry" not in sys.modules and "stock_entry.bridge" not in sys.modules
    assert "stock_entry_other" in sys.modules   # only the package and its submodules


def test_modules_of_the_current_code_are_kept(fake_package: types.ModuleType) -> None:
    drop = load_drop_stale_package()
    fake_package._loaded_fingerprint = drop()   # first call drops (no fingerprint yet): put it back
    sys.modules["stock_entry"] = fake_package
    sys.modules["stock_entry.bridge"] = types.ModuleType("stock_entry.bridge")
    assert drop() == fake_package._loaded_fingerprint
    assert sys.modules["stock_entry"] is fake_package and "stock_entry.bridge" in sys.modules
