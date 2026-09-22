"""Enforces the project's primary constraint: core never writes user data.

This walks the AST of every module in ftv.core (except index.py, which owns the
database) looking for write-capable filesystem calls and for any open() whose
mode is not exactly "rb". It runs on every test invocation, so the invariant
cannot quietly rot as the code grows.
"""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "ftv" / "core"

#: index.py owns the index database and is the one module allowed to write.
EXEMPT = {"index.py"}

FORBIDDEN_NAMES = frozenset(
    {
        "chmod",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "link",
        "lchmod",
        "makedirs",
        "mkdir",
        "mkfifo",
        "move",
        "remove",
        "removedirs",
        "rename",
        "renames",
        "replace",
        "rmdir",
        "rmtree",
        "symlink",
        "touch",
        "truncate",
        "unlink",
        "utime",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
    }
)


def core_modules() -> list[Path]:
    modules = [p for p in sorted(CORE.glob("*.py")) if p.name not in EXEMPT]
    assert modules, f"no core modules found under {CORE}"
    return modules


def test_core_contains_no_write_capable_calls():
    offenders = []
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}:{node.lineno}: .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}:{node.lineno}: {node.id}")
    assert offenders == [], (
        "write-capable filesystem calls found in the read-only core: " + "; ".join(offenders)
    )


def test_core_never_imports_shutil():
    offenders = []
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}:{node.lineno}" for a in node.names if a.name == "shutil"
                ]
            elif isinstance(node, ast.ImportFrom) and node.module == "shutil":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], "shutil imported in read-only core: " + "; ".join(offenders)


def _open_mode(node: ast.Call) -> object:
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def test_core_opens_files_read_binary_only():
    offenders = []
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_open = (isinstance(func, ast.Name) and func.id == "open") or (
                isinstance(func, ast.Attribute) and func.attr == "open"
            )
            if is_open and _open_mode(node) != "rb":
                offenders.append(f"{path.name}:{node.lineno}: open(mode={_open_mode(node)!r})")
    assert offenders == [], "non-read-binary open() in core: " + "; ".join(offenders)


def test_only_fsread_performs_directory_walks():
    offenders = []
    for path in core_modules():
        if path.name == "fsread.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {
                "walk",
                "scandir",
                "iterdir",
                "rglob",
                "glob",
                "stat",
                "is_dir",
                "is_file",
                "exists",
                "lstat",
            }:
                offenders.append(f"{path.name}:{node.lineno}: .{node.attr}")
    assert offenders == [], "directory traversal outside fsread.py: " + "; ".join(offenders)
