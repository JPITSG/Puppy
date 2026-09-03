#!/usr/bin/env python3
"""Build the single-file headless Puppy backend zipapp."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipapp

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from puppy import __version__  # noqa: E402


def _commit() -> str:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=str(ROOT),
            stderr=subprocess.DEVNULL, text=True).strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=str(ROOT), stderr=subprocess.DEVNULL, text=True).strip())
        return value + ("-dirty" if dirty else "")
    except Exception:
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(BACKEND_DIR / "dist" / "puppy-backend.pyz"))
    parser.add_argument("--launcher-output",
                        help="launcher path (default: puppy-backend-launcher.py beside the zipapp)")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    launcher_output = (Path(args.launcher_output).resolve() if args.launcher_output else
                       output.with_name("puppy-backend-launcher.py"))
    license_output = output.with_name("LICENSE")
    output.parent.mkdir(parents=True, exist_ok=True)
    commit = _commit()

    with tempfile.TemporaryDirectory(prefix="puppy-backend-build-") as tmp:
        stage = Path(tmp) / "stage"
        shutil.copytree(ROOT / "puppy", stage / "puppy",
                        ignore=shutil.ignore_patterns("static", "__pycache__", "*.pyc"))
        shutil.copytree(BACKEND_DIR / "puppy_backend", stage / "puppy_backend",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
        (stage / "puppy_backend" / "build_info.py").write_text(
            '"""Generated backend artifact metadata."""\n\n'
            f'ARTIFACT_KIND = "zipapp"\nBUILD_COMMIT = {commit!r}\n',
            encoding="utf-8")
        (stage / "__main__.py").write_text(
            "from puppy_backend.cli import main\n\nmain()\n", encoding="utf-8")
        temporary_output = output.with_name("." + output.name + ".tmp")
        try:
            temporary_output.unlink()
        except FileNotFoundError:
            pass
        zipapp.create_archive(stage, target=temporary_output,
                              interpreter="/usr/bin/env python3", compressed=True)
        os.chmod(temporary_output, 0o755)
        os.replace(temporary_output, output)

    launcher_temporary = launcher_output.with_name("." + launcher_output.name + ".tmp")
    launcher_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BACKEND_DIR / "launcher.py", launcher_temporary)
    os.chmod(launcher_temporary, 0o755)
    os.replace(launcher_temporary, launcher_output)
    license_temporary = license_output.with_name("." + license_output.name + ".tmp")
    shutil.copy2(ROOT / "LICENSE", license_temporary)
    os.chmod(license_temporary, 0o644)
    os.replace(license_temporary, license_output)

    size_kib = output.stat().st_size / 1024
    print(f"built {output} ({size_kib:.1f} KiB), puppy {__version__}, commit {commit or 'unknown'}")
    print(f"copied upgrade launcher to {launcher_output}")
    print(f"copied MIT license to {license_output}")


if __name__ == "__main__":
    main()
