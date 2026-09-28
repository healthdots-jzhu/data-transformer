"""Run the transformer suite and enforce 96% statement AND branch coverage.

pytest-cov checks the combined percentage. Checking the two measures separately
also prevents covered statements from masking untested branches.
Run with the Python environment used for the project dependencies.
"""
from pathlib import Path
import json
import subprocess
import sys


PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
MINIMUM_PERCENT = 96.0


def main():
    """Produce reports, propagate test failures, then check each coverage measure."""
    command = [
        sys.executable, "-m", "pytest", "-q", "--cov", "--cov-branch",
        "--cov-report=term-missing", "--cov-report=json:coverage_integration.json",
        "--cov-report=html:htmlcov_integration", "--junitxml=test_results_integration.xml",
    ]
    completed = subprocess.run(command, cwd=PROJECT_DIRECTORY)
    if completed.returncode:
        return completed.returncode

    report = json.loads((PROJECT_DIRECTORY / "coverage_integration.json").read_text(encoding="utf-8"))
    failures = []
    selected = [
        entry["summary"] for filename, entry in report["files"].items()
        if filename.replace("\\", "/").startswith("data_transformer/")
    ]
    if not selected:
        failures.append("No coverage data for data_transformer")
    else:
        for label, covered_key, total_key in (
            ("statements", "covered_lines", "num_statements"),
            ("branches", "covered_branches", "num_branches"),
        ):
            covered = sum(summary[covered_key] for summary in selected)
            total = sum(summary[total_key] for summary in selected)
            percentage = 100 * covered / total if total else 100.0
            print(f"data_transformer: {label} {percentage:.2f}% ({covered}/{total})", flush=True)
            if percentage < MINIMUM_PERCENT:
                failures.append(f"data_transformer {label} must reach {MINIMUM_PERCENT:.0f}%")
    for failure in failures:
        print(f"COVERAGE FAILURE: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
