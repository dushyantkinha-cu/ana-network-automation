#!/usr/bin/env python3

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from automation.device_connection import (
    open_connection,
)


class AristaPreviewError(RuntimeError):
    """Raised when an Arista preview operation fails."""


@dataclass(frozen=True)
class AristaPreviewResult:
    session_name: str
    command_count: int
    diff: str


FORBIDDEN_COMMAND_PREFIXES = (
    "commit",
    "abort",
    "configure session",
    "write",
    "copy running-config",
)

CLI_ERROR_PATTERNS = (
    "% invalid input",
    "% unrecognized command",
    "% incomplete command",
    "% ambiguous command",
    "error:",
)

SENSITIVE_PATTERN = re.compile(
    r"(?i)"
    r"(password|secret|community|"
    r"snmp-server\s+user|"
    r"username\s+.*\s+secret|"
    r"auth\s+sha|priv\s+aes)"
)

CONTEXT_COMMAND_PATTERNS = (
    re.compile(r"^interface\s+\S+"),
    re.compile(r"^vlan\s+\S+"),
    re.compile(r"^ipv6 prefix-list\s+\S+$"),
    re.compile(r"^route-map\s+\S+"),
    re.compile(r"^router isis\s+\S+"),
    re.compile(r"^router ospf\s+\S+"),
    re.compile(r"^ipv6 router ospf\s+\S+"),
    re.compile(
        r"^address-family\s+"
        r"(?:ipv4|ipv6)"
        r"(?:\s+unicast)?$"
    ),
)


def is_context_command(
    command,
    next_indent=None,
    current_indent=0,
):
    if (
        next_indent is not None
        and next_indent > current_indent
    ):
        return True

    return any(
        pattern.match(command)
        for pattern in CONTEXT_COMMAND_PATTERNS
    )


def rendered_config_to_commands(rendered):
    parsed = []

    for raw_line in rendered.splitlines():
        stripped = raw_line.strip()

        if not stripped:
            continue

        if stripped == "!":
            continue

        if "\t" in raw_line:
            raise AristaPreviewError(
                "Rendered EOS configuration "
                "must use spaces for indentation."
            )

        indent = (
            len(raw_line)
            - len(raw_line.lstrip(" "))
        )

        parsed.append(
            (
                indent,
                stripped,
            )
        )

    if not parsed:
        raise AristaPreviewError(
            "Rendered EOS configuration "
            "contains no commands."
        )

    commands = []
    context_stack = []

    for index, (
        indent,
        command,
    ) in enumerate(parsed):
        while (
            context_stack
            and indent <= context_stack[-1]
        ):
            commands.append("exit")
            context_stack.pop()

        commands.append(command)

        next_indent = None

        if index + 1 < len(parsed):
            next_indent = parsed[
                index + 1
            ][0]

        if is_context_command(
            command,
            next_indent=next_indent,
            current_indent=indent,
        ):
            context_stack.append(indent)

    while context_stack:
        commands.append("exit")
        context_stack.pop()

    return commands

def make_session_name(hostname):
    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    suffix = secrets.token_hex(3)

    safe_hostname = re.sub(
        r"[^A-Za-z0-9_-]",
        "-",
        hostname,
    )

    return (
        f"ANA-{safe_hostname}-"
        f"{timestamp}-{suffix}"
    )


def sanitize_diff(diff):
    sanitized = []

    for line in diff.splitlines():
        if SENSITIVE_PATTERN.search(line):
            prefix = ""

            if line.startswith("+"):
                prefix = "+"
            elif line.startswith("-"):
                prefix = "-"

            sanitized.append(
                f"{prefix}"
                "<redacted sensitive configuration>"
            )
        else:
            sanitized.append(line)

    return "\n".join(sanitized).rstrip()


def validate_preview_commands(commands):
    normalized = []

    for command in commands:
        if not isinstance(command, str):
            raise AristaPreviewError(
                "Preview commands must be strings."
            )

        command = command.strip()

        if not command:
            continue

        if "\n" in command or "\r" in command:
            raise AristaPreviewError(
                "Each preview command must be "
                "a single CLI line."
            )

        lowered = command.lower()

        if any(
            lowered == prefix
            or lowered.startswith(
                prefix + " "
            )
            for prefix in FORBIDDEN_COMMAND_PREFIXES
        ):
            raise AristaPreviewError(
                f"Forbidden preview command: "
                f"{command!r}"
            )

        normalized.append(command)

    if not normalized:
        raise AristaPreviewError(
            "No preview commands were provided."
        )

    return normalized


def check_cli_output(
    command,
    output,
):
    lowered = output.lower()

    for pattern in CLI_ERROR_PATTERNS:
        if pattern in lowered:
            raise AristaPreviewError(
                f"EOS rejected command "
                f"{command!r}."
            )

def preview_commands(
    device,
    commands,
    session_name=None,
    connection_factory=None,
):
    if device.get("platform") != "Arista EOS":
        raise AristaPreviewError(
            "Arista preview adapter requires "
            "platform 'Arista EOS'."
        )

    commands = validate_preview_commands(
        commands
    )

    if session_name is None:
        session_name = make_session_name(
            device.get(
                "hostname",
                "UNKNOWN",
            )
        )

    if connection_factory is None:
        connection_factory = open_connection

    connection = connection_factory(device)
    session_open = False

    try:
        session_command = (
            f"configure session "
            f"{session_name}"
        )

        output = connection.send_command_timing(
            session_command
        )

        check_cli_output(
            session_command,
            output,
        )

        session_open = True

        stage_output = connection.send_config_set(
            commands,
            enter_config_mode=False,
            exit_config_mode=False,
            cmd_verify=True,
        )

        check_cli_output(
            "staged configuration",
            stage_output,
        )

        diff_command = (
            "show session-config diff"
        )

        raw_diff = (
            connection.send_command_timing(
                diff_command
            )
        )

        check_cli_output(
            diff_command,
            raw_diff,
        )

        return AristaPreviewResult(
            session_name=session_name,
            command_count=len(commands),
            diff=sanitize_diff(raw_diff),
        )

    finally:
        try:
            if session_open:
                connection.send_command_timing(
                    "abort"
                )
        finally:
            connection.disconnect()

def preview_rendered_config(
    device,
    rendered_config,
    session_name=None,
    connection_factory=None,
):
    commands = rendered_config_to_commands(
        rendered_config
    )

    return preview_commands(
        device,
        commands,
        session_name=session_name,
        connection_factory=connection_factory,
    )

class AristaApplyError(RuntimeError):
    """Raised when an Arista apply transaction fails."""


@dataclass(frozen=True)
class AristaApplyResult:
    session_name: str
    command_count: int
    diff: str
    validation_passed: bool
    confirmed: bool
    persisted: bool
    rolled_back: bool


COMMIT_TIMER_PATTERN = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})$"
)


def validate_commit_timer(commit_timer):
    if not isinstance(commit_timer, str):
        raise AristaApplyError(
            "Commit timer must be a string."
        )

    match = COMMIT_TIMER_PATTERN.fullmatch(
        commit_timer
    )

    if match is None:
        raise AristaApplyError(
            "Commit timer must use HH:MM:SS "
            "format."
        )

    hours, minutes, seconds = (
        int(value)
        for value in match.groups()
    )

    if minutes > 59 or seconds > 59:
        raise AristaApplyError(
            "Commit timer contains an invalid "
            "minute or second value."
        )

    total_seconds = (
        hours * 3600
        + minutes * 60
        + seconds
    )

    if not 30 <= total_seconds <= 3600:
        raise AristaApplyError(
            "Commit timer must be between "
            "30 seconds and 1 hour."
        )

    return commit_timer


def validate_apply_commands(commands):
    try:
        return validate_preview_commands(commands)
    except AristaPreviewError as exc:
        raise AristaApplyError(
            str(exc)
        ) from exc


def check_apply_cli_output(
    command,
    output,
):
    try:
        check_cli_output(
            command,
            output,
        )
    except AristaPreviewError as exc:
        raise AristaApplyError(
            str(exc)
        ) from exc


def abort_apply_session(
    connection,
    session_name,
    timer_started,
):
    if timer_started:
        command = (
            f"configure session "
            f"{session_name} abort"
        )
    else:
        command = "abort"

    output = connection.send_command_timing(
        command
    )

    check_apply_cli_output(
        command,
        output,
    )


def apply_commands(
    device,
    commands,
    post_validate,
    session_name=None,
    commit_timer="00:02:00",
    connection_factory=None,
):
    if device.get("platform") != "Arista EOS":
        raise AristaApplyError(
            "Arista apply adapter requires "
            "platform 'Arista EOS'."
        )

    if not callable(post_validate):
        raise AristaApplyError(
            "post_validate must be callable."
        )

    commands = validate_apply_commands(
        commands
    )

    commit_timer = validate_commit_timer(
        commit_timer
    )

    if session_name is None:
        session_name = make_session_name(
            device.get(
                "hostname",
                "UNKNOWN",
            )
        )

    if connection_factory is None:
        connection_factory = open_connection

    connection = connection_factory(device)

    session_entered = False
    timer_started = False
    transaction_finished = False

    try:
        session_command = (
            f"configure session "
            f"{session_name}"
        )

        output = connection.send_command_timing(
            session_command
        )

        check_apply_cli_output(
            session_command,
            output,
        )

        session_entered = True

        stage_output = connection.send_config_set(
            commands,
            enter_config_mode=False,
            exit_config_mode=False,
            cmd_verify=True,
        )

        check_apply_cli_output(
            "staged configuration",
            stage_output,
        )

        diff_command = (
            "show session-config diff"
        )

        raw_diff = (
            connection.send_command_timing(
                diff_command
            )
        )

        check_apply_cli_output(
            diff_command,
            raw_diff,
        )

        diff = sanitize_diff(raw_diff)

        timer_command = (
            f"commit timer {commit_timer}"
        )

        timer_output = (
            connection.send_command_timing(
                timer_command
            )
        )

        check_apply_cli_output(
            timer_command,
            timer_output,
        )

        timer_started = True

        try:
            validation_passed = post_validate()
        except Exception as exc:
            raise AristaApplyError(
                "Post-deployment validation "
                "raised an exception."
            ) from exc

        if not isinstance(
            validation_passed,
            bool,
        ):
            raise AristaApplyError(
                "Post-deployment validation "
                "must return True or False."
            )

        if not validation_passed:
            abort_apply_session(
                connection,
                session_name,
                timer_started=True,
            )

            transaction_finished = True

            return AristaApplyResult(
                session_name=session_name,
                command_count=len(commands),
                diff=diff,
                validation_passed=False,
                confirmed=False,
                persisted=False,
                rolled_back=True,
            )

        confirm_command = (
            f"configure session "
            f"{session_name} commit"
        )

        confirm_output = (
            connection.send_command_timing(
                confirm_command
            )
        )

        check_apply_cli_output(
            confirm_command,
            confirm_output,
        )

        transaction_finished = True

        persist_command = (
            "copy running-config startup-config"
        )

        try:
            persist_output = (
                connection.send_command_timing(
                    persist_command
                )
            )

            check_apply_cli_output(
                persist_command,
                persist_output,
            )
        except Exception as exc:
            raise AristaApplyError(
                "Running configuration was "
                "confirmed, but persistence to "
                "startup-config failed. Manual "
                "recovery is required."
            ) from exc

        return AristaApplyResult(
            session_name=session_name,
            command_count=len(commands),
            diff=diff,
            validation_passed=True,
            confirmed=True,
            persisted=True,
            rolled_back=False,
        )

    except Exception as exc:
        if (
            session_entered
            and not transaction_finished
        ):
            try:
                abort_apply_session(
                    connection,
                    session_name,
                    timer_started=timer_started,
                )
            except Exception as rollback_exc:
                raise AristaApplyError(
                    "Arista apply failed and the "
                    "automatic rollback attempt "
                    "also failed. The commit timer "
                    "may still be active."
                ) from rollback_exc

        if isinstance(
            exc,
            AristaApplyError,
        ):
            raise

        raise AristaApplyError(
            "Arista apply transaction failed."
        ) from exc

    finally:
        connection.disconnect()


def apply_rendered_config(
    device,
    rendered_config,
    post_validate,
    session_name=None,
    commit_timer="00:02:00",
    connection_factory=None,
):
    try:
        commands = rendered_config_to_commands(
            rendered_config
        )
    except AristaPreviewError as exc:
        raise AristaApplyError(
            str(exc)
        ) from exc

    return apply_commands(
        device,
        commands,
        post_validate=post_validate,
        session_name=session_name,
        commit_timer=commit_timer,
        connection_factory=connection_factory,
    )
