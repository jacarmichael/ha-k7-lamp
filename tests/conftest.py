"""Load the integration's pure modules without importing Home Assistant."""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "k7_lamp"
pkg = types.ModuleType("k7_lamp")
pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("k7_lamp", pkg)
