#!/usr/bin/env python3

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.deployment.arista import (  # noqa: E402
    AristaApplyError,
    AristaPreviewError,
    apply_rendered_config as apply_arista,
    preview_rendered_config as preview_arista,
)

from automation.deployment.cisco import (  # noqa: E402
    CiscoApplyError,
    CiscoPreviewError,
    apply_rendered_config as apply_cisco,
    preview_rendered_config as preview_cisco,
)

from automation.deployment.nokia import (  # noqa: E402
    NokiaApplyError,
    NokiaPreviewError,
    apply_rendered_config as apply_nokia,
    preview_rendered_config as preview_nokia,
)

from automation.deployment.common import (  # noqa: E402
    DeploymentSafetyError,
    find_deployment_target,
    validate_apply_confirmation,
)

from automation.render_config import (  # noqa: E402
    load_inventory,
)


RENDER_SCRIPT = (
    REPO_ROOT
    / "automation"
    / "render_config.py"
)

VALIDATION_SCRIPT = (
    REPO_ROOT
    / "automation"
    / "run_validation.py"
)

GENERATED_CONFIG_DIR = (
    REPO_ROOT
    / "generated-configs"
)

DEPLOYMENT_REPORT_DIR = (
    REPO_ROOT
    / "deployment-reports"
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


def get_device_record(
    inventory,
    hostname,
):
    for device in inventory.get(
        "devices",
        [],
    ):
        if device.get("hostname") == hostname:
            return device

    raise DeploymentSafetyError(
        f"{hostname}: device record disappeared "
        f"from inventory."
    )


def run_post_validation(hostname):
    result = subprocess.run(
        [
            sys.executable,
            str(VALIDATION_SCRIPT),
            "--device",
            hostname,
            "--details",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout.rstrip())

    if result.stderr:
        print(
            result.stderr.rstrip(),
            file=sys.stderr,
        )

    return result.returncode == 0


def dispatch_apply(
    target,
    device,
    rendered_config,
    post_validate,
):
    if target.adapter == "arista":
        return apply_arista(
            device,
            rendered_config,
            post_validate=post_validate,
        )

    if target.adapter == "cisco":
        return apply_cisco(
            device,
            rendered_config,
            post_validate=post_validate,
        )

    if target.adapter == "nokia":
        return apply_nokia(
            device,
            rendered_config,
            post_validate=post_validate,
        )

    raise DeploymentSafetyError(
        f"{target.hostname}: apply is not "
        f"implemented for adapter "
        f"{target.adapter!r}."
    )


def normalize_apply_result(
    target,
    result,
):
    if target.adapter == "arista":
        normalized = {
            "transaction_name": result.session_name,
            "command_count": result.command_count,
            "sanitized_diff": result.diff,
            "validation_passed": (
                result.validation_passed
            ),
            "committed": result.confirmed,
            "persisted": result.persisted,
            "rolled_back": result.rolled_back,
        }

    elif target.adapter == "cisco":
        normalized = {
            "transaction_name": None,
            "command_count": result.command_count,
            "sanitized_diff": None,
            "validation_passed": (
                result.validation_passed
            ),
            "committed": result.confirmed,
            "persisted": result.persisted,
            "rolled_back": result.rolled_back,
        }

    elif target.adapter == "nokia":
        normalized = {
            "transaction_name": (
                result.candidate_name
            ),
            "command_count": result.command_count,
            "sanitized_diff": result.diff,
            "validation_passed": (
                result.validation_passed
            ),
            "committed": result.accepted,
            "persisted": result.persisted,
            "rolled_back": result.rejected,
        }

    else:
        raise DeploymentSafetyError(
            f"{target.hostname}: cannot normalize "
            f"apply result for adapter "
            f"{target.adapter!r}."
        )

    normalized["error"] = None

    if (
        normalized["validation_passed"]
        and normalized["committed"]
        and normalized["persisted"]
        and not normalized["rolled_back"]
    ):
        normalized["status"] = "success"

    elif (
        not normalized["validation_passed"]
        and not normalized["committed"]
        and not normalized["persisted"]
        and normalized["rolled_back"]
    ):
        normalized["status"] = "rolled_back"

    else:
        normalized["status"] = "failed"

    return normalized


def normalize_apply_error(
    target,
    error,
):
    return {
        "transaction_name": None,
        "command_count": None,
        "sanitized_diff": None,
        "validation_passed": None,
        "committed": None,
        "persisted": None,
        "rolled_back": None,
        "status": "failed",
        "error": str(error),
    }


def execute_apply(
    target,
    device,
    rendered_path,
    rendered_config,
):
    def post_validate():
        return run_post_validation(
            target.hostname
        )

    try:
        result = dispatch_apply(
            target,
            device,
            rendered_config,
            post_validate,
        )

    except (
        AristaApplyError,
        CiscoApplyError,
        NokiaApplyError,
        DeploymentSafetyError,
    ) as exc:
        normalized = normalize_apply_error(
            target,
            exc,
        )

    else:
        normalized = normalize_apply_result(
            target,
            result,
        )

    report_path = write_deployment_report(
        target,
        rendered_path,
        normalized,
    )

    return normalized, report_path


def write_deployment_report(
    target,
    rendered_path,
    normalized_result,
):
    report = {
        "timestamp_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "device": {
            "hostname": target.hostname,
            "netbox_device_id": target.device_id,
            "status": target.status,
            "platform": target.platform,
            "profile": target.config_profile,
            "adapter": target.adapter,
            "management_ip": target.management_ip,
        },
        "rendered_config": {
            "path": str(
                display_path(rendered_path)
            ),
            "line_count": count_lines(
                rendered_path
            ),
            "sha256": sha256_file(
                rendered_path
            ),
        },
        "mode": "apply",
        "safety_gates": "pass",
        "result": normalized_result,
        "overall_status": (
            normalized_result["status"]
        ),
    }

    DEPLOYMENT_REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    DEPLOYMENT_REPORT_DIR.chmod(0o700)

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    report_path = (
        DEPLOYMENT_REPORT_DIR
        / (
            f"deployment-{target.hostname}-"
            f"{timestamp}.json"
        )
    )

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    report_path.chmod(0o600)

    return report_path


def print_apply_result(
    target,
    normalized,
    report_path,
):
    print("=== DEPLOYMENT APPLY RESULT ===")
    print(
        f"Device:              "
        f"{target.hostname}"
    )
    print(
        f"Adapter:             "
        f"{target.adapter}"
    )
    print(
        "Mode:                APPLY"
    )
    print(
        f"Status:              "
        f"{normalized['status'].upper()}"
    )
    print(
        f"CLI commands staged: "
        f"{normalized['command_count']}"
    )
    print(
        f"Validation passed:   "
        f"{normalized['validation_passed']}"
    )
    print(
        f"Committed:           "
        f"{normalized['committed']}"
    )
    print(
        f"Persisted:           "
        f"{normalized['persisted']}"
    )
    print(
        f"Rolled back:         "
        f"{normalized['rolled_back']}"
    )

    if normalized["transaction_name"]:
        print(
            f"Transaction:         "
            f"{normalized['transaction_name']}"
        )

    if normalized["error"]:
        print(
            f"Error:               "
            f"{normalized['error']}"
        )

    print(
        f"Report:              "
        f"{report_path}"
    )


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


def print_preview_header(
    target,
    rendered_path,
):
    print(
        "=== DEPLOYMENT PREVIEW RESULT ==="
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
        "Mode:                PREVIEW"
    )


def print_arista_preview_result(
    target,
    rendered_path,
    preview,
):
    print_preview_header(
        target,
        rendered_path,
    )

    print(
        f"Session:             "
        f"{preview.session_name}"
    )

    print(
        f"CLI commands staged: "
        f"{preview.command_count}"
    )

    print()
    print(
        "=== SANITIZED SESSION DIFF ==="
    )

    if preview.diff:
        print(preview.diff)
    else:
        print("<no diff>")

    print()
    print(
        "Preview session was aborted."
    )

    print(
        "No configuration was committed."
    )


def print_cisco_preview_result(
    target,
    rendered_path,
    preview,
):
    print_preview_header(
        target,
        rendered_path,
    )

    print(
        f"CLI commands staged: "
        f"{preview.command_count}"
    )

    print(
        f"Rollback started:    "
        f"{preview.rollback_started}"
    )

    print(
        f"Rollback completed:  "
        f"{preview.rollback_completed}"
    )

    print(
        f"Config restored:     "
        f"{preview.config_restored}"
    )

    print(
        f"Before hash:         "
        f"{preview.before_hash}"
    )

    print(
        f"After hash:          "
        f"{preview.after_hash}"
    )

    print()
    print(
        "Preview changes were rolled back."
    )

    print(
        "No configuration was confirmed or saved."
    )


def print_nokia_preview_result(
    target,
    rendered_path,
    preview,
):
    print_preview_header(
        target,
        rendered_path,
    )

    print(
        f"Candidate:           "
        f"{preview.candidate_name}"
    )

    print(
        f"CLI commands staged: "
        f"{preview.command_count}"
    )

    print(
        f"Validation passed:   "
        f"{preview.validation_passed}"
    )

    print(
        f"Discarded:           "
        f"{preview.discarded}"
    )

    print()
    print(
        "=== SANITIZED CANDIDATE DIFF ==="
    )

    if preview.diff:
        print(preview.diff)
    else:
        print("<no diff>")

    print()
    print(
        "Preview candidate was discarded."
    )

    print(
        "No configuration was committed."
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Build or preview a safe deployment "
            "plan for one managed network device."
        )
    )

    parser.add_argument(
        "--device",
        required=True,
        help=(
            "Managed NetBox device hostname, "
            "for example R1."
        ),
    )

    mode_group = (
        parser.add_mutually_exclusive_group(
            required=True
        )
    )

    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Render and validate the deployment "
            "plan without contacting the device."
        ),
    )

    mode_group.add_argument(
        "--preview",
        action="store_true",
        help=(
            "Stage rendered configuration in a "
            "temporary candidate session, show "
            "the sanitized diff, and abort. "
            "No configuration is committed."
        ),
    )

    mode_group.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Perform an actual guarded deployment. "
            "Requires an exact --confirm-device "
            "value, vendor rollback protection, "
            "and post-deployment validation."
        ),
    )

    parser.add_argument(
        "--confirm-device",
        help=(
            "Exact device hostname confirmation "
            "required with --apply."
        ),
    )

    args = parser.parse_args()

    try:
        if args.apply:
            validate_apply_confirmation(
                args.device,
                args.confirm_device,
            )
        elif args.confirm_device is not None:
            raise DeploymentSafetyError(
                "--confirm-device is only valid "
                "with --apply."
            )
    except DeploymentSafetyError as exc:
        fail(str(exc))

    inventory = load_inventory()

    try:
        target = find_deployment_target(
            inventory,
            args.device,
        )

        rendered_path = render_device(
            target.hostname
        )

        device = get_device_record(
            inventory,
            target.hostname,
        )

        if (
            args.preview
            and target.adapter
            not in {
                "arista",
                "cisco",
                "nokia",
            }
        ):
            raise DeploymentSafetyError(
                f"{target.hostname}: preview is "
                f"not implemented for adapter "
                f"{target.adapter!r}."
            )

    except DeploymentSafetyError as exc:
        fail(str(exc))

    if args.dry_run:
        print_plan(
            target,
            rendered_path,
        )
        return

    rendered_config = rendered_path.read_text(
        encoding="utf-8"
    )

    if args.apply:
        normalized, report_path = execute_apply(
            target,
            device,
            rendered_path,
            rendered_config,
        )

        print_apply_result(
            target,
            normalized,
            report_path,
        )

        if normalized["status"] != "success":
            sys.exit(1)

        return

    if target.adapter == "arista":
        try:
            preview = preview_arista(
                device,
                rendered_config,
            )
        except AristaPreviewError as exc:
            fail(
                f"{target.hostname}: "
                f"preview failed: {exc}"
            )

        print_arista_preview_result(
            target,
            rendered_path,
            preview,
        )

    elif target.adapter == "cisco":
        try:
            preview = preview_cisco(
                device,
                rendered_config,
            )
        except CiscoPreviewError as exc:
            fail(
                f"{target.hostname}: "
                f"preview failed: {exc}"
            )

        print_cisco_preview_result(
            target,
            rendered_path,
            preview,
        )

    elif target.adapter == "nokia":
        try:
            preview = preview_nokia(
                device,
                rendered_config,
            )
        except NokiaPreviewError as exc:
            fail(
                f"{target.hostname}: "
                f"preview failed: {exc}"
            )

        print_nokia_preview_result(
            target,
            rendered_path,
            preview,
        )

if __name__ == "__main__":
    main()
