import pytest

from automation.deployment.arista import (
    AristaApplyError,
    AristaPreviewError,
    apply_commands,
    apply_rendered_config,
    preview_commands,
    preview_rendered_config,
    rendered_config_to_commands,
    sanitize_diff,
    validate_commit_timer,
    validate_preview_commands,
)

ARISTA_DEVICE = {
    "hostname": "R1",
    "platform": "Arista EOS",
    "management_ip": "172.20.20.14/24",
}

class FakeConnection:
    def __init__(
        self,
        diff="",
        reject_command=None,
        timing_reject_command=None,
    ):
        self.diff = diff
        self.reject_command = reject_command
        self.timing_reject_command = (
            timing_reject_command
        )
        self.commands = []
        self.config_set_calls = []
        self.disconnected = False

    def send_command_timing(
        self,
        command,
    ):
        self.commands.append(command)

        if (
            command
            == self.timing_reject_command
        ):
            return "% Invalid input"

        if command == "show session-config diff":
            return self.diff

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

        for command in commands:
            self.commands.append(command)

            if command == self.reject_command:
                return "% Invalid input"

        return ""

    def disconnect(self):
        self.disconnected = True

def test_preview_opens_session_and_aborts():
    connection = FakeConnection(
        diff="+hostname R1-PREVIEW"
    )

    result = preview_commands(
        ARISTA_DEVICE,
        ["hostname R1-PREVIEW"],
        session_name="ANA-TEST",
        connection_factory=lambda device: connection,
    )

    assert result.session_name == "ANA-TEST"
    assert result.command_count == 1

    assert connection.commands == [
        "configure session ANA-TEST",
        "hostname R1-PREVIEW",
        "show session-config diff",
        "abort",
    ]

    assert connection.disconnected is True

def test_rendered_config_to_commands_basic():
    rendered = """\
hostname R1
!
interface Ethernet1
   no switchport
   ip address 10.3.0.2/31
!
router ospf 1
   router-id 10.255.255.11
!
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "hostname R1",
        "interface Ethernet1",
        "no switchport",
        "ip address 10.3.0.2/31",
        "exit",
        "router ospf 1",
        "router-id 10.255.255.11",
        "exit",
    ]


def test_empty_isis_address_family_gets_exit():
    rendered = """\
router isis 1
   net 49.0001.0000.0000.0001.00
   address-family ipv4 unicast
   !
   address-family ipv6 unicast
      multi-topology
!
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "router isis 1",
        "net 49.0001.0000.0000.0001.00",
        "address-family ipv4 unicast",
        "exit",
        "address-family ipv6 unicast",
        "multi-topology",
        "exit",
        "exit",
    ]


def test_vlan_without_children_gets_exit():
    rendered = """\
vlan 10,20,30,50
!
interface Ethernet1
   no switchport
!
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "vlan 10,20,30,50",
        "exit",
        "interface Ethernet1",
        "no switchport",
        "exit",
    ]


def test_ipv6_prefix_list_context():
    rendered = """\
ipv6 prefix-list PL-TEST
   seq 10 permit 2001:db8::/32 le 64
!
hostname R1
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "ipv6 prefix-list PL-TEST",
        "seq 10 permit 2001:db8::/32 le 64",
        "exit",
        "hostname R1",
    ]


def test_nested_context_returns_to_parent():
    rendered = """\
router isis 1
   is-type level-1
   address-family ipv6 unicast
      redistribute ospfv3 match external
      multi-topology
   redistribute ospf include leaked match external
!
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "router isis 1",
        "is-type level-1",
        "address-family ipv6 unicast",
        "redistribute ospfv3 match external",
        "multi-topology",
        "exit",
        "redistribute ospf include leaked match external",
        "exit",
    ]


def test_rendered_tabs_are_rejected():
    rendered = (
        "interface Ethernet1\n"
        "\tno switchport\n"
    )

    with pytest.raises(
        AristaPreviewError,
        match="spaces for indentation",
    ):
        rendered_config_to_commands(
            rendered
        )


def test_empty_rendered_config_is_rejected():
    with pytest.raises(
        AristaPreviewError,
        match="contains no commands",
    ):
        rendered_config_to_commands(
            "\n!\n\n"
        )

def test_preview_never_commits():
    connection = FakeConnection()

    preview_commands(
        ARISTA_DEVICE,
        ["hostname R1"],
        session_name="ANA-TEST",
        connection_factory=lambda device: connection,
    )

    assert not any(
        command == "commit"
        or command.startswith("commit ")
        for command in connection.commands
    )


@pytest.mark.parametrize(
    "command",
    [
        "commit",
        "commit timer 5",
        "abort",
        "configure session BAD",
        "write memory",
        "copy running-config startup-config",
    ],
)
def test_forbidden_control_commands(
    command,
):
    with pytest.raises(
        AristaPreviewError,
        match="Forbidden preview command",
    ):
        validate_preview_commands(
            [command]
        )


def test_empty_command_list_is_denied():
    with pytest.raises(
        AristaPreviewError,
        match="No preview commands",
    ):
        validate_preview_commands([])


def test_multiline_command_is_denied():
    with pytest.raises(
        AristaPreviewError,
        match="single CLI line",
    ):
        validate_preview_commands(
            [
                "hostname R1\n"
                "hostname R2"
            ]
        )


def test_sensitive_diff_is_redacted():
    diff = "\n".join(
        [
            "+hostname R1",
            "+username admin secret abc123",
            "-snmp-server community private",
        ]
    )

    sanitized = sanitize_diff(diff)

    assert "+hostname R1" in sanitized
    assert "abc123" not in sanitized
    assert "private" not in sanitized

    assert (
        "+<redacted sensitive configuration>"
        in sanitized
    )

    assert (
        "-<redacted sensitive configuration>"
        in sanitized
    )


def test_rejected_command_still_aborts():
    connection = FakeConnection(
        reject_command="hostname BAD",
    )

    with pytest.raises(
        AristaPreviewError,
        match="EOS rejected",
    ):
        preview_commands(
            ARISTA_DEVICE,
            ["hostname BAD"],
            session_name="ANA-TEST",
            connection_factory=lambda device: connection,
        )

    assert "abort" in connection.commands
    assert connection.disconnected is True


def test_wrong_platform_is_denied():
    device = dict(ARISTA_DEVICE)
    device["platform"] = "Cisco IOS-XE"

    with pytest.raises(
        AristaPreviewError,
        match="requires platform",
    ):
        preview_commands(
            device,
            ["hostname R3"],
        )

def test_preview_uses_existing_session_mode():
    connection = FakeConnection()

    preview_commands(
        ARISTA_DEVICE,
        [
            "interface Ethernet1",
            "description TEST",
            "exit",
        ],
        session_name="ANA-TEST",
        connection_factory=lambda device: connection,
    )

    assert len(
        connection.config_set_calls
    ) == 1

    commands, kwargs = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface Ethernet1",
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


def test_preview_rendered_config_translates_hierarchy():
    connection = FakeConnection(
        diff="+description TEST"
    )

    rendered = """\
interface Ethernet1
   description TEST
!
"""

    result = preview_rendered_config(
        ARISTA_DEVICE,
        rendered,
        session_name="ANA-TEST",
        connection_factory=lambda device: connection,
    )

    commands, _ = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface Ethernet1",
        "description TEST",
        "exit",
    ]

    assert result.command_count == 3
    assert result.diff == "+description TEST"

    assert connection.commands[-1] == "abort"
    assert connection.disconnected is True

def test_apply_success_uses_commit_timer_and_persists():
    connection = FakeConnection(
        diff="+description STAGE7F"
    )

    def post_validate():
        connection.commands.append(
            "<post-validation>"
        )
        return True

    result = apply_commands(
        ARISTA_DEVICE,
        [
            "interface Ethernet1",
            "description STAGE7F",
            "exit",
        ],
        post_validate=post_validate,
        session_name="ANA-APPLY",
        connection_factory=lambda device: connection,
    )

    assert result.session_name == "ANA-APPLY"
    assert result.command_count == 3
    assert result.validation_passed is True
    assert result.confirmed is True
    assert result.persisted is True
    assert result.rolled_back is False
    assert result.diff == "+description STAGE7F"

    assert connection.commands == [
        "configure session ANA-APPLY",
        "interface Ethernet1",
        "description STAGE7F",
        "exit",
        "show session-config diff",
        "commit timer 00:02:00",
        "<post-validation>",
        "configure session ANA-APPLY commit",
        "copy running-config startup-config",
    ]

    assert "abort" not in connection.commands
    assert connection.disconnected is True


def test_apply_validation_failure_rolls_back():
    connection = FakeConnection(
        diff="+description BAD"
    )

    def post_validate():
        connection.commands.append(
            "<post-validation>"
        )
        return False

    result = apply_commands(
        ARISTA_DEVICE,
        ["hostname R1"],
        post_validate=post_validate,
        session_name="ANA-FAIL",
        connection_factory=lambda device: connection,
    )

    assert result.validation_passed is False
    assert result.confirmed is False
    assert result.persisted is False
    assert result.rolled_back is True

    assert (
        "configure session ANA-FAIL abort"
        in connection.commands
    )

    assert (
        "configure session ANA-FAIL commit"
        not in connection.commands
    )

    assert (
        "copy running-config startup-config"
        not in connection.commands
    )

    assert connection.disconnected is True


def test_apply_validation_exception_rolls_back():
    connection = FakeConnection()

    def post_validate():
        raise RuntimeError(
            "validation exploded"
        )

    with pytest.raises(
        AristaApplyError,
        match="validation raised an exception",
    ):
        apply_commands(
            ARISTA_DEVICE,
            ["hostname R1"],
            post_validate=post_validate,
            session_name="ANA-EXCEPTION",
            connection_factory=lambda device: connection,
        )

    assert (
        "configure session "
        "ANA-EXCEPTION abort"
        in connection.commands
    )

    assert (
        "configure session "
        "ANA-EXCEPTION commit"
        not in connection.commands
    )

    assert (
        "copy running-config startup-config"
        not in connection.commands
    )

    assert connection.disconnected is True


def test_apply_stage_failure_aborts_before_timer():
    connection = FakeConnection(
        reject_command="hostname BAD",
    )

    with pytest.raises(
        AristaApplyError,
        match="EOS rejected",
    ):
        apply_commands(
            ARISTA_DEVICE,
            ["hostname BAD"],
            post_validate=lambda: True,
            session_name="ANA-STAGE-FAIL",
            connection_factory=lambda device: connection,
        )

    assert "abort" in connection.commands

    assert not any(
        command.startswith("commit timer ")
        for command in connection.commands
    )

    assert connection.disconnected is True


def test_apply_confirm_failure_attempts_rollback():
    connection = FakeConnection(
        timing_reject_command=(
            "configure session "
            "ANA-CONFIRM-FAIL commit"
        ),
    )

    with pytest.raises(
        AristaApplyError,
        match="EOS rejected",
    ):
        apply_commands(
            ARISTA_DEVICE,
            ["hostname R1"],
            post_validate=lambda: True,
            session_name="ANA-CONFIRM-FAIL",
            connection_factory=lambda device: connection,
        )

    assert (
        "configure session "
        "ANA-CONFIRM-FAIL abort"
        in connection.commands
    )

    assert (
        "copy running-config startup-config"
        not in connection.commands
    )


def test_apply_persistence_failure_is_explicit():
    connection = FakeConnection(
        timing_reject_command=(
            "copy running-config startup-config"
        ),
    )

    with pytest.raises(
        AristaApplyError,
        match="persistence to startup-config failed",
    ):
        apply_commands(
            ARISTA_DEVICE,
            ["hostname R1"],
            post_validate=lambda: True,
            session_name="ANA-SAVE-FAIL",
            connection_factory=lambda device: connection,
        )

    assert (
        "configure session "
        "ANA-SAVE-FAIL commit"
        in connection.commands
    )

    assert (
        "copy running-config startup-config"
        in connection.commands
    )

    assert (
        "configure session "
        "ANA-SAVE-FAIL abort"
        not in connection.commands
    )


@pytest.mark.parametrize(
    "timer",
    [
        "00:00:29",
        "01:00:01",
        "00:60:00",
        "BAD",
    ],
)
def test_apply_rejects_unsafe_commit_timer(timer):
    with pytest.raises(
        AristaApplyError,
        match="Commit timer",
    ):
        validate_commit_timer(timer)


def test_apply_rendered_config_translates_hierarchy():
    connection = FakeConnection(
        diff="+description APPLY"
    )

    rendered = """\
interface Ethernet1
   description APPLY
!
"""

    result = apply_rendered_config(
        ARISTA_DEVICE,
        rendered,
        post_validate=lambda: True,
        session_name="ANA-RENDERED",
        connection_factory=lambda device: connection,
    )

    commands, _ = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface Ethernet1",
        "description APPLY",
        "exit",
    ]

    assert result.command_count == 3
    assert result.confirmed is True
    assert result.persisted is True


def test_apply_wrong_platform_is_denied():
    device = dict(ARISTA_DEVICE)
    device["platform"] = "Cisco IOS-XE"

    with pytest.raises(
        AristaApplyError,
        match="requires platform",
    ):
        apply_commands(
            device,
            ["hostname R3"],
            post_validate=lambda: True,
        )
