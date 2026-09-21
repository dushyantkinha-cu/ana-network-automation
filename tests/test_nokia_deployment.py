import pytest

from automation.deployment.nokia import (
    NokiaPreviewError,
    preview_commands,
    preview_rendered_config,
    rendered_config_to_commands,
    sanitize_diff,
    validate_preview_commands,
)


NOKIA_DEVICE = {
    "hostname": "S4",
    "platform": "Nokia SR Linux",
    "management_ip": "172.20.20.4/24",
}


class FakeConnection:
    def __init__(
        self,
        enter_output=None,
        confirmation_output="",
        stage_output="",
        validate_output=(
            "Candidate configuration "
            "validated successfully"
        ),
        diff_output="",
        discard_output=None,
    ):
        self.enter_output = enter_output
        self.confirmation_output = (
            confirmation_output
        )
        self.stage_output = stage_output
        self.validate_output = (
            validate_output
        )
        self.diff_output = diff_output
        self.discard_output = (
            discard_output
        )

        self.mode = "running"
        self.candidate_name = None
        self.pending_candidate = None

        self.timing_commands = []
        self.commands = []
        self.config_set_calls = []
        self.disconnected = False

    @staticmethod
    def _has_error(
        output,
    ):
        lowered = (
            output or ""
        ).strip().lower()

        return lowered.startswith(
            (
                "error",
                "invalid",
                "failed",
                "unknown command",
                "syntax error",
            )
        )

    def send_command_timing(
        self,
        command,
    ):
        self.timing_commands.append(
            command
        )

        prefix = (
            "enter candidate exclusive name "
        )

        if command.startswith(prefix):
            candidate_name = (
                command[len(prefix):]
            )

            self.pending_candidate = (
                candidate_name
            )

            if self.enter_output is None:
                self.mode = "candidate"
                self.candidate_name = (
                    candidate_name
                )

                return (
                    "\n--{ + candidate "
                    "shared-exclusive "
                    f"{candidate_name} "
                    "}--[  ]--"
                )

            if (
                not self._has_error(
                    self.enter_output
                )
                and "are you sure"
                not in self.enter_output.lower()
                and "(y/[n])"
                not in self.enter_output.lower()
            ):
                self.mode = "candidate"
                self.candidate_name = (
                    candidate_name
                )

            return self.enter_output

        if command == "y":
            if (
                self.pending_candidate
                and not self._has_error(
                    self.confirmation_output
                )
            ):
                self.mode = "candidate"
                self.candidate_name = (
                    self.pending_candidate
                )

            return self.confirmation_output

        if command == "commit validate":
            return self.validate_output

        if command == "discard now":
            output = self.discard_output

            if output is None:
                output = (
                    "Discarded candidate "
                    "configuration.\n\n"
                    "--{ + running }--[  ]--"
                )

            if not self._has_error(
                output
            ):
                self.mode = "running"
                self.candidate_name = None

            return output

        return ""

    def send_config_set(
        self,
        commands,
        **kwargs,
    ):
        commands = list(commands)

        self.config_set_calls.append(
            (
                commands,
                kwargs,
            )
        )

        return self.stage_output

    def send_command(
        self,
        command,
        **kwargs,
    ):
        self.commands.append(command)

        if command == "diff /":
            return self.diff_output

        return ""

    def find_prompt(self):
        if self.mode == "candidate":
            return (
                "--{ candidate exclusive "
                f"{self.candidate_name} "
                "}--[ ]--"
            )

        return "--{ running }--[ ]--"

    def disconnect(self):
        self.disconnected = True


def test_translates_hierarchical_config():
    rendered = """\
interface ethernet-1/1 {
    admin-state enable
    subinterface 0 {
        ipv4 {
            admin-state enable
            address 10.1.0.1/31 {
            }
        }
    }
}
"""

    assert rendered_config_to_commands(
        rendered
    ) == [
        "interface ethernet-1/1",
        "admin-state enable",
        "subinterface 0",
        "ipv4",
        "admin-state enable",
        "address 10.1.0.1/31",
        "exit",
        "exit",
        "exit",
        "exit",
    ]


def test_translates_empty_list_object():
    rendered = """\
network-instance default {
    interface ethernet-1/1.0 {
    }
}
"""

    assert rendered_config_to_commands(
        rendered
    ) == [
        "network-instance default",
        "interface ethernet-1/1.0",
        "exit",
        "exit",
    ]


def test_ignores_blank_lines():
    rendered = """\
interface lo0 {
    admin-state enable
}

network-instance default {
    type default
}
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert "" not in commands


def test_rejects_unmatched_closing_brace():
    with pytest.raises(
        NokiaPreviewError,
        match="unmatched closing brace",
    ):
        rendered_config_to_commands(
            "}\n"
        )


def test_rejects_unclosed_context():
    with pytest.raises(
        NokiaPreviewError,
        match="unclosed",
    ):
        rendered_config_to_commands(
            "interface ethernet-1/1 {\n"
            "    admin-state enable\n"
        )


def test_rejects_malformed_braces():
    with pytest.raises(
        NokiaPreviewError,
        match="malformed brace placement",
    ):
        rendered_config_to_commands(
            "admin-state { enable }\n"
        )


def test_rejects_tabs():
    with pytest.raises(
        NokiaPreviewError,
        match="tabs",
    ):
        rendered_config_to_commands(
            "\tadmin-state enable\n"
        )


def test_rejects_empty_config():
    with pytest.raises(
        NokiaPreviewError,
        match="no CLI commands",
    ):
        rendered_config_to_commands(
            "\n\n"
        )


@pytest.mark.parametrize(
    "command",
    [
        "commit now",
        "commit stay",
        "commit confirmed",
        "discard now",
        "enter running",
        "enter candidate",
        "quit",
        "logout",
        "tools",
        "environment",
    ],
)
def test_rejects_control_commands(
    command,
):
    with pytest.raises(
        NokiaPreviewError,
        match="Forbidden preview command",
    ):
        validate_preview_commands(
            [command]
        )


def test_rejects_multiline_command():
    with pytest.raises(
        NokiaPreviewError,
        match="single CLI line",
    ):
        validate_preview_commands(
            [
                "interface ethernet-1/1\n"
                "admin-state enable"
            ]
        )


def test_sanitizes_sensitive_diff():
    diff = """\
+ system {
+     password super-secret
+     host-name S4
+ }
"""

    sanitized = sanitize_diff(
        diff
    )

    assert "super-secret" not in sanitized
    assert (
        "+<redacted sensitive line>"
        in sanitized
    )
    assert "host-name S4" in sanitized


def test_sanitize_diff_removes_srlinux_prompt():
    diff = """\
      interface ethernet-1/1 {
+         description STAGE7-TEST
      }

--{ +* candidate shared-exclusive ANA-S4-TEST }--[  ]--
"""

    sanitized = sanitize_diff(
        diff
    )

    assert "description STAGE7-TEST" in sanitized
    assert "candidate shared-exclusive" not in sanitized
    assert "--{" not in sanitized


def test_preview_candidate_lifecycle():
    connection = FakeConnection(
        diff_output=(
            "+ interface ethernet-1/1 {\n"
            "+     description TEST\n"
            "+ }\n"
        )
    )

    result = preview_commands(
        NOKIA_DEVICE,
        [
            "interface ethernet-1/1",
            "description TEST",
            "exit",
        ],
        connection_factory=(
            lambda device: connection
        ),
        candidate_name="ANA-S4-TEST",
    )

    assert (
        result.candidate_name
        == "ANA-S4-TEST"
    )

    assert result.command_count == 3
    assert (
        result.validation_passed
        is True
    )
    assert result.discarded is True

    assert connection.timing_commands == [
        (
            "enter candidate exclusive "
            "name ANA-S4-TEST"
        ),
        "commit validate",
        "discard now",
    ]

    assert connection.commands == [
        "diff /",
    ]

    assert len(
        connection.config_set_calls
    ) == 1

    commands, kwargs = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface ethernet-1/1",
        "description TEST",
        "exit",
    ]

    assert (
        kwargs["enter_config_mode"]
        is False
    )

    assert (
        kwargs["exit_config_mode"]
        is False
    )

    assert kwargs["cmd_verify"] is True
    assert connection.mode == "running"
    assert connection.disconnected is True


def test_preview_never_commits():
    connection = FakeConnection()

    preview_commands(
        NOKIA_DEVICE,
        ["interface ethernet-1/1", "exit"],
        connection_factory=(
            lambda device: connection
        ),
        candidate_name="ANA-S4-TEST",
    )

    all_commands = (
        connection.timing_commands
        + connection.commands
    )

    assert "commit now" not in all_commands
    assert "commit stay" not in all_commands
    assert (
        "commit confirmed"
        not in all_commands
    )

    assert "commit validate" in all_commands
    assert "discard now" in all_commands


def test_candidate_entry_failure_does_not_stage():
    connection = FakeConnection(
        enter_output=(
            "Error: candidate is locked"
        )
    )

    with pytest.raises(
        NokiaPreviewError,
        match="rejected",
    ):
        preview_commands(
            NOKIA_DEVICE,
            ["interface ethernet-1/1"],
            connection_factory=(
                lambda device: connection
            ),
            candidate_name="ANA-S4-TEST",
        )

    assert (
        connection.config_set_calls
        == []
    )

    assert (
        "discard now"
        not in connection.timing_commands
    )

    assert connection.disconnected is True


def test_stage_failure_still_discards():
    connection = FakeConnection(
        stage_output=(
            "Error: invalid configuration"
        )
    )

    with pytest.raises(
        NokiaPreviewError,
        match="rejected",
    ):
        preview_commands(
            NOKIA_DEVICE,
            ["interface ethernet-1/1"],
            connection_factory=(
                lambda device: connection
            ),
            candidate_name="ANA-S4-TEST",
        )

    assert (
        "discard now"
        in connection.timing_commands
    )

    assert connection.mode == "running"
    assert connection.disconnected is True


def test_validation_failure_still_discards():
    connection = FakeConnection(
        validate_output=(
            "Error: validation failed"
        )
    )

    with pytest.raises(
        NokiaPreviewError,
        match="rejected",
    ):
        preview_commands(
            NOKIA_DEVICE,
            ["interface ethernet-1/1"],
            connection_factory=(
                lambda device: connection
            ),
            candidate_name="ANA-S4-TEST",
        )

    assert (
        "discard now"
        in connection.timing_commands
    )

    assert connection.mode == "running"
    assert connection.disconnected is True


def test_discard_failure_is_detected():
    connection = FakeConnection(
        discard_output=(
            "Error: discard failed"
        )
    )

    with pytest.raises(
        NokiaPreviewError,
        match="cleanup also failed",
    ):
        preview_commands(
            NOKIA_DEVICE,
            ["interface ethernet-1/1"],
            connection_factory=(
                lambda device: connection
            ),
            candidate_name="ANA-S4-TEST",
        )

    assert connection.mode == "candidate"
    assert connection.disconnected is True


def test_rendered_preview_uses_translator():
    connection = FakeConnection()

    rendered = """\
interface ethernet-1/1 {
    admin-state enable
}
"""

    result = preview_rendered_config(
        NOKIA_DEVICE,
        rendered,
        connection_factory=(
            lambda device: connection
        ),
        candidate_name="ANA-S4-TEST",
    )

    commands, _ = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface ethernet-1/1",
        "admin-state enable",
        "exit",
    ]

    assert result.command_count == 3
    assert (
        result.validation_passed
        is True
    )
    assert result.discarded is True
