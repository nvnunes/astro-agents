"""Authored current-format research fixtures shared by native reproduction tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from log_commands.context import EntryContext, LogContext
from log_commands.reproduction_invocation import ReproductionRuntime
from log_commands.reproduction_planner import (
    ReproductionSelection,
    plan_reproduction_work,
    prepare_reproduction_context,
)
from research_log_cli_test_support import fixture_parameter_roles
from research_log_data import Fingerprint
from validation.engine import (
    EvaluationRequest,
    FullEvaluationTarget,
    evaluate_mechanical,
)
from validation.pyrun_state import (
    ExecutionRecipe,
    ObservedExecution,
    PyrunCommand,
    PyrunExecution,
    PyrunFile,
    execution_id,
)


def _fingerprint(path: Path) -> Fingerprint:
    return Fingerprint("sha256", hashlib.sha256(path.read_bytes()).hexdigest())


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _evidence_scoped_fixture(
    root: Path,
) -> tuple[_Fixture, object, Path, str, object]:
    """Build a planner-produced evidence definition, never raw plan JSON."""

    fixture = _Fixture(root)
    entry = fixture.entry(1)
    raw = entry.root / "data" / "raw.txt"
    output = entry.root / "data" / "output.txt"
    raw.write_text("raw\n", encoding="utf-8")
    output.write_text("stable\nruntime 1\n", encoding="utf-8")
    fixture.write_data(entry, [fixture.item(entry, "raw", raw, origin=True)])
    identity, execution = fixture.execution(
        entry, "build", {"raw": raw}, {"output": output}
    )
    fixture.write_pyrun(entry, [(identity, execution)])
    data_path = entry.root / "data.json"
    data = __import__("json").loads(data_path.read_text(encoding="utf-8"))
    data["inputs"].append(
        {
            "reproduction_comparison": {
                "contract": "research-log-evidence-scoped-comparison/1",
                "profile": "evidence",
            },
            "identity": {"algorithm": "sha256"},
            "kind": "file",
            "location": "data/output.txt",
            "name": "output",
            "origin": False,
        }
    )
    data_path.write_text(__import__("json").dumps(data), encoding="utf-8")
    (entry.root / "evidence.json").write_text(
        __import__("json").dumps(
            {
                "records": [
                    {
                        "document": f"entries/{entry.root.name}/{entry.id}.md",
                        "id": "stable-output",
                        "kind": "output",
                        "sources": [
                            {
                                "locator": {"text": {"line": "1"}},
                                "source": "<output>",
                            }
                        ],
                        "transformation": None,
                    }
                ],
                "schema": "research-log-evidence/v5",
            }
        ),
        encoding="utf-8",
    )
    document = entry.root / f"{entry.id}.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "Recorded.",
            "<!-- eid:stable-output source=output line=1 -->\n```text\nstable\n```",
        ),
        encoding="utf-8",
    )
    return fixture, entry, output, identity, prepare_plan(fixture, entry)


class _Fixture:
    def __init__(self, root: Path):
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        self.root = root
        self.summary = root / "docs" / "study.md"
        self.log_root = self.summary.with_suffix("")
        self.log_root.mkdir(parents=True)
        self.summary.write_text("# Study\n\n## Entries\n", encoding="utf-8")
        self.log = LogContext(self.summary.resolve(), self.log_root.resolve())

    def entry(self, number: int) -> EntryContext:
        entry_id = f"e{number:03d}"
        root = self.log_root / "entries" / f"2026-09-{number:02d}-{entry_id}-study"
        (root / "data").mkdir(parents=True)
        (root / "scripts").mkdir()
        relative = (Path("study") / "entries" / root.name / f"{entry_id}.md").as_posix()
        with self.summary.open("a", encoding="utf-8") as handle:
            handle.write(f"\n- [Fixture]({relative})\n")
        return EntryContext(self.log, entry_id, root.resolve())

    def write_data(self, entry: EntryContext, items: list[dict[str, object]]) -> None:
        _write_json(
            entry.root / "data.json",
            {"inputs": items, "schema": "research-log-data/v6"},
        )

    def item(
        self, entry: EntryContext, name: str, path: Path, *, origin: bool
    ) -> dict[str, object]:
        return {
            "identity": {"algorithm": "sha256"},
            "kind": "file",
            "location": os.path.relpath(path, entry.root),
            "name": name,
            "origin": origin,
        }

    def evidence(self, entry: EntryContext, *names: str) -> None:
        document = entry.root / f"{entry.id}.md"
        data = json.loads((entry.root / "data.json").read_text(encoding="utf-8"))
        locations = {
            item["name"]: item["location"]
            for item in data["inputs"]
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and isinstance(item.get("location"), str)
        }
        artifacts = []
        for number, name in enumerate(names, 1):
            location = locations[name]
            artifacts.append(
                f"[artifact]({location})<!-- eid:result-{number} source={name} -->"
            )
        document.write_text(
            "# Entry\n\n## Evidence\n\n`Background:`\n\n"
            "Fixture evidence.\n\n`Steps:`\n\nInspect retained output.\n\n"
            "`Results:`\n\n" + "\n".join(artifacts) + "\n",
            encoding="utf-8",
        )
        _write_json(
            entry.root / "evidence.json",
            {
                "records": [
                    {
                        "document": (f"entries/{entry.root.name}/{entry.id}.md"),
                        "id": f"result-{number}",
                        "kind": "artifact",
                        "sources": [{"locator": None, "source": f"<{name}>"}],
                        "transformation": None,
                        "artifact_fingerprint": _fingerprint(
                            entry.root / locations[name]
                        ).as_dict(),
                    }
                    for number, name in enumerate(names, 1)
                ],
                "schema": "research-log-evidence/v5",
            },
        )

    def execution(
        self,
        entry: EntryContext,
        name: str,
        inputs: dict[str, Path],
        outputs: dict[str, Path],
        *,
        auto_reproduce: bool = True,
        requires_reproduction: bool = True,
        last_run_at: str | None = None,
    ) -> tuple[str, PyrunExecution]:
        script = entry.root / "scripts" / f"{name}.py"
        script.write_text(f"# {name}\n", encoding="utf-8")
        parameters = tuple(
            token
            for input_name in sorted(inputs)
            for token in ("--input-data", f"<{input_name}>")
        ) + tuple(
            token
            for _output_name, output_path in sorted(outputs.items())
            for token in ("--output-data", f"data/{output_path.name}")
        )
        recipe = ExecutionRecipe(
            f"scripts/{name}.py",
            parameters,
            (),
            tuple(sorted(inputs)),
            tuple(sorted((f"data/{path.name}", "file") for path in outputs.values())),
            parameter_roles=fixture_parameter_roles(
                parameters,
                tuple(sorted(inputs)),
                tuple(
                    sorted((f"data/{path.name}", "file") for path in outputs.values())
                ),
            ),
        )
        observed = ObservedExecution(
            _fingerprint(script),
            tuple(sorted((key, _fingerprint(path)) for key, path in inputs.items())),
            (),
            tuple(
                sorted(
                    (f"data/{path.name}", _fingerprint(path))
                    for path in outputs.values()
                )
            ),
        )
        execution = PyrunExecution(
            requires_reproduction,
            auto_reproduce,
            last_run_at,
            "research-log-pyrun-runner/1",
            "pyrun-standard/v1",
            "research-log-pyrun-execution/2",
            recipe,
            observed,
        )
        document = entry.root / f"{entry.id}.md"
        if not document.exists():
            document.write_text("# Entry\n", encoding="utf-8")
        command = f"./pyrun --cid {name}"
        if not auto_reproduce:
            command += " --auto-reproduce=false"
        command += " -- scripts/{name}.py".format(name=name)
        command += "".join(
            f" --input-data '<{input_name}>'" for input_name in sorted(inputs)
        )
        command += "".join(
            f" --output-data '<{output_name}>'"
            for output_name, _output_path in sorted(outputs.items())
        )
        document.write_text(
            document.read_text(encoding="utf-8")
            + f"\n## {name}\n\n`Background:`\n\nFixture command.\n\n"
            "`Steps:`\n\n```bash\n" + command + "\n```\n\n`Results:`\n\nRecorded.\n",
            encoding="utf-8",
        )
        return execution_id(recipe), execution

    def write_pyrun(
        self, entry: EntryContext, executions: list[tuple[str, PyrunExecution]]
    ) -> None:
        state = PyrunFile(
            entry.root / "pyrun.json",
            entry.root,
            {
                execution.recipe.script.rsplit("/", 1)[-1].removesuffix(
                    ".py"
                ): PyrunCommand({identity: execution})
                for identity, execution in executions
            },
        )
        (entry.root / "pyrun.json").write_text(state.serialized(), encoding="utf-8")


def prepare_plan(
    fixture,
    entry=None,
    *,
    include_all=False,
    recheck=False,
    runtime=ReproductionRuntime(),
):
    evaluation = evaluate_mechanical(
        EvaluationRequest(fixture.summary, FullEvaluationTarget())
    )
    return plan_reproduction_work(
        fixture.log,
        prepare_reproduction_context(evaluation),
        entry=entry,
        include_all=include_all,
        runtime=runtime,
        selection=ReproductionSelection("recheck" if recheck else "incremental"),
    )
