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
