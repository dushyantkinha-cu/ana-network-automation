#!/usr/bin/env python3

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from automation.deployment.common import (  # noqa: E402
    DeploymentSafetyError,
    find_deployment_target,
)
from automation.render_config import (  # noqa: E402
    load_inventory,
)


RENDER_SCRIPT = (
    REPO_ROOT
    / "automation"
    / "render_config.py"
)

GENERATED_CONFIG_DIR = (
    REPO_ROOT
    / "generated-configs"
)


def fail(message):
    print(
        f"ERROR: {message}",
        file=sys.stderr,
    )
    sys.exit(1)


def render_device(hostname):
    result = subprocess.run(
        [
            sys.executable,
            str(RENDER_SCRIPT),
            "--device",
            hostname,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        message = (
            result.stderr.strip()
            or result.stdout.strip()
            or "Configuration rendering failed."
        )

        raise DeploymentSafetyError(
            f"{hostname}: {message}"
        )

    output_path = (
        GENERATED_CONFIG_DIR
        / f"{hostname}.cfg"
    )

    if not output_path.is_file():
        raise DeploymentSafetyError(
            f"{hostname}: renderer completed but "
            f"{output_path} was not created."
        )

    return output_path


def sha256_file(path):
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(65536),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def count_lines(path):
    with path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        return sum(1 for _ in handle)


def display_path(path):
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path


def print_plan(
    target,
    rendered_path,
):
    print(
        "=== DEPLOYMENT DRY-RUN PLAN ==="
    )
    print(
        f"Device:              "
        f"{target.hostname}"
    )
    print(
        f"NetBox device ID:    "
        f"{target.device_id}"
    )
    print(
        f"Status:              "
        f"{target.status}"
    )
    print(
        f"Platform:            "
        f"{target.platform}"
    )
    print(
        f"Profile:             "
        f"{target.config_profile}"
    )
    print(
        f"Adapter:             "
        f"{target.adapter}"
    )
    print(
        f"Management IP:       "
        f"{target.management_ip}"
    )
    print(
        f"Rendered config:     "
        f"{display_path(rendered_path)}"
    )
    print(
        f"Rendered lines:      "
        f"{count_lines(rendered_path)}"
    )
    print(
        f"Rendered SHA-256:    "
        f"{sha256_file(rendered_path)}"
    )
    print(
        "Safety gates:        PASS"
    )
    print(
        "Mode:                DRY RUN"
    )

    print()
    print(
        "No device connection was opened."
    )
    print(
        "No device configuration was attempted."
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Build a safe deployment plan "
            "for one managed network device."
        )
    )

    parser.add_argument(
        "--device",
        required=True,
        help=(
            "Managed NetBox device hostname, "
            "for example R3."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Render and validate the deployment "
            "plan without contacting the device."
        ),
    )

    args = parser.parse_args()

    if not args.dry_run:
        parser.error(
            "Stage 7B supports dry-run only. "
            "Real deployment is not implemented."
        )

    inventory = load_inventory()

    try:
        target = find_deployment_target(
            inventory,
            args.device,
        )

        rendered_path = render_device(
            target.hostname
        )

    except DeploymentSafetyError as exc:
        fail(str(exc))

    print_plan(
        target,
        rendered_path,
    )


if __name__ == "__main__":
    main()
