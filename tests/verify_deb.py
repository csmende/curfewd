#!/usr/bin/env python3
"""Packaging manifest, AST import audit, and isolated import verifier for curfewd .deb builds."""

from __future__ import annotations

import argparse
import ast
import glob
import subprocess
import tempfile
from pathlib import Path

REQUIRED_ENTRYPOINTS: tuple[str, ...] = (
    "curfewd",
    "curfewd.daemon",
    "curfewd.cli.main",
    "curfewd.tray.app",
    "curfewd.tray.osd",
    "curfewd.ui.app",
)

INSTALLED_CLI_BINARIES: tuple[str, ...] = (
    "/usr/bin/curfewd",
    "/usr/bin/curfewctl",
    "/usr/bin/curfewd-tray",
    "/usr/bin/curfewd-ui",
)

_ISOLATED_IMPORT_PROBE = r"""
import importlib
import os
import pathlib
import sys

mode = sys.argv[1]
repo_root = os.path.realpath(sys.argv[2])
expected_version = sys.argv[3]
entrypoints = sys.argv[4:]

if mode == "extracted":
    dist_root = os.path.realpath(sys.argv[4])
    entrypoints = sys.argv[5:]
    sys.path.insert(0, dist_root)
    expected_curfewd_dir = os.path.realpath(os.path.join(dist_root, "curfewd"))
else:
    expected_curfewd_dir = os.path.realpath("/usr/lib/python3/dist-packages/curfewd")

# Ensure neither CWD ("") nor the git checkout leaked onto sys.path
for entry in sys.path:
    if entry == "":
        raise SystemExit("Isolated import check failed: empty CWD entry found on sys.path")
    if os.path.realpath(entry) == repo_root:
        raise SystemExit(f"Isolated import check failed: repo root {repo_root} found on sys.path")

# Import all explicit critical entrypoints first
for mod_name in entrypoints:
    importlib.import_module(mod_name)

# Dynamically import every packaged module under expected_curfewd_dir (except __main__.py)
pkg_root = pathlib.Path(expected_curfewd_dir)
if not pkg_root.is_dir():
    raise SystemExit(f"Expected curfewd package directory not found: {pkg_root}")

discovered: list[str] = []
for py_file in sorted(pkg_root.rglob("*.py")):
    if py_file.name == "__main__.py":
        continue
    rel = py_file.relative_to(pkg_root.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    mod_name = ".".join(parts)
    importlib.import_module(mod_name)
    discovered.append(mod_name)

# Verify every loaded curfewd module resolved strictly inside expected_curfewd_dir
expected_prefix = expected_curfewd_dir + os.sep
for name, mod in sorted(sys.modules.items()):
    if name != "curfewd" and not name.startswith("curfewd."):
        continue
    mod_file = getattr(mod, "__file__", None)
    if not mod_file:
        raise SystemExit(f"Module {name} has no __file__ (namespace package leak?)")
    real_file = os.path.realpath(mod_file)
    if not real_file.startswith(expected_prefix):
        raise SystemExit(
            f"Module {name} imported from unexpected location {real_file} "
            f"(expected under {expected_prefix})"
        )

import curfewd

if expected_version and curfewd.__version__ != expected_version:
    raise SystemExit(
        f"Packaged curfewd.__version__ is {curfewd.__version__!r}, expected {expected_version!r}"
    )

print(
    f"Verified {len(discovered)} packaged curfewd modules from {expected_curfewd_dir} "
    f"(version={curfewd.__version__}, schema={curfewd.SCHEMA_VERSION})"
)
"""


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def parse_debian_install(repo_root: Path) -> dict[str, set[str]]:
    """Expand debian/install entries into a map of repo-relative source -> installed deb paths."""
    install_file = repo_root / "debian" / "install"
    mapping: dict[str, set[str]] = {}
    for lineno, raw_line in enumerate(
        install_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"debian/install:{lineno}: expected '<src> <dest>' pair, got {line!r}")
        dest_dir = _normalize_rel(parts[-1]).rstrip("/")
        for pattern in parts[:-1]:
            matches = sorted(glob.glob(str(repo_root / pattern)))
            if not matches:
                raise ValueError(f"debian/install:{lineno}: pattern {pattern!r} matched no files")
            for match_str in matches:
                match_path = Path(match_str)
                if not match_path.is_file():
                    raise ValueError(
                        f"debian/install:{lineno}: pattern {pattern!r} matched non-file {match_path}"
                    )
                src_rel = match_path.relative_to(repo_root).as_posix()
                dest_path = f"{dest_dir}/{match_path.name}"
                mapping.setdefault(src_rel, set()).add(dest_path)
    return mapping


def list_repo_files(repo_root: Path, prefixes: tuple[str, ...]) -> list[str]:
    """List tracked package files under prefixes via git ls-files (with filesystem fallback)."""
    if (repo_root / ".git").exists():
        res = subprocess.run(
            ["git", "ls-files", "--", *prefixes],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return sorted(line.strip() for line in res.stdout.splitlines() if line.strip())

    files: list[str] = []
    for prefix in prefixes:
        root = repo_root / prefix
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            files.append(path.relative_to(repo_root).as_posix())
    return sorted(files)


def expected_deb_targets(repo_root: Path) -> dict[str, set[str]]:
    """Return expected installed paths in the .deb for all tracked package files."""
    install_map = parse_debian_install(repo_root)
    expected: dict[str, set[str]] = {}

    for rel in list_repo_files(repo_root, ("curfewd", "bin", "extras", "resource")):
        if rel.startswith("curfewd/") and rel.endswith(".py"):
            expected[rel] = {f"usr/lib/python3/dist-packages/{rel}"}
        elif rel.startswith("curfewd/ui/web/"):
            sub = rel.removeprefix("curfewd/ui/web/")
            expected[rel] = {f"usr/share/curfewd/ui/{sub}"}
        elif rel.startswith("bin/"):
            expected[rel] = {f"usr/bin/{Path(rel).name}"}
        elif rel.startswith("extras/"):
            expected[rel] = {f"usr/share/doc/curfewd/examples/{Path(rel).name}"}
        else:
            # All resource/* files must at least be covered by debian/install
            expected[rel] = set(install_map.get(rel, set()))

    return expected


def _module_exists(dist_packages_root: Path, dotted_name: str) -> tuple[bool, Path | None]:
    parts = dotted_name.split(".")
    mod_file = dist_packages_root.joinpath(*parts).with_suffix(".py")
    if mod_file.is_file():
        return True, mod_file
    pkg_init = dist_packages_root.joinpath(*parts, "__init__.py")
    if pkg_init.is_file():
        return True, pkg_init
    return False, None


def _exported_names_in_module(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def audit_internal_imports(dist_packages_root: Path) -> list[str]:
    """AST-audit every internal curfewd.* import across packaged .py files."""
    pkg_dir = dist_packages_root / "curfewd"
    if not pkg_dir.is_dir():
        return [f"Missing package directory: {pkg_dir}"]

    errors: list[str] = []
    init_exports_cache: dict[Path, set[str]] = {}

    for py_file in sorted(pkg_dir.rglob("*.py")):
        rel = py_file.relative_to(dist_packages_root).with_suffix("")
        parts = list(rel.parts)
        current_pkg_parts = parts[:-1]
        source_label = py_file.relative_to(dist_packages_root).as_posix()

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=source_label)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "curfewd" or alias.name.startswith("curfewd."):
                        exists, _ = _module_exists(dist_packages_root, alias.name)
                        if not exists:
                            errors.append(
                                f"{source_label}:{node.lineno}: missing internal import {alias.name!r}"
                            )
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    drop = node.level - 1
                    if drop > len(current_pkg_parts):
                        errors.append(
                            f"{source_label}:{node.lineno}: invalid relative import level {node.level}"
                        )
                        continue
                    base_parts = current_pkg_parts[: len(current_pkg_parts) - drop]
                    if node.module:
                        base_parts = [*base_parts, *node.module.split(".")]
                    base_mod = ".".join(base_parts)
                else:
                    base_mod = node.module or ""

                if base_mod != "curfewd" and not base_mod.startswith("curfewd."):
                    continue

                exists, resolved = _module_exists(dist_packages_root, base_mod)
                if not exists or resolved is None:
                    errors.append(
                        f"{source_label}:{node.lineno}: missing internal module {base_mod!r}"
                    )
                    continue

                if resolved.name == "__init__.py":
                    exports = init_exports_cache.get(resolved)
                    if exports is None:
                        exports = _exported_names_in_module(resolved)
                        init_exports_cache[resolved] = exports
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        submod = f"{base_mod}.{alias.name}"
                        sub_exists, _ = _module_exists(dist_packages_root, submod)
                        if not sub_exists and alias.name not in exports:
                            errors.append(
                                f"{source_label}:{node.lineno}: missing submodule or symbol "
                                f"{alias.name!r} from package {base_mod!r}"
                            )
    return errors


def list_deb_entries(deb_path: Path) -> set[str]:
    """Return normalized file paths contained in a .deb archive via `dpkg-deb -c`."""
    res = subprocess.run(
        ["dpkg-deb", "-c", str(deb_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    entries: set[str] = set()
    for line in res.stdout.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        raw_path = parts[5].split(" -> ", 1)[0].strip()
        if raw_path.endswith("/"):
            continue
        entries.add(_normalize_rel(raw_path))
    return entries


def verify_isolated_imports(
    dist_packages_root: Path | None,
    repo_root: Path,
    *,
    python_bin: str = "/usr/bin/python3",
    expected_version: str | None = None,
) -> None:
    """Import all packaged curfewd modules in an isolated python3 subprocess outside the repo."""
    cmd = [
        python_bin,
        "-I",
        "-c",
        _ISOLATED_IMPORT_PROBE,
        "extracted" if dist_packages_root is not None else "installed",
        str(repo_root.resolve()),
        expected_version or "",
    ]
    if dist_packages_root is not None:
        cmd.append(str(dist_packages_root.resolve()))
    cmd.extend(REQUIRED_ENTRYPOINTS)

    res = subprocess.run(
        cmd,
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Isolated import check failed (exit {res.returncode}):\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    print(res.stdout.strip())


def verify_installed_binaries(expected_version: str | None = None) -> None:
    """Invoke installed CLI binaries from / so they load strictly from /usr/lib/python3/dist-packages."""
    for binary in INSTALLED_CLI_BINARIES:
        res = subprocess.run(
            [binary, "--version"],
            cwd="/",
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(
                f"{binary} --version failed (exit {res.returncode}): {res.stderr.strip()}"
            )
        output = (res.stdout + res.stderr).strip()
        if expected_version and expected_version not in output:
            raise RuntimeError(
                f"{binary} --version printed {output!r}, expected version {expected_version!r}"
            )
        print(f"Verified binary: {output}")

    help_res = subprocess.run(
        ["/usr/bin/curfewctl", "--help"],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )
    if help_res.returncode != 0:
        raise RuntimeError(
            f"/usr/bin/curfewctl --help failed (exit {help_res.returncode}): {help_res.stderr.strip()}"
        )


def verify_deb(
    deb_path: Path,
    repo_root: Path,
    *,
    check_installed: bool = False,
    expected_version: str | None = None,
) -> None:
    """Run full manifest, AST import, and isolated runtime verification against a built .deb."""
    if not deb_path.is_file():
        raise FileNotFoundError(f".deb package not found: {deb_path}")

    deb_entries = list_deb_entries(deb_path)

    # 1. No bytecode caches baked into the .deb archive
    leaked_bytecode = sorted(
        entry for entry in deb_entries if "__pycache__/" in entry or entry.endswith(".pyc")
    )
    if leaked_bytecode:
        raise RuntimeError(f"Unexpected bytecode files shipped in .deb: {leaked_bytecode}")

    # 2. Every tracked file under curfewd/, bin/, extras/, resource/ is packaged at expected path
    expected = expected_deb_targets(repo_root)
    missing: list[str] = []
    for src_rel, targets in sorted(expected.items()):
        if not targets:
            missing.append(f"{src_rel} (not mapped in debian/install)")
            continue
        for target in sorted(targets):
            if target not in deb_entries:
                missing.append(f"{src_rel} -> {target}")
    if missing:
        formatted = "\n  - ".join(missing)
        raise RuntimeError(f"Files missing from {deb_path.name}:\n  - {formatted}")

    py_count = sum(1 for k in expected if k.startswith("curfewd/") and k.endswith(".py"))
    print(
        f"Manifest OK: all {py_count} Python modules and {len(expected)} tracked files in {deb_path.name}"
    )

    # 3. Extract .deb and run AST audit + isolated runtime imports from extracted dist-packages
    with tempfile.TemporaryDirectory(prefix="curfewd-deb-verify-") as tmpdir:
        tmp_path = Path(tmpdir)
        subprocess.run(["dpkg-deb", "-x", str(deb_path), str(tmp_path)], check=True)
        extracted_dist = tmp_path / "usr" / "lib" / "python3" / "dist-packages"

        ast_errors = audit_internal_imports(extracted_dist)
        if ast_errors:
            formatted = "\n  - ".join(ast_errors)
            raise RuntimeError(
                f"AST internal import audit failed on extracted .deb:\n  - {formatted}"
            )
        print("AST internal import audit OK on extracted .deb")

        verify_isolated_imports(
            extracted_dist,
            repo_root,
            expected_version=expected_version,
        )

    # 4. Optionally verify live system installation under /usr/lib/python3/dist-packages
    if check_installed:
        verify_isolated_imports(None, repo_root, expected_version=expected_version)
        verify_installed_binaries(expected_version=expected_version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify curfewd .deb package completeness and isolated Python imports"
    )
    parser.add_argument("deb_path", type=Path, help="Path to built curfewd_*.deb archive")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Path to curfewd repository root",
    )
    parser.add_argument(
        "--installed",
        action="store_true",
        help="Also verify live installation under /usr/lib/python3/dist-packages and /usr/bin",
    )
    parser.add_argument(
        "--expected-version",
        default=None,
        help="Optional expected curfewd.__version__ string inside the package",
    )
    args = parser.parse_args(argv)
    verify_deb(
        args.deb_path,
        args.repo_root,
        check_installed=args.installed,
        expected_version=args.expected_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
