import json
import os
import subprocess
import sys

from webapp.config import (
    GOLDEN_DIR,
    REPO_ROOT,
    VALIDATION_DIR,
    VALIDATION_SCRIPT,
)


def run_validation():
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATION_SCRIPT),
            "--details",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=os.environ,
    )

    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def latest_validation_report():
    reports = sorted(
        VALIDATION_DIR.glob("validation-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not reports:
        return None

    path = reports[0]

    try:
        with path.open() as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    return {
        "path": path,
        "name": path.name,
        "data": report,
    }


def validation_step_status(report):
    if not report:
        return {}

    steps = report.get("steps", {})

    statuses = {}

    render = steps.get("render")

    if isinstance(render, list):
        statuses["render"] = all(
            item.get("returncode") == 0
            for item in render
        )
    else:
        statuses["render"] = False

    for step_name in (
        "collect",
        "intent_validation",
        "drift",
    ):
        step = steps.get(step_name)

        if not isinstance(step, dict):
            statuses[step_name] = False
            continue

        if step.get("skipped"):
            statuses[step_name] = None
        else:
            statuses[step_name] = (
                step.get("returncode") == 0
            )

    return statuses


def golden_snapshots():
    if not GOLDEN_DIR.exists():
        return []

    snapshots = []

    for path in GOLDEN_DIR.iterdir():
        if not path.is_dir():
            continue

        manifest_path = path / "manifest.json"

        if not manifest_path.is_file():
            continue

        try:
            with manifest_path.open() as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        snapshots.append(
            {
                "snapshot_id": path.name,
                "path": path,
                "manifest": manifest,
            }
        )

    return sorted(
        snapshots,
        key=lambda item: item["snapshot_id"],
        reverse=True,
    )
