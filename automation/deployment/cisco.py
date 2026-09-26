#!/usr/bin/env python3

import re
import hashlib
from dataclasses import dataclass
from automation.device_connection import (
    open_connection,
)

class CiscoPreviewError(RuntimeError):
    """Raised when a Cisco preview operation fails."""


CONTEXT_COMMAND_PATTERNS = (
    re.compile(r"^interface\s+\S+"),
    re.compile(r"^route-map\s+\S+"),
    re.compile(r"^ip access-list\s+"),
    re.compile(r"^router\s+\S+"),
    re.compile(r"^address-family\s+\S+"),
)

VOLATILE_RUNNING_CONFIG_PATTERNS = (
    re.compile(r"^Building configuration\.\.\.$"),
    re.compile(
        r"^Current configuration\s*:"
    ),
    re.compile(
        r"^! Last configuration change "
    ),
    re.compile(
        r"^! NVRAM config last updated "
    ),
)

@dataclass(frozen=True)
class CiscoPrerequisiteResult:
    archive_path: str
    startup_archive_persistent: bool
    rollback_pending: bool

@dataclass(frozen=True)
class CiscoPreviewResult:
    command_count: int
    rollback_started: bool
    rollback_completed: bool
    before_hash: str
    after_hash: str
    config_restored: bool

ROLLBACK_TIMER_MINUTES = 5

CLI_ERROR_PATTERNS = (
    "% invalid input",
    "% incomplete command",
    "% ambiguous command",
    "% error",
    "error:",
)

REQUIRED_ARCHIVE_PATH = (
    "bootflash:ana-archive"
)

NO_ROLLBACK_PENDING = (
    "No Rollback Confirmed Change pending"
)

FORBIDDEN_COMMAND_PREFIXES = (
    "configure confirm",
    "configure revert",
    "configure terminal revert",
    "write",
    "copy running-config",
    "reload",
    "erase",
)

def check_cli_output(
    operation,
    output,
):
    lowered = output.lower()

    for pattern in CLI_ERROR_PATTERNS:
        if pattern in lowered:
            raise CiscoPreviewError(
                f"IOS-XE rejected "
                f"{operation!r}."
            )

def check_prerequisites_on_connection(
    connection,
):
    running_archive = connection.send_command(
        "show running-config "
        "| section ^archive"
    )

    archive_path = extract_archive_path(
        running_archive
    )

    if not archive_path:
        raise CiscoPreviewError(
            "Cisco configuration archive "
            "is not configured."
        )

    if archive_path != REQUIRED_ARCHIVE_PATH:
        raise CiscoPreviewError(
            "Cisco configuration archive "
            f"path is {archive_path!r}; "
            f"expected "
            f"{REQUIRED_ARCHIVE_PATH!r}."
        )

    rollback_state = connection.send_command(
        "show archive config rollback timer"
    )

    rollback_pending = (
        NO_ROLLBACK_PENDING
        not in rollback_state
    )

    if rollback_pending:
        raise CiscoPreviewError(
            "Another rollback-confirmed "
            "configuration change may "
            "already be pending."
        )

    startup_archive = connection.send_command(
        "show startup-config "
        "| section ^archive"
    )

    startup_path = extract_archive_path(
        startup_archive
    )

    return CiscoPrerequisiteResult(
        archive_path=archive_path,
        startup_archive_persistent=(
            startup_path
            == REQUIRED_ARCHIVE_PATH
        ),
        rollback_pending=False,
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

        if stripped.startswith("!"):
            continue

        if stripped == "end":
            continue

        if "\t" in raw_line:
            raise CiscoPreviewError(
                "Rendered IOS-XE configuration "
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
        raise CiscoPreviewError(
            "Rendered IOS-XE configuration "
            "contains no commands."
        )

    commands = []
    context_stack = []

    for index, (
        indent,
        command,
    ) in enumerate(parsed):
        if command == "exit-address-family":
            commands.append(command)

            if (
                context_stack
                and context_stack[-1] == indent
            ):
                context_stack.pop()
            else:
                raise CiscoPreviewError(
                    "Unexpected "
                    "'exit-address-family' "
                    "in rendered configuration."
                )

            continue

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

def extract_archive_path(output):
    for raw_line in output.splitlines():
        line = raw_line.strip()

        if line.startswith("path "):
            return line.split(
                None,
                1,
            )[1]

    return None

def check_preview_prerequisites(
    device,
    connection_factory=None,
):
    if device.get("platform") != "Cisco IOS-XE":
        raise CiscoPreviewError(
            "Cisco preview prerequisite check "
            "requires platform "
            "'Cisco IOS-XE'."
        )

    if connection_factory is None:
        connection_factory = open_connection

    connection = connection_factory(device)

    try:
        return check_prerequisites_on_connection(
            connection
        )
    finally:
        connection.disconnect()

def revert_preview(connection):
    command = "configure revert now"

    output = connection.send_command_timing(
        command
    )

    check_cli_output(
        command,
        output,
    )

    rollback_state = connection.send_command(
        "show archive config rollback timer"
    )

    if (
        NO_ROLLBACK_PENDING
        not in rollback_state
    ):
        raise CiscoPreviewError(
            "Cisco preview rollback did not "
            "complete successfully."
        )


def normalize_running_config(config):
    normalized = []

    for raw_line in config.splitlines():
        line = raw_line.rstrip()

        if any(
            pattern.match(line)
            for pattern
            in VOLATILE_RUNNING_CONFIG_PATTERNS
        ):
            continue

        normalized.append(line)

    return "\n".join(normalized).rstrip() + "\n"

def running_config_hash(config):
    normalized = normalize_running_config(
        config
    )

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()

def collect_running_config(
    connection,
):
    output = connection.send_command(
        "show running-config",
        read_timeout=60,
    )

    if not output.strip():
        raise CiscoPreviewError(
            "IOS-XE returned an empty "
            "running configuration."
        )

    return output

def validate_preview_commands(commands):
    normalized = []

    for command in commands:
        if not isinstance(command, str):
            raise CiscoPreviewError(
                "Preview commands must be strings."
            )

        command = command.strip()

        if not command:
            continue

        if "\n" in command or "\r" in command:
            raise CiscoPreviewError(
                "Each preview command must be "
                "a single CLI line."
            )

        lowered = command.lower()

        if any(
            lowered == prefix
            or lowered.startswith(prefix + " ")
            for prefix in FORBIDDEN_COMMAND_PREFIXES
        ):
            raise CiscoPreviewError(
                f"Forbidden preview command: "
                f"{command!r}"
            )

        normalized.append(command)

    if not normalized:
        raise CiscoPreviewError(
            "No preview commands were provided."
        )

    return normalized

def preview_commands(
    device,
    commands,
    connection_factory=None,
):
    if device.get("platform") != "Cisco IOS-XE":
        raise CiscoPreviewError(
            "Cisco preview adapter requires "
            "platform 'Cisco IOS-XE'."
        )

    commands = validate_preview_commands(
        commands
    )

    if connection_factory is None:
        connection_factory = open_connection

    connection = connection_factory(device)
    rollback_started = False

    try:
        check_prerequisites_on_connection(
            connection
        )

        before_config = collect_running_config(
            connection
        )
        before_hash = running_config_hash(
            before_config
        )

        timer_command = (
            "configure terminal revert timer "
            f"{ROLLBACK_TIMER_MINUTES}"
        )

        output = connection.send_command_timing(
            timer_command
        )

        check_cli_output(
            timer_command,
            output,
        )

        rollback_started = True

        try:
            stage_output = (
                connection.send_config_set(
                    commands,
                    enter_config_mode=False,
                    exit_config_mode=True,
                    cmd_verify=True,
                )
            )

            check_cli_output(
                "staged configuration",
                stage_output,
            )

            rollback_state = (
                connection.send_command(
                    "show archive config "
                    "rollback timer"
                )
            )

            if (
                NO_ROLLBACK_PENDING
                in rollback_state
            ):
                raise CiscoPreviewError(
                    "Rollback protection was not "
                    "active after staging."
                )

        except Exception as preview_error:
            try:
                revert_preview(connection)
            except Exception as rollback_error:
                raise CiscoPreviewError(
                    "Cisco preview failed and "
                    "rollback verification also "
                    f"failed: {rollback_error}"
                ) from preview_error

            raise

        revert_preview(connection)

        after_config = collect_running_config(
            connection
        )
        after_hash = running_config_hash(
            after_config
        )
        config_restored = (
            before_hash == after_hash
        )

        if not config_restored:
            raise CiscoPreviewError(
                "IOS-XE running configuration did "
                "not return to its pre-preview "
                "state after rollback."
            )

        return CiscoPreviewResult(
            command_count=len(commands),
            rollback_started=rollback_started,
            rollback_completed=True,
            before_hash=before_hash,
            after_hash=after_hash,
            config_restored=config_restored,
        )

    finally:
        connection.disconnect()

def preview_rendered_config(
    device,
    rendered_config,
    connection_factory=None,
):
    commands = rendered_config_to_commands(
        rendered_config
    )

    return preview_commands(
        device,
        commands,
        connection_factory=connection_factory,
    )


class CiscoApplyError(RuntimeError):
    """Raised when a Cisco apply transaction fails."""


@dataclass(frozen=True)
class CiscoApplyResult:
    command_count: int
    validation_passed: bool
    confirmed: bool
    persisted: bool
    rolled_back: bool


def validate_apply_commands(commands):
    try:
        return validate_preview_commands(commands)
    except CiscoPreviewError as exc:
        raise CiscoApplyError(
            str(exc)
        ) from exc


def check_apply_cli_output(
    operation,
    output,
):
    try:
        check_cli_output(
            operation,
            output,
        )
    except CiscoPreviewError as exc:
        raise CiscoApplyError(
            str(exc)
        ) from exc


def check_apply_prerequisites_on_connection(
    connection,
):
    try:
        return check_prerequisites_on_connection(
            connection
        )
    except CiscoPreviewError as exc:
        raise CiscoApplyError(
            str(exc)
        ) from exc


def revert_apply(connection):
    try:
        revert_preview(connection)
    except CiscoPreviewError as exc:
        raise CiscoApplyError(
            str(exc)
        ) from exc


def confirm_apply(connection):
    command = "configure confirm"

    output = connection.send_command_timing(
        command
    )

    check_apply_cli_output(
        command,
        output,
    )

    rollback_state = connection.send_command(
        "show archive config rollback timer"
    )

    if (
        NO_ROLLBACK_PENDING
        not in rollback_state
    ):
        raise CiscoApplyError(
            "Cisco apply confirmation did not "
            "clear rollback protection."
        )


def apply_commands(
    device,
    commands,
    post_validate,
    connection_factory=None,
):
    if device.get("platform") != "Cisco IOS-XE":
        raise CiscoApplyError(
            "Cisco apply adapter requires "
            "platform 'Cisco IOS-XE'."
        )

    if not callable(post_validate):
        raise CiscoApplyError(
            "post_validate must be callable."
        )

    commands = validate_apply_commands(
        commands
    )

    if connection_factory is None:
        connection_factory = open_connection

    connection = connection_factory(device)

    rollback_started = False
    transaction_finished = False

    try:
        check_apply_prerequisites_on_connection(
            connection
        )

        timer_command = (
            "configure terminal revert timer "
            f"{ROLLBACK_TIMER_MINUTES}"
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

        rollback_started = True

        stage_output = connection.send_config_set(
            commands,
            enter_config_mode=False,
            exit_config_mode=True,
            cmd_verify=True,
        )

        check_apply_cli_output(
            "staged configuration",
            stage_output,
        )

        rollback_state = connection.send_command(
            "show archive config rollback timer"
        )

        if (
            NO_ROLLBACK_PENDING
            in rollback_state
        ):
            raise CiscoApplyError(
                "Rollback protection was not "
                "active after staging."
            )

        try:
            validation_passed = post_validate()
        except Exception as exc:
            raise CiscoApplyError(
                "Post-deployment validation "
                "raised an exception."
            ) from exc

        if not isinstance(
            validation_passed,
            bool,
        ):
            raise CiscoApplyError(
                "Post-deployment validation "
                "must return True or False."
            )

        if not validation_passed:
            revert_apply(connection)

            transaction_finished = True

            return CiscoApplyResult(
                command_count=len(commands),
                validation_passed=False,
                confirmed=False,
                persisted=False,
                rolled_back=True,
            )

        confirm_apply(connection)

        transaction_finished = True

        try:
            persist_output = (
                connection.save_config()
            )

            check_apply_cli_output(
                "persistence to startup-config",
                persist_output,
            )

        except Exception as exc:
            raise CiscoApplyError(
                "Running configuration was "
                "confirmed, but persistence to "
                "startup-config failed. Manual "
                "recovery is required."
            ) from exc

        return CiscoApplyResult(
            command_count=len(commands),
            validation_passed=True,
            confirmed=True,
            persisted=True,
            rolled_back=False,
        )

    except Exception as exc:
        if (
            rollback_started
            and not transaction_finished
        ):
            try:
                revert_apply(connection)
            except Exception as rollback_exc:
                raise CiscoApplyError(
                    "Cisco apply failed and the "
                    "automatic rollback attempt "
                    "also failed. The revert timer "
                    "may still be active."
                ) from rollback_exc

        if isinstance(
            exc,
            CiscoApplyError,
        ):
            raise

        raise CiscoApplyError(
            "Cisco apply transaction failed."
        ) from exc

    finally:
        connection.disconnect()


def apply_rendered_config(
    device,
    rendered_config,
    post_validate,
    connection_factory=None,
):
    try:
        commands = rendered_config_to_commands(
            rendered_config
        )
    except CiscoPreviewError as exc:
        raise CiscoApplyError(
            str(exc)
        ) from exc

    return apply_commands(
        device,
        commands,
        post_validate=post_validate,
        connection_factory=connection_factory,
    )
