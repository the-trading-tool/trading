"""Every PushoverNotifier call site must match the real constructor/method signature.

The callers wrap the push in ``try/except Exception`` and only log a warning, so a
wrong keyword (``username=``) or a missing method (``send``) fails silently at runtime.
"""
import ast
import inspect
import pathlib

from tradinglib.pushover_notifier import PushoverNotifier

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKIP = {".venv", ".claude", ".git", "tests", "liveticker-pi", "__pycache__"}


def _sources():
    for p in ROOT.rglob("*.py"):
        if _SKIP.intersection(p.relative_to(ROOT).parts):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if "PushoverNotifier" in text:
            yield p, ast.parse(text)


def _name(func):
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def test_constructor_keywords_exist():
    allowed = set(inspect.signature(PushoverNotifier.__init__).parameters) - {"self"}
    bad = []
    for p, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _name(node.func) == "PushoverNotifier":
                bad += [f"{p.name}:{node.lineno} {k.arg}" for k in node.keywords if k.arg not in allowed]
    assert not bad, bad


def test_notifier_methods_exist():
    """``<x>notifier.<method>(...)`` must call a method the class actually has."""
    methods = {m for m in dir(PushoverNotifier) if not m.startswith("__")}
    bad = []
    for p, tree in _sources():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id.endswith("notifier")
                    and node.func.attr not in methods):
                bad.append(f"{p.name}:{node.lineno} .{node.func.attr}")
    assert not bad, bad
