"""Fast in-process and explicit process-boundary helpers for ``scripts/log``."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LOG = SCRIPTS / "log"
sys.path.insert(0, str(SCRIPTS))

from log_commands.dispatcher import main  # noqa: E402


def run_log(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run dispatcher semantics in-process with captured CLI streams."""

    stdout = io.StringIO()
    stderr = io.StringIO()
    previous = Path.cwd()
    try:
        os.chdir(cwd)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                returncode = main(arguments)
            except SystemExit as error:
                returncode = error.code if isinstance(error.code, int) else 1
    finally:
        os.chdir(previous)
    return subprocess.CompletedProcess(
        [str(LOG), *arguments],
        returncode,
        stdout.getvalue(),
        stderr.getvalue(),
    )


def run_log_process(
    cwd: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run the executable when process startup or environment is under test."""

    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    return subprocess.run(
        [sys.executable, str(LOG), *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
