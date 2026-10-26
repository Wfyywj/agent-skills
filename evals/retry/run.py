"""Prepare three prompt conditions, grade saved Python candidates, or self-check."""

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import statistics
import sys
import tempfile
import time

import checks as basic
from maintenance import checks as maintenance

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ARMS = ("baseline", "workflow", "experience")
CORE = ("clarify-task", "plain-plan", "implement-slice", "verify-change", "review-change", "deliver-change")
SUITES = {"basic": basic, "maintenance": maintenance}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inputs(suite):
    task_dir = HERE if suite == "basic" else HERE / "maintenance"
    paths = [task_dir / "task.md", *(ROOT / "skills" / name / "SKILL.md" for name in CORE),
             ROOT / "experiences/retry-safety.md", task_dir / "checks.py", HERE / "run.py"]
    task = paths[0].read_text()
    if suite == "maintenance":
        paths.append(task_dir / "starter.py")
        task += "\n\n## 现有完整模块\n\n```python\n" + paths[-1].read_text() + "\n```\n"
    hashes = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}
    workflow = "\n\n# 本次提供的六节点技能\n\n" + "\n\n".join(path.read_text() for path in paths[1:7])
    experience = "\n\n# 本次提供的候选经验\n\n" + paths[7].read_text()
    # Identical task and output contract; only the optional context changes.
    prompts = dict(baseline=task, workflow=task + workflow, experience=task + workflow + experience)
    return hashes, prompts


def prepare(args):
    if args.trials < 1:
        raise ValueError("trials must be positive")
    hashes, prompts = inputs(args.suite)
    manifest = dict(
        schema_version=2, experiment="retry-context-ablation", suite=args.suite, model=args.model,
        reasoning_effort=args.effort, harness=args.harness, trials_per_arm=args.trials,
        prepared_at=datetime.now(timezone.utc).isoformat(),
        source_hashes=hashes,
        prompt_hashes={arm: digest(text.encode()) for arm, text in prompts.items()},
        shared_overhead=dict(experience_preparation_seconds=None, tool_build_seconds=None,
                             evaluation_and_maintenance_seconds=None, cost_usd=None),
        schedule=[dict(trial=trial + 1, arm=ARMS[(offset + trial) % len(ARMS)])
                  for trial in range(args.trials) for offset in range(len(ARMS))],
        notes="Use fresh sessions, no resume or answer repair. Unknown cost is null. Case counts are not independent generation counts.",
    )
    args.out.mkdir(parents=True, exist_ok=False)
    for arm, prompt in prompts.items():
        (args.out / f"{arm}.prompt.txt").write_text(prompt)
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Prepared {args.trials} generations per arm in {args.out}; follow manifest.schedule.")


def candidate_name(arm, trial, count):
    return arm if count == 1 else f"{arm}-{trial}"


def validate_manifest(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("schema_version") != 2 or manifest.get("suite") not in SUITES:
        raise ValueError("Use the original Git revision to replay a schema-1 trial; prepare schema-2 trials for this runner.")
    hashes, prompts = inputs(manifest["suite"])
    if manifest.get("source_hashes") != hashes:
        raise ValueError("Source snapshot changed; prepare a new trial instead of rewriting old evidence.")
    expected = {arm: digest(text.encode()) for arm, text in prompts.items()}
    if manifest.get("prompt_hashes") != expected:
        raise ValueError("Prompt hashes do not match the source snapshot.")
    for arm in ARMS:
        if digest((folder / f"{arm}.prompt.txt").read_bytes()) != expected[arm]:
            raise ValueError(f"Prepared prompt changed: {arm}; start a new trial.")
    return manifest


def stamp(args):
    manifest = validate_manifest(args.run)
    if not 1 <= args.trial <= manifest["trials_per_arm"]:
        raise ValueError("trial outside the predeclared count")
    name = candidate_name(args.arm, args.trial, manifest["trials_per_arm"])
    now = datetime.now(timezone.utc)
    started = args.run / f"{name}.started.json"
    if args.event == "start":
        data = dict(arm=args.arm, trial=args.trial, model=manifest["model"],
                    reasoning_effort=manifest["reasoning_effort"], started_at=now.isoformat())
        target = started
    else:
        data = json.loads(started.read_text())
        data.update(finished_observed_at=now.isoformat(),
                    observed_turn_seconds=round((now - datetime.fromisoformat(data["started_at"])).total_seconds(), 3),
                    generation_seconds=None, input_tokens=None, output_tokens=None, model_cost_usd=None,
                    boundary="Before dispatch to first observed final response; includes dispatch, queueing, and observer delay.")
        target = args.run / f"{name}.metrics.json"
    with target.open("x") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"Recorded {args.event}: {name}")


def worker(path, suite):
    # Process/time limits are not an OS sandbox. Only run reviewed candidates.
    spec = importlib.util.spec_from_file_location("candidate", path)
    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(sys.stderr):
        spec.loader.exec_module(module)
        checks = SUITES[suite]
        candidate = module.run if suite == "basic" else module
        results = [checks.evaluate(candidate, case) for case in checks.CASES]
    print(json.dumps(results, ensure_ascii=False))


def ratio(passed, total):
    return dict(passed=passed, total=total, rate=passed / total if total else None)


def score_candidate(path, suite):
    cases = SUITES[suite].CASES
    start, error = time.monotonic(), None
    try:
        with tempfile.TemporaryDirectory(prefix="retry-worker-") as cwd:
            result = subprocess.run(
                [sys.executable, "-B", str(HERE / "run.py"), "_worker", str(path), "--suite", suite],
                cwd=cwd, env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
                capture_output=True, text=True, timeout=5,
            )
        if result.returncode:
            raise ValueError(f"candidate worker exit {result.returncode}: {result.stderr[-1500:]}")
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or [row["id"] for row in rows] != [case["id"] for case in cases]:
            raise ValueError("invalid worker results")
    except (subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
        rows = [dict(id=case["id"], task=case.get("task", "retry"), split=case["split"], passed=False, errors=[error]) for case in cases]
    tasks = sorted({case.get("task", "retry") for case in cases})
    tasks_passed = {task: all(row["passed"] for row in rows if row.get("task", "retry") == task) for task in tasks}
    scores = {split: ratio(sum(row["passed"] for row in rows if row["split"] == split),
                           sum(row["split"] == split for row in rows)) for split in ("development", "holdout")}
    return dict(candidate_sha256=digest(path.read_bytes()), scores=scores, tasks_passed=tasks_passed,
                grade_wall_seconds=round(time.monotonic() - start, 4), candidate_error=error,
                passed_cases=[row["id"] for row in rows if row["passed"]],
                failures=[row for row in rows if not row["passed"]])


def grade(args):
    manifest = validate_manifest(args.run)
    count, suite = manifest["trials_per_arm"], manifest["suite"]
    if type(count) is not int or count < 1:
        raise ValueError("invalid predeclared trial count")
    for trial in range(1, count + 1):
        for arm in ARMS:
            name = candidate_name(arm, trial, count)
            if not (args.run / f"{name}.py").is_file():
                raise ValueError(f"Missing candidate: {name}.py; do not silently omit an attempt.")
    report = dict(schema_version=2, experiment=manifest["experiment"], suite=suite,
                  model=manifest["model"], reasoning_effort=manifest["reasoning_effort"], harness=manifest["harness"],
                  trials_per_arm=count, manifest_sha256=digest((args.run / "manifest.json").read_bytes()),
                  conditions={}, shared_overhead=manifest["shared_overhead"], limitations=[
                      "Small descriptive sample; no statistical significance or cross-model claim.",
                      "Two tasks in one candidate are correlated; only fresh generations count as independent runs.",
                      "Skill-context ablation, not execution of the six workflow stages or a real HTTP service.",
                      "Observed turn time includes dispatch, queueing and observer delay; it is not pure generation time.",
                      "Unknown model/overhead cost is null; total-cost savings cannot be inferred.",
                  ])
    for arm in ARMS:
        runs = []
        for trial in range(1, count + 1):
            name = candidate_name(arm, trial, count)
            row = score_candidate((args.run / f"{name}.py").resolve(), suite)
            metric_file = args.run / f"{name}.metrics.json"
            metrics = json.loads(metric_file.read_text()) if metric_file.is_file() else {}
            if metrics and any(metrics.get(key) != expected for key, expected in
                               (("arm", arm), ("trial", trial), ("model", manifest["model"]),
                                ("reasoning_effort", manifest["reasoning_effort"]))):
                raise ValueError(f"measurement identity mismatch: {name}")
            for key in ("observed_turn_seconds", "generation_seconds", "input_tokens", "output_tokens", "model_cost_usd"):
                value = metrics.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError(f"invalid measurement: {name}/{key}")
                metrics.setdefault(key, None)
            row.update(trial=trial, measurements=metrics)
            runs.append(row)
        task_names = sorted(runs[0]["tasks_passed"])
        times = [row["measurements"]["observed_turn_seconds"] for row in runs if row["measurements"]["observed_turn_seconds"] is not None]
        costs = [row["measurements"]["model_cost_usd"] for row in runs if row["measurements"]["model_cost_usd"] is not None]
        report["conditions"][arm] = dict(
            independent_generations=count,
            whole_run_success=ratio(sum(all(row["tasks_passed"].values()) for row in runs), count),
            task_success=ratio(sum(sum(row["tasks_passed"].values()) for row in runs), count * len(task_names)),
            case_success=ratio(sum(len(row["passed_cases"]) for row in runs), count * len(SUITES[suite].CASES)),
            per_task={task: ratio(sum(row["tasks_passed"][task] for row in runs), count) for task in task_names},
            observed_turn_seconds=dict(measured_runs=len(times), mean=statistics.mean(times) if times else None,
                                       minimum=min(times) if times else None, maximum=max(times) if times else None),
            model_cost_usd=dict(measured_runs=len(costs), total=sum(costs) if len(costs) == count else None),
            runs=runs,
        )
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as stream:
            stream.write(text)
    else:
        print(text, end="")
    # Completion means all conditions were scored, not that candidates passed.
    for arm, result in report["conditions"].items():
        score, cases = result["task_success"], result["case_success"]
        print(f"{arm}: tasks {score['passed']}/{score['total']}, cases {cases['passed']}/{cases['total']}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)
    p.add_argument("--harness", required=True)
    p.add_argument("--suite", choices=SUITES, default="basic")
    p.add_argument("--trials", type=int, default=1)
    g = sub.add_parser("grade")
    g.add_argument("--run", type=Path, required=True)
    g.add_argument("--output", type=Path)
    sub.add_parser("self-check")
    s = sub.add_parser("stamp", help="Record observed wall-clock boundaries, not provider generation time")
    s.add_argument("--run", type=Path, required=True)
    s.add_argument("--arm", choices=ARMS, required=True)
    s.add_argument("--trial", type=int, required=True)
    s.add_argument("--event", choices=("start", "finish"), required=True)
    w = sub.add_parser("_worker", help=argparse.SUPPRESS)
    w.add_argument("candidate", type=Path)
    w.add_argument("--suite", choices=SUITES, default="basic")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args)
        elif args.command == "grade":
            grade(args)
        elif args.command == "self-check":
            basic.self_check()
            maintenance.self_check()
        elif args.command == "stamp":
            stamp(args)
        else:
            worker(args.candidate, args.suite)
    except (OSError, ValueError, AssertionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
