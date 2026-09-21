from dataclasses import dataclass
from datetime import datetime, timezone
import re
import secrets

from automation.device_connection import (
    open_connection,
)


class NokiaPreviewError(RuntimeError):
    """Raised when an SR Linux preview cannot proceed safely."""


@dataclass(frozen=True)
class NokiaPreviewResult:
    candidate_name: str
    command_count: int
    validation_passed: bool
    diff: str
    discarded: bool


FORBIDDEN_COMMAND_PREFIXES = (
    "commit",
    "discard",
    "enter",
    "quit",
    "logout",
    "tools",
    "environment",
)

CLI_ERROR_PATTERNS = (
    re.compile(
        r"(?im)^\s*error\b"
    ),
    re.compile(
        r"(?im)^\s*invalid\b"
    ),
    re.compile(
        r"(?im)^\s*failed\b"
    ),
    re.compile(
        r"(?im)^\s*unknown command\b"
    ),
    re.compile(
        r"(?im)^\s*syntax error\b"
    ),
)

SENSITIVE_LINE_PATTERN = re.compile(
    r"(?i)\b("
    r"password|"
    r"secret|"
    r"community|"
    r"authentication-key|"
    r"privacy-key|"
    r"private-key"
    r")\b"
)


def rendered_config_to_commands(
    rendered_config,
):
    if not isinstance(rendered_config, str):
        raise NokiaPreviewError(
            "Rendered configuration must be text."
        )

    commands = []
    depth = 0

    for line_number, raw_line in enumerate(
        rendered_config.splitlines(),
        start=1,
    ):
        if "\t" in raw_line:
            raise NokiaPreviewError(
                f"Line {line_number}: tabs are "
                "not allowed in rendered config."
            )

        line = raw_line.strip()

        if not line:
            continue

        if line == "}":
            if depth <= 0:
                raise NokiaPreviewError(
                    f"Line {line_number}: unmatched "
                    "closing brace."
                )

            commands.append("exit")
            depth -= 1
            continue

        if line.endswith("{"):
            command = line[:-1].rstrip()

            if not command:
                raise NokiaPreviewError(
                    f"Line {line_number}: empty "
                    "configuration context."
                )

            commands.append(command)
            depth += 1
            continue

        if "{" in line or "}" in line:
            raise NokiaPreviewError(
                f"Line {line_number}: malformed "
                "brace placement."
            )

        commands.append(line)

    if depth != 0:
        raise NokiaPreviewError(
            "Rendered configuration contains "
            "unclosed configuration contexts."
        )

    if not commands:
        raise NokiaPreviewError(
            "Rendered configuration produced "
            "no CLI commands."
        )

    return commands


def validate_preview_commands(
    commands,
):
    validated = []

    for command in commands:
        if not isinstance(command, str):
            raise NokiaPreviewError(
                "Preview commands must be strings."
            )

        command = command.strip()

        if not command:
            continue

        if "\n" in command or "\r" in command:
            raise NokiaPreviewError(
                "Each preview command must be "
                "a single CLI line."
            )

        lowered = command.lower()

        if any(
            lowered == prefix
            or lowered.startswith(prefix + " ")
            for prefix
            in FORBIDDEN_COMMAND_PREFIXES
        ):
            raise NokiaPreviewError(
                f"Forbidden preview command: "
                f"{command!r}"
            )

        validated.append(command)

    if not validated:
        raise NokiaPreviewError(
            "No preview commands were provided."
        )

    return validated


def check_cli_output(
    action,
    output,
):
    output = output or ""

    if any(
        pattern.search(output)
        for pattern in CLI_ERROR_PATTERNS
    ):
        raise NokiaPreviewError(
            "SR Linux rejected the operation "
            f"during {action}."
        )


def sanitize_diff(
    diff,
):
    sanitized = []

    for line in (diff or "").splitlines():
        stripped = line.strip()

        if (
            stripped.startswith("--{")
            and "}--[" in stripped
            and stripped.endswith("]--")
        ):
            continue

        if SENSITIVE_LINE_PATTERN.search(line):
            prefix = ""

            if line.startswith(("+", "-")):
                prefix = line[0]

            sanitized.append(
                f"{prefix}<redacted sensitive line>"
            )
        else:
            sanitized.append(line)

    return "\n".join(sanitized)


def make_candidate_name(
    device,
):
    hostname = str(
        device.get("hostname", "device")
    )

    hostname = re.sub(
        r"[^A-Za-z0-9_-]",
        "-",
        hostname,
    )

    hostname = hostname[:24] or "device"

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    suffix = secrets.token_hex(2)

    return (
        f"ANA-{hostname}-"
        f"{timestamp}-{suffix}"
    )


def validate_candidate_name(
    candidate_name,
):
    if not isinstance(candidate_name, str):
        raise NokiaPreviewError(
            "Candidate name must be text."
        )

    if not re.fullmatch(
        r"[A-Za-z0-9_-]+",
        candidate_name,
    ):
        raise NokiaPreviewError(
            "Candidate name contains unsupported "
            "characters."
        )

    return candidate_name


def enter_candidate(
    connection,
    candidate_name,
):
    command = (
        "enter candidate exclusive name "
        f"{candidate_name}"
    )

    output = connection.send_command_timing(
        command
    )

    check_cli_output(
        "candidate entry",
        output,
    )

    state_output = output
    lowered = output.lower()

    if (
        "are you sure" in lowered
        or "(y/[n])" in lowered
    ):
        state_output = (
            connection.send_command_timing(
                "y"
            )
        )

        check_cli_output(
            "candidate entry confirmation",
            state_output,
        )

    state_lower = state_output.lower()

    if (
        "candidate" not in state_lower
        or "exclusive" not in state_lower
        or candidate_name.lower()
        not in state_lower
    ):
        raise NokiaPreviewError(
            "SR Linux did not enter the expected "
            "named candidate."
        )


def validate_candidate(
    connection,
):
    output = connection.send_command_timing(
        "commit validate"
    )

    check_cli_output(
        "candidate validation",
        output,
    )


def collect_candidate_diff(
    connection,
):
    output = connection.send_command(
        "diff /",
        read_timeout=60,
    )

    return sanitize_diff(output)


def discard_candidate(
    connection,
):
    output = connection.send_command_timing(
        "discard now"
    )

    check_cli_output(
        "candidate discard",
        output,
    )

    if "running" not in output.lower():
        raise NokiaPreviewError(
            "SR Linux did not return to running "
            "mode after candidate discard."
        )


def preview_commands(
    device,
    commands,
    connection_factory=None,
    candidate_name=None,
):
    if (
        device.get("platform")
        != "Nokia SR Linux"
    ):
        raise NokiaPreviewError(
            "Nokia preview adapter requires "
            "platform 'Nokia SR Linux'."
        )

    commands = validate_preview_commands(
        commands
    )

    if candidate_name is None:
        candidate_name = make_candidate_name(
            device
        )

    candidate_name = validate_candidate_name(
        candidate_name
    )

    if connection_factory is None:
        connection_factory = open_connection

    connection = connection_factory(device)

    candidate_entered = False
    discarded = False

    try:
        enter_candidate(
            connection,
            candidate_name,
        )

        candidate_entered = True

        stage_output = (
            connection.send_config_set(
                commands,
                enter_config_mode=False,
                exit_config_mode=False,
                cmd_verify=True,
            )
        )

        check_cli_output(
            "configuration staging",
            stage_output,
        )

        validate_candidate(
            connection
        )

        diff = collect_candidate_diff(
            connection
        )

        discard_candidate(
            connection
        )

        discarded = True

        return NokiaPreviewResult(
            candidate_name=candidate_name,
            command_count=len(commands),
            validation_passed=True,
            diff=diff,
            discarded=True,
        )

    except Exception as preview_error:
        if (
            candidate_entered
            and not discarded
        ):
            try:
                discard_candidate(
                    connection
                )
            except Exception as discard_error:
                raise NokiaPreviewError(
                    "Nokia preview failed and "
                    "candidate cleanup also failed: "
                    f"{discard_error}"
                ) from preview_error

        raise

    finally:
        connection.disconnect()


def preview_rendered_config(
    device,
    rendered_config,
    connection_factory=None,
    candidate_name=None,
):
    commands = rendered_config_to_commands(
        rendered_config
    )

    return preview_commands(
        device,
        commands,
        connection_factory=connection_factory,
        candidate_name=candidate_name,
    )
