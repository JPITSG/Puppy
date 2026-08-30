#!/usr/bin/env python3
"""No-quota tests for runtime environment and source-tree launch portability."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import sys
import tempfile
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PRIVATE_TESTS = BASE / "data" / "tests"
PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE_TESTS.chmod(0o700)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="portability-", dir=str(PRIVATE_TESTS)))

from puppy import user_paths  # noqa: E402
from puppy.drivers.base import clean_env  # noqa: E402
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.drivers.codex import _codex_home  # noqa: E402


def _python_output(code: str, environment: dict) -> str:
    return subprocess.check_output(
        [sys.executable, "-c", code], cwd="/", env=environment,
        stderr=subprocess.STDOUT, text=True).strip()


def test_service_home() -> None:
    configured = TEST_ROOT / "configured home"
    configured.mkdir()
    with mock.patch.dict(os.environ, {"HOME": str(configured)}):
        assert user_paths.service_home() == str(configured)
        assert _codex_home() == str(configured / ".codex")
        assert ClaudeDriver().auth_touch_paths() == [
            str(configured / ".claude" / ".credentials.json")]
    with mock.patch.dict(os.environ, {"HOME": ""}), mock.patch(
            "puppy.user_paths.pwd.getpwuid",
            return_value=SimpleNamespace(pw_dir="/srv/puppy-user")):
        assert user_paths.service_home() == "/srv/puppy-user"
    with mock.patch.dict(os.environ, {"HOME": "relative/home"}), mock.patch(
            "puppy.user_paths.pwd.getpwuid", side_effect=KeyError("missing")):
        assert user_paths.service_home() == "/"


def test_claude_root_bypass_environment() -> None:
    driver = ClaudeDriver()

    def turn_env(permission_mode: str, effective_uid: int) -> dict:
        # Model the runner's per-turn environment construction, including an
        # ambient value that Puppy must never leak into an ineligible turn.
        env = clean_env({"PATH": "/usr/bin", "IS_SANDBOX": "ambient"})
        with mock.patch("puppy.drivers.claude.os.geteuid",
                        return_value=effective_uid):
            env.update(driver.build_env(
                {"permission_mode": permission_mode}, True, "prompt", "id"))
        return env

    assert turn_env("bypassPermissions", 0)["IS_SANDBOX"] == "1"
    assert "IS_SANDBOX" not in turn_env("auto", 0)
    assert "IS_SANDBOX" not in turn_env("dontAsk", 0)
    assert "IS_SANDBOX" not in turn_env("bypassPermissions", 1000)

    # A mode flip is observed on the next process environment in both
    # directions; Claude is spawned anew for every Puppy turn.
    switched = [turn_env(mode, 0).get("IS_SANDBOX") for mode in (
        "bypassPermissions", "auto", "bypassPermissions")]
    assert switched == ["1", None, "1"]


def test_config_default_and_persistence() -> None:
    first_home = TEST_ROOT / "first home"
    second_home = TEST_ROOT / "second home"
    first_home.mkdir()
    second_home.mkdir()
    data_dir = TEST_ROOT / "config data"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(BASE) + os.pathsep + environment.get("PYTHONPATH", "")
    environment["PUPPY_DATA"] = str(data_dir)
    environment["HOME"] = str(first_home)
    read_default = "from puppy import config; print(config.get('sessions.default_cwd'))"
    assert _python_output(read_default, environment) == str(first_home)

    explicit = "/srv/projects-explicit"
    persist = (
        "from puppy import config; "
        "config.set_value('sessions.default_cwd', {!r}); "
        "print(config.get('sessions.default_cwd'))".format(explicit))
    assert _python_output(persist, environment) == explicit
    environment["HOME"] = str(second_home)
    assert _python_output(read_default, environment) == explicit


def test_source_tree_launcher() -> None:
    project = TEST_ROOT / "portable project"
    tools_dir = TEST_ROOT / "runtime tools"
    project.mkdir()
    tools_dir.mkdir()
    launcher = project / "run.sh"
    shutil.copy2(str(BASE / "run.sh"), str(launcher))
    launcher.chmod(0o700)
    record = TEST_ROOT / "launcher result"
    python_shim = tools_dir / "python shim"
    python_shim.write_text(
        "#!/bin/sh\n"
        "{ pwd; printf '%s\\n' \"$@\"; } > \"$PUPPY_TEST_OUTPUT\"\n",
        encoding="utf-8")
    python_shim.chmod(0o700)
    environment = dict(os.environ)
    environment.update({
        "PUPPY_PYTHON": str(python_shim),
        "PUPPY_TEST_OUTPUT": str(record),
    })
    subprocess.run([str(launcher)], cwd="/", env=environment, check=True)
    assert record.read_text(encoding="utf-8").splitlines() == [
        str(project), "-m", "puppy"]


def main() -> None:
    try:
        test_service_home()
        test_claude_root_bypass_environment()
        test_config_default_and_persistence()
        test_source_tree_launcher()
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)
    print("runtime environment and source-tree launcher portability passed")


if __name__ == "__main__":
    main()
