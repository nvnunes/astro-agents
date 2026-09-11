"""Fast in-process and explicit process-boundary research-log test helpers."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
LOG = SCRIPTS / "log"
PROCESS_TIMEOUT_SECONDS = 30
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
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def run_pyrun_process(
    cwd: Path,
    *arguments: str,
    environment_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an entry launcher without direct temporary-shebang dispatch."""

    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    if environment_updates is not None:
        environment.update(environment_updates)
    return subprocess.run(
        ["/bin/sh", str(cwd / "pyrun"), *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def fixture_parameter_roles(parameters, inputs=(), outputs=()):
    """Build explicit v5 roles for synthetic fixtures with known material sets."""
    from research_log_data import input_token_parts
    from validation.pyrun_contract import (
        automatic_option_role,
        recipe_script_parameters,
        split_argument_values,
    )

    options, positionals = split_argument_values(recipe_script_parameters(parameters))
    values = [(option.name, option.value) for option in options]
    values += [(f"@{index}", value) for index, value in enumerate(positionals, 1)]
    roles = {}
    for name, value in values:
        parts = input_token_parts(value)
        if parts is not None and parts[0] in inputs:
            role = "input"
        elif value in dict(outputs):
            role = "output"
        else:
            role = automatic_option_role(name) or "ordinary"
        roles[name] = role
    return tuple(sorted(roles.items()))


def replace_fixture_recipe(recipe, **changes):
    """Keep explicitly synthetic recipe roles aligned when changing its fields."""
    from dataclasses import replace

    updated = replace(recipe, **changes)
    from validation.pyrun_state import ExecutionRecipe
    if isinstance(updated, ExecutionRecipe) and "parameter_roles" not in changes:
        updated = replace(
            updated,
            parameter_roles=fixture_parameter_roles(
                updated.parameters,
                updated.inputs,
                updated.outputs,
            ),
        )
    return updated
