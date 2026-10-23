"""Validate every skill directory and require the six workflow nodes."""

from pathlib import Path
import sys

from skills_ref import validate


REQUIRED = {
    "clarify-task",
    "plain-plan",
    "implement-slice",
    "verify-change",
    "review-change",
    "deliver-change",
}


def check_skills(root: Path) -> list[str]:
    skills = root / "skills"
    names = REQUIRED | {path.name for path in skills.glob("*") if path.is_dir()}
    errors = []
    for name in sorted(names):
        path = skills / name
        if not (path / "SKILL.md").is_file():
            errors.append(f"skills/{name}/SKILL.md: missing file")
            continue
        errors.extend(f"skills/{name}/SKILL.md: {error}" for error in validate(path))
    return errors


if __name__ == "__main__":
    errors = check_skills(Path(__file__).resolve().parents[1])
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("All skills are valid; the six workflow nodes are present.")
    sys.exit(1 if errors else 0)
