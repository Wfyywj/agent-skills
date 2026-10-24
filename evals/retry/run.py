"""Prepare three prompt conditions, grade saved Python candidates, or self-check."""

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from checks import CASES, evaluate, self_check

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ARMS = ("baseline", "workflow", "experience")
CORE = ("clarify-task", "plain-plan", "implement-slice", "verify-change", "review-change", "deliver-change")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inputs():
    paths = [HERE / "task.md", *(ROOT / "skills" / name / "SKILL.md" for name in CORE),
             ROOT / "experiences/retry-safety.md", HERE / "checks.py", HERE / "run.py"]
    hashes = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}
    task = paths[0].read_text()
    workflow = "\n\n# 本次提供的六节点技能\n\n" + "\n\n".join(path.read_text() for path in paths[1:7])
    experience = "\n\n# 本次提供的候选经验\n\n" + paths[7].read_text()
    # Identical task and output contract; only the optional context changes.
    prompts = dict(baseline=task, workflow=task + workflow, experience=task + workflow + experience)
    return hashes, prompts


def prepare(args):
    hashes, prompts = inputs()
    manifest = dict(
        schema_version=1, experiment="retry-context-ablation", model=args.model,
        reasoning_effort=args.effort, harness=args.harness, trials_per_arm=1,
        source_hashes=hashes,
        prompt_hashes={arm: digest(text.encode()) for arm, text in prompts.items()},
        measurements={arm: dict(generation_seconds=None, input_tokens=None,
                                output_tokens=None, model_cost_usd=None) for arm in ARMS},
        shared_overhead=dict(experience_preparation_seconds=None, tool_build_seconds=None,
                             evaluation_and_maintenance_seconds=None, cost_usd=None),
        notes="Metadata is operator-reported. Unknown cost is null, never zero. No statistical conclusion from one trial.",
    )
    args.out.mkdir(parents=True, exist_ok=False)
    for arm, prompt in prompts.items():
        (args.out / f"{arm}.prompt.txt").write_text(prompt)
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared three prompts in {args.out}. Save unedited outputs as baseline.py, workflow.py, experience.py.")


def worker(path):
    # Process/time limits are not an OS sandbox. Only run reviewed candidates.
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
        results = [evaluate(module.run, case) for case in CASES]
    print(json.dumps(results, ensure_ascii=False))


def grade(args):
    manifest = json.loads((args.run / "manifest.json").read_text())
    hashes, prompts = inputs()
    if manifest.get("schema_version") != 1 or manifest.get("source_hashes") != hashes:
        raise ValueError("Source snapshot changed or manifest invalid; prepare a new trial instead of rewriting old evidence.")
    if manifest.get("prompt_hashes") != {arm: digest(text.encode()) for arm, text in prompts.items()}:
        raise ValueError("Prompt hashes do not match the source snapshot.")
    for arm in ARMS:
        if digest((args.run / f"{arm}.prompt.txt").read_bytes()) != manifest["prompt_hashes"][arm]:
            raise ValueError(f"Prepared prompt changed: {arm}; start a new trial.")
        if not (args.run / f"{arm}.py").is_file():
            raise ValueError(f"Missing candidate: {arm}.py; do not silently omit a condition.")
    report = dict(experiment=manifest["experiment"], model=manifest["model"],
                  reasoning_effort=manifest["reasoning_effort"], harness=manifest["harness"],
                  trials_per_arm=1, manifest_sha256=digest((args.run / "manifest.json").read_bytes()),
                  conditions={}, limitations=[
                      "One generation per condition; no significance or generalization claim.",
                      "Skill-context ablation, not an execution of all six workflow stages.",
                      "Deterministic simulated transport, not a real HTTP service or business idempotency test.",
                      "Holdout cases were not sent to generation; they remain public and share one API contract.",
                      "Total model, preparation and maintenance costs are unknown; cost savings cannot be inferred.",
                  ])
    for arm in ARMS:
        path = (args.run / f"{arm}.py").resolve()
        start = time.monotonic()
        error = None
        try:
            with tempfile.TemporaryDirectory(prefix="retry-worker-") as cwd:
                result = subprocess.run(
                    [sys.executable, "-B", str(HERE / "run.py"), "_worker", str(path)],
                    cwd=cwd, env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True, text=True, timeout=5,
                )
            if result.returncode:
                raise ValueError(f"candidate worker exit {result.returncode}: {result.stderr[-1500:]}")
            rows = json.loads(result.stdout)
            if not isinstance(rows, list) or [row["id"] for row in rows] != [case["id"] for case in CASES]:
                raise ValueError("invalid worker results")
        except (subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
            error = str(exc)
            rows = [dict(id=case["id"], split=case["split"], passed=False, errors=[error]) for case in CASES]
        scores = {}
        for split in ("development", "holdout"):
            selected = [row for row in rows if row["split"] == split]
            scores[split] = dict(passed=sum(row["passed"] for row in selected), total=len(selected))
        report["conditions"][arm] = dict(
            candidate_sha256=digest(path.read_bytes()), scores=scores,
            grade_wall_seconds=round(time.monotonic() - start, 4),
            candidate_error=error, cases=rows,
        )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(text)
    else:
        print(text, end="")
    # Completion means all conditions were scored, not that candidates passed.
    for arm, result in report["conditions"].items():
        score = result["scores"]["holdout"]
        print(f"{arm}: holdout {score['passed']}/{score['total']}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)
    p.add_argument("--harness", required=True)
    g = sub.add_parser("grade")
    g.add_argument("--run", type=Path, required=True)
    g.add_argument("--output", type=Path)
    sub.add_parser("self-check")
    w = sub.add_parser("_worker", help=argparse.SUPPRESS)
    w.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args)
        elif args.command == "grade":
            grade(args)
        elif args.command == "self-check":
            self_check()
        else:
            worker(args.candidate)
    except (OSError, ValueError, AssertionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
