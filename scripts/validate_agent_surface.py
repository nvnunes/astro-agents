#!/usr/bin/env python3
"""Validate the astro-agents skill surface.

This is a lightweight repository-local harness, not a general test framework.
It separates deterministic repository checks from optional Codex runtime
discovery checks.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
ACTIVATION_CASES = ROOT / "tests" / "activation_cases.csv"

EXPECTED_OPENAI_KEYS = {
    "display_name",
    "short_description",
    "default_prompt",
}

STALE_TERMS = {
    "authoring/": {"docs/skills-upgrade-plan.md"},
    "validation/": {"docs/skills-upgrade-plan.md"},
    "research-log/": {"docs/skills-upgrade-plan.md"},
    "agents/validation": {"docs/skills-upgrade-plan.md"},
    "research-log-creation": set(),
    "skills/_shared": set(),
    "docs/upgrade-design.md": set(),
    "guidance/": set(),
    ".agents/astro-agents/reviews": set(),
    "routing-and-scope-review": set(),
    "Route Summary": set(),
}

SCAN_ROOTS = [
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "docs",
    ROOT / "examples",
    ROOT / ".agents",
    ROOT / "skills",
]


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    description: str


class ValidationError(Exception):
    """Raised for validation failures."""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate astro-agents skills, fixtures, and optional Codex discovery.",
    )
    parser.add_argument(
        "--codex-discovery",
        action="store_true",
        help="also run Codex runtime discovery checks using codex debug prompt-input",
    )
    parser.add_argument(
        "--activation-eval",
        action="store_true",
        help="run read-only Codex activation eval prompts from the activation cases fixture",
    )
    parser.add_argument(
        "--activation-eval-limit",
        type=int,
        default=0,
        help="maximum activation eval cases to run; 0 means all cases",
    )
    parser.add_argument(
        "--activation-cases",
        type=Path,
        default=ACTIVATION_CASES,
        help="CSV fixture for activation eval cases",
    )
    args = parser.parse_args()

    failures: list[str] = []

    checks = [
        ("skill frontmatter", lambda: check_skill_frontmatter()),
        ("OpenAI metadata", lambda: check_openai_metadata()),
        ("skill references", lambda: check_skill_references()),
        ("activation fixtures", lambda: check_activation_cases(args.activation_cases)),
        ("stale paths", lambda: check_stale_terms()),
    ]

    if args.codex_discovery:
        checks.append(("Codex discovery", lambda: check_codex_discovery()))
    if args.activation_eval:
        checks.append(
            (
                "activation eval",
                lambda: check_activation_eval(args.activation_cases, args.activation_eval_limit),
            )
        )

    for label, check in checks:
        try:
            check()
        except ValidationError as exc:
            failures.append(f"{label}: {exc}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    labels = ", ".join(label for label, _ in checks)
    print(f"OK {labels}")
    return 0


def check_skill_frontmatter() -> list[Skill]:
    skills: list[Skill] = []
    names: dict[str, Path] = {}

    for path in sorted(SKILLS.glob("*/SKILL.md")):
        data = parse_frontmatter(path)
        name = require_string(data, "name", path)
        description = require_string(data, "description", path)
        dirname = path.parent.name

        if name != dirname:
            raise ValidationError(f"{path}: name {name!r} does not match directory {dirname!r}")
        if name in names:
            raise ValidationError(f"duplicate skill name {name!r}: {names[name]} and {path}")

        names[name] = path
        skills.append(Skill(name=name, path=path, description=description))

    if not skills:
        raise ValidationError("no skills found")

    return skills


def check_openai_metadata() -> None:
    for skill in check_skill_frontmatter():
        metadata = skill.path.parent / "agents" / "openai.yaml"
        if not metadata.exists():
            raise ValidationError(f"{skill.name}: missing {relative(metadata)}")

        interface = parse_openai_interface(metadata)
        missing = EXPECTED_OPENAI_KEYS - set(interface)
        if missing:
            raise ValidationError(f"{relative(metadata)}: missing interface keys {sorted(missing)}")

        for key in EXPECTED_OPENAI_KEYS:
            if not interface[key].strip():
                raise ValidationError(f"{relative(metadata)}: interface.{key} is empty")

        prompt = interface["default_prompt"]
        if f"${skill.name}" not in prompt:
            raise ValidationError(
                f"{relative(metadata)}: default_prompt must mention ${skill.name}"
            )


def check_skill_references() -> None:
    failures: list[str] = []

    for path in sorted(SKILLS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for raw in extract_path_candidates(text):
            resolved = resolve_reference(path, raw)
            if resolved is None:
                continue
            if not resolved.exists():
                failures.append(f"{relative(path)} references missing {raw!r}")

    if failures:
        raise ValidationError("; ".join(failures))


def check_activation_cases(path: Path) -> None:
    load_activation_cases(path)


def load_activation_cases(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise ValidationError(f"missing fixture {relative(path)}")

    skills = {skill.name for skill in check_skill_frontmatter()}
    seen: set[str] = set()
    seen_cases: set[tuple[str, bool, str, str]] = set()
    required = {
        "id",
        "expected_skill",
        "expected_selected_skill",
        "should_trigger",
        "kind",
        "prompt",
    }

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValidationError(f"{relative(path)}: missing header")
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValidationError(f"{relative(path)}: missing columns {sorted(missing)}")

        rows = list(reader)

    if not rows:
        raise ValidationError(f"{relative(path)}: no cases")

    kinds: dict[str, int] = {"explicit": 0, "implicit": 0, "negative": 0}

    for row in rows:
        case_id = row["id"].strip()
        expected_skill = row["expected_skill"].strip()
        expected_selected_skill = row["expected_selected_skill"].strip()
        should_trigger = parse_bool(row["should_trigger"], path, case_id)
        kind = row["kind"].strip()
        prompt = row["prompt"].strip()

        if not case_id:
            raise ValidationError(f"{relative(path)}: blank id")
        if case_id in seen:
            raise ValidationError(f"{relative(path)}: duplicate id {case_id!r}")
        seen.add(case_id)

        if expected_skill not in skills:
            raise ValidationError(f"{case_id}: unknown expected_skill {expected_skill!r}")
        if expected_selected_skill not in skills:
            raise ValidationError(
                f"{case_id}: unknown expected_selected_skill {expected_selected_skill!r}"
            )
        if kind not in kinds:
            raise ValidationError(f"{case_id}: invalid kind {kind!r}")
        if kind == "explicit" and f"${expected_skill}" not in prompt:
            raise ValidationError(f"{case_id}: explicit prompt must mention ${expected_skill}")
        if kind == "negative" and should_trigger:
            raise ValidationError(f"{case_id}: negative cases must have should_trigger=false")
        if should_trigger and expected_selected_skill != expected_skill:
            raise ValidationError(
                f"{case_id}: triggering case must select expected_skill {expected_skill!r}"
            )
        if not should_trigger and expected_selected_skill == expected_skill:
            raise ValidationError(
                f"{case_id}: negative case cannot select excluded skill {expected_skill!r}"
            )
        if not prompt:
            raise ValidationError(f"{case_id}: blank prompt")

        fingerprint = (expected_skill, should_trigger, kind, prompt)
        if fingerprint in seen_cases:
            raise ValidationError(f"{relative(path)}: duplicate semantic case {case_id!r}")
        seen_cases.add(fingerprint)

        kinds[kind] += 1

    for kind, count in kinds.items():
        if count == 0:
            raise ValidationError(f"{relative(path)}: missing {kind} cases")

    missing_coverage: list[str] = []
    for skill in sorted(skills):
        explicit = any(
            row["expected_skill"].strip() == skill
            and row["kind"].strip() == "explicit"
            and row["should_trigger"].strip().lower() == "true"
            for row in rows
        )
        implicit = any(
            row["expected_skill"].strip() == skill
            and row["kind"].strip() == "implicit"
            and row["should_trigger"].strip().lower() == "true"
            for row in rows
        )
        negative = any(
            row["expected_skill"].strip() == skill
            and row["kind"].strip() == "negative"
            and row["should_trigger"].strip().lower() == "false"
            for row in rows
        )
        if not explicit:
            missing_coverage.append(f"{skill}: explicit")
        if not implicit:
            missing_coverage.append(f"{skill}: implicit")
        if not negative:
            missing_coverage.append(f"{skill}: negative")

    if missing_coverage:
        raise ValidationError("missing activation coverage " + ", ".join(missing_coverage))

    return rows


def check_activation_eval(path: Path, limit: int) -> None:
    cases = load_activation_cases(path)
    if limit < 0:
        raise ValidationError("--activation-eval-limit must be >= 0")
    if limit:
        cases = cases[:limit]

    failures: list[str] = []
    results: list[dict[str, object]] = []

    for case in cases:
        result = run_activation_case(case)
        results.append(result)
        if not result["passed"]:
            failures.append(f"{case['id']}: {result['reason']}")

    print(json.dumps({"activation_eval": results}, indent=2))

    if failures:
        raise ValidationError("; ".join(failures))


def check_stale_terms() -> None:
    failures: list[str] = []

    for path in iter_scan_files():
        rel = relative(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term, allowlist in STALE_TERMS.items():
            if rel not in allowlist and contains_stale_term(text, term):
                failures.append(f"{rel}: stale term {term!r}")

    if failures:
        raise ValidationError("; ".join(failures))


def check_codex_discovery() -> None:
    link = Path.home() / ".agents" / "skills" / "astro-agents"
    expected_target = SKILLS.resolve()

    if not link.is_symlink():
        raise ValidationError(f"{link} is not a symlink")
    if link.resolve() != expected_target:
        raise ValidationError(f"{link} resolves to {link.resolve()}, expected {expected_target}")

    command = ["codex", "debug", "prompt-input", "List available astro-agents skills."]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ValidationError("codex command not found") from exc

    if completed.returncode != 0:
        raise ValidationError(completed.stderr.strip() or "codex debug prompt-input failed")

    try:
        prompt_input = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationError("codex debug prompt-input did not return JSON") from exc

    serialized = json.dumps(prompt_input)
    missing: list[str] = []
    for skill in check_skill_frontmatter():
        if f"- {skill.name}:" not in serialized or str(skill.path) not in serialized:
            missing.append(skill.name)

    if missing:
        raise ValidationError(f"missing skills from prompt input: {', '.join(missing)}")


def run_activation_case(case: dict[str, str]) -> dict[str, object]:
    case_id = case["id"].strip()
    expected_skill = case["expected_skill"].strip()
    expected_selected_skill = case["expected_selected_skill"].strip()
    prompt = case["prompt"].strip()

    with tempfile.NamedTemporaryFile("w+", suffix=".json", encoding="utf-8") as output:
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--output-last-message",
            output.name,
            build_activation_eval_prompt(case),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output.seek(0)
        raw_message = output.read().strip()

    if completed.returncode != 0:
        return {
            "id": case_id,
            "expected_skill": expected_skill,
            "expected_selected_skill": expected_selected_skill,
            "passed": False,
            "reason": completed.stderr.strip() or "codex exec failed",
        }

    try:
        report = parse_json_object(raw_message)
    except ValidationError as exc:
        return {
            "id": case_id,
            "expected_skill": expected_skill,
            "expected_selected_skill": expected_selected_skill,
            "passed": False,
            "reason": str(exc),
            "raw_message": raw_message,
        }

    selected_skill = str(report.get("selected_skill", "")).strip()
    activated = report.get("activated")
    if not isinstance(activated, bool):
        return {
            "id": case_id,
            "expected_skill": expected_skill,
            "expected_selected_skill": expected_selected_skill,
            "passed": False,
            "reason": "activation eval response field 'activated' was not boolean",
        }

    passed = activated and selected_skill == expected_selected_skill
    reason = (
        "ok"
        if passed
        else f"expected selection of {expected_selected_skill!r}, got {selected_skill!r}"
    )

    return {
        "id": case_id,
        "expected_skill": expected_skill,
        "expected_selected_skill": expected_selected_skill,
        "selected_skill": selected_skill,
        "activated": activated,
        "passed": passed,
        "reason": reason,
        "model_reason": str(report.get("reason", "")).strip(),
    }


def build_activation_eval_prompt(case: dict[str, str]) -> str:
    return "\n".join(
        [
            "This is an astro-agents skill activation evaluation.",
            "Do not edit files, run commands, or perform the requested task.",
            "Decide which available skill, if any, should be active for the user request.",
            "Respond only as compact JSON with keys:",
            '{"selected_skill": string, "activated": boolean, "reason": string}',
            "",
            f"Expected skill under test: {case['expected_skill'].strip()}",
            f"Case kind: {case['kind'].strip()}",
            f"Should trigger expected skill: {case['should_trigger'].strip()}",
            "",
            "User request:",
            case["prompt"].strip(),
        ]
    )


def parse_json_object(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise ValidationError("activation eval response was not JSON")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"activation eval response JSON parse failed: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValidationError("activation eval response JSON was not an object")
    return parsed


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not match:
        raise ValidationError(f"{relative(path)}: missing frontmatter")

    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValidationError(f"{relative(path)}: invalid frontmatter line {line!r}")
        key, value = line.split(":", 1)
        values[key.strip()] = strip_quotes(value.strip())
    return values


def parse_openai_interface(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    in_interface = False

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("interface:"):
            in_interface = True
            continue
        if in_interface and line and not line.startswith(" "):
            break
        if not in_interface or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = strip_quotes(value.strip())

    return values


def require_string(data: dict[str, str], key: str, path: Path) -> str:
    value = data.get(key, "").strip()
    if not value:
        raise ValidationError(f"{relative(path)}: missing {key}")
    return value


def extract_path_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for raw in re.findall(r"`([^`\n]+)`", text):
        if "/" not in raw:
            continue
        if raw.endswith("/"):
            continue
        if any(marker in raw for marker in ("<", ">", "*", " ")):
            continue
        if raw.startswith(("./", "/")):
            continue
        if raw.startswith(("references/", "scripts/", "../", "skills/", ".agents/")):
            candidates.append(raw)
    return candidates


def resolve_reference(source: Path, raw: str) -> Path | None:
    if raw.startswith("skills/"):
        return ROOT / raw
    if raw.startswith(".agents/astro-agents/"):
        return ROOT / raw
    if raw.startswith("../") and re.fullmatch(r"\.\./[^/]+/SKILL\.md", raw):
        return (source.parent / raw).resolve()
    if raw.startswith(("references/", "scripts/")) and source.name == "SKILL.md":
        skill_root = find_skill_root(source)
        if skill_root is None:
            return None
        return skill_root / raw
    return None


def find_skill_root(path: Path) -> Path | None:
    for parent in [path.parent, *path.parents]:
        if parent.parent == SKILLS and (parent / "SKILL.md").exists():
            return parent
    return None


def iter_scan_files() -> list[Path]:
    paths: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            paths.append(root)
        elif root.is_dir():
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix in {".md", ".yaml", ".yml"}
            )
    return sorted(paths)


def parse_bool(raw: str, path: Path, case_id: str) -> bool:
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValidationError(f"{relative(path)}:{case_id}: invalid boolean {raw!r}")


def contains_stale_term(text: str, term: str) -> bool:
    if term.endswith("/"):
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(term)}"
        return re.search(pattern, text) is not None
    return term in text


def strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
