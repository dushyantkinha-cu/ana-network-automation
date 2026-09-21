import pytest

from automation.deployment.cisco import (
    CiscoPreviewError,
    check_preview_prerequisites,
    extract_archive_path,
    normalize_running_config,
    preview_commands,
    preview_rendered_config,
    rendered_config_to_commands,
    running_config_hash,
    validate_preview_commands,
)

CISCO_DEVICE = {
    "hostname": "R3",
    "platform": "Cisco IOS-XE",
    "management_ip": "172.20.20.9/24",
}

class FakeConnection:
    def __init__(
        self,
        running_archive=(
            "archive\n"
            " path bootflash:ana-archive\n"
            " maximum 5\n"
        ),
        rollback_state=(
            "%No Rollback Confirmed "
            "Change pending"
        ),
        startup_archive="",
    ):
        self.outputs = {
            (
                "show running-config "
                "| section ^archive"
            ): running_archive,
            (
                "show archive config "
                "rollback timer"
            ): rollback_state,
            (
                "show startup-config "
                "| section ^archive"
            ): startup_archive,
        }

        self.commands = []
        self.disconnected = False

    def send_command(
        self,
        command,
    ):
        self.commands.append(command)
        return self.outputs.get(
            command,
            "",
        )

    def disconnect(self):
        self.disconnected = True

class FakePreviewConnection(FakeConnection):
    def __init__(
        self,
        stage_output="",
        timer_output="",
        active_rollback_state=(
            "Rollback Confirmed Change pending"
        ),
        revert_output="",
        post_revert_state=(
            "%No Rollback Confirmed "
            "Change pending"
        ),
        before_running_config=(
            "Building configuration...\n"
            "\n"
            "Current configuration : 100 bytes\n"
            "!\n"
            "! Last configuration change "
            "at 10:00:00 UTC\n"
            "hostname R3\n"
            "!\n"
            "end\n"
        ),
        after_running_config=None,
    ):
        super().__init__()

        self.stage_output = stage_output
        self.timer_output = timer_output
        self.active_rollback_state = (
            active_rollback_state
        )
        self.revert_output = revert_output
        self.post_revert_state = (
            post_revert_state
        )

        self.before_running_config = (
            before_running_config
        )
        self.after_running_config = (
            after_running_config
            if after_running_config is not None
            else before_running_config.replace(
                "10:00:00",
                "10:05:00",
            )
        )

        self.timing_commands = []
        self.config_set_calls = []

        self.rollback_started = False
        self.reverted = False

    def send_command_timing(
        self,
        command,
    ):
        self.timing_commands.append(command)

        if command.startswith(
            "configure terminal revert timer "
        ):
            if "% invalid input" not in (
                self.timer_output.lower()
            ):
                self.rollback_started = True

            return self.timer_output

        if command == "configure revert now":
            if "% invalid input" not in (
                self.revert_output.lower()
            ):
                self.reverted = True

            return self.revert_output

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

        if (
            command
            == "show archive config rollback timer"
        ):
            if not self.rollback_started:
                return (
                    "%No Rollback Confirmed "
                    "Change pending"
                )

            if self.reverted:
                return self.post_revert_state

            return self.active_rollback_state

        if command == "show running-config":
            if self.reverted:
                return self.after_running_config

            return self.before_running_config

        return self.outputs.get(
            command,
            "",
        )

def test_preview_starts_timer_stages_and_reverts():
    connection = FakePreviewConnection()

    result = preview_commands(
        CISCO_DEVICE,
        [
            "interface GigabitEthernet2",
            "description STAGE7-TEST",
            "exit",
        ],
        connection_factory=(
            lambda device: connection
        ),
    )

    assert result.command_count == 3
    assert result.rollback_started is True
    assert result.rollback_completed is True
    assert connection.reverted is True
    assert result.config_restored is True
    assert result.before_hash == result.after_hash

    assert connection.timing_commands == [
        "configure terminal revert timer 5",
        "configure revert now",
    ]

    assert len(
        connection.config_set_calls
    ) == 1

    commands, kwargs = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface GigabitEthernet2",
        "description STAGE7-TEST",
        "exit",
    ]

    assert (
        kwargs["enter_config_mode"]
        is False
    )

    assert (
        kwargs["exit_config_mode"]
        is True
    )

    assert kwargs["cmd_verify"] is True
    assert connection.disconnected is True


def test_preview_never_confirms_or_saves():
    connection = FakePreviewConnection()

    preview_commands(
        CISCO_DEVICE,
        ["hostname R3"],
        connection_factory=(
            lambda device: connection
        ),
    )

    all_commands = (
        connection.commands
        + connection.timing_commands
    )

    assert "configure confirm" not in all_commands
    assert "write memory" not in all_commands
    assert "copy running-config startup-config" not in all_commands


def test_stage_failure_still_reverts():
    connection = FakePreviewConnection(
        stage_output="% Invalid input"
    )

    with pytest.raises(
        CiscoPreviewError,
        match="rejected",
    ):
        preview_commands(
            CISCO_DEVICE,
            ["hostname BAD"],
            connection_factory=(
                lambda device: connection
            ),
        )

    assert (
        "configure revert now"
        in connection.timing_commands
    )

    assert connection.disconnected is True


def test_timer_failure_does_not_stage():
    connection = FakePreviewConnection(
        timer_output="% Invalid input"
    )

    with pytest.raises(
        CiscoPreviewError,
        match="rejected",
    ):
        preview_commands(
            CISCO_DEVICE,
            ["hostname R3"],
            connection_factory=(
                lambda device: connection
            ),
        )

    assert (
        connection.config_set_calls
        == []
    )

    assert (
        "configure revert now"
        not in connection.timing_commands
    )

    assert connection.disconnected is True


def test_missing_rollback_after_staging_reverts():
    connection = FakePreviewConnection(
        active_rollback_state=(
            "%No Rollback Confirmed "
            "Change pending"
        )
    )

    with pytest.raises(
        CiscoPreviewError,
        match="not active",
    ):
        preview_commands(
            CISCO_DEVICE,
            ["hostname R3"],
            connection_factory=(
                lambda device: connection
            ),
        )

    assert (
        "configure revert now"
        in connection.timing_commands
    )


def test_extract_archive_path():
    output = """\
archive
 path bootflash:ana-archive
 maximum 5
"""

    assert extract_archive_path(
        output
    ) == "bootflash:ana-archive"


def test_prerequisites_pass_running_only():
    connection = FakeConnection()

    result = check_preview_prerequisites(
        CISCO_DEVICE,
        connection_factory=(
            lambda device: connection
        ),
    )

    assert (
        result.archive_path
        == "bootflash:ana-archive"
    )

    assert (
        result.startup_archive_persistent
        is False
    )

    assert (
        result.rollback_pending
        is False
    )

    assert connection.disconnected is True


def test_prerequisites_report_startup_persistence():
    connection = FakeConnection(
        startup_archive=(
            "archive\n"
            " path bootflash:ana-archive\n"
            " maximum 5\n"
        )
    )

    result = check_preview_prerequisites(
        CISCO_DEVICE,
        connection_factory=(
            lambda device: connection
        ),
    )

    assert (
        result.startup_archive_persistent
        is True
    )


def test_missing_archive_is_denied():
    connection = FakeConnection(
        running_archive=""
    )

    with pytest.raises(
        CiscoPreviewError,
        match="archive is not configured",
    ):
        check_preview_prerequisites(
            CISCO_DEVICE,
            connection_factory=(
                lambda device: connection
            ),
        )

    assert connection.disconnected is True


def test_wrong_archive_path_is_denied():
    connection = FakeConnection(
        running_archive=(
            "archive\n"
            " path bootflash:wrong-path\n"
        )
    )

    with pytest.raises(
        CiscoPreviewError,
        match="expected",
    ):
        check_preview_prerequisites(
            CISCO_DEVICE,
            connection_factory=(
                lambda device: connection
            ),
        )

    assert connection.disconnected is True


def test_existing_rollback_is_denied():
    connection = FakeConnection(
        rollback_state=(
            "Rollback Confirmed Change "
            "pending"
        )
    )

    with pytest.raises(
        CiscoPreviewError,
        match="already be pending",
    ):
        check_preview_prerequisites(
            CISCO_DEVICE,
            connection_factory=(
                lambda device: connection
            ),
        )

    assert connection.disconnected is True


def test_prerequisite_wrong_platform_denied():
    device = dict(CISCO_DEVICE)
    device["platform"] = "Arista EOS"

    connection_called = False

    def forbidden_connection(
        device,
    ):
        nonlocal connection_called
        connection_called = True
        raise AssertionError

    with pytest.raises(
        CiscoPreviewError,
        match="requires platform",
    ):
        check_preview_prerequisites(
            device,
            connection_factory=(
                forbidden_connection
            ),
        )

    assert connection_called is False

def test_interface_context():
    rendered = """\
hostname R3
!
interface GigabitEthernet2
 ip address 10.1.0.10 255.255.255.254
 ip nat inside
!
router ospf 1
 router-id 10.255.255.13
!
end
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "hostname R3",
        "interface GigabitEthernet2",
        "ip address 10.1.0.10 255.255.255.254",
        "ip nat inside",
        "exit",
        "router ospf 1",
        "router-id 10.255.255.13",
        "exit",
    ]


def test_empty_address_family_uses_explicit_exit():
    rendered = """\
router ospfv3 1
 address-family ipv4 unicast
 exit-address-family
 address-family ipv6 unicast
  router-id 10.255.255.13
 exit-address-family
!
end
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "router ospfv3 1",
        "address-family ipv4 unicast",
        "exit-address-family",
        "address-family ipv6 unicast",
        "router-id 10.255.255.13",
        "exit-address-family",
        "exit",
    ]


def test_bgp_address_families():
    rendered = """\
router bgp 65003
 bgp router-id 10.255.255.13
 address-family ipv4
  neighbor 203.0.114.1 activate
 exit-address-family
 address-family ipv6
  neighbor 2001:DB8:1::1 activate
 exit-address-family
!
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "router bgp 65003",
        "bgp router-id 10.255.255.13",
        "address-family ipv4",
        "neighbor 203.0.114.1 activate",
        "exit-address-family",
        "address-family ipv6",
        "neighbor 2001:DB8:1::1 activate",
        "exit-address-family",
        "exit",
    ]


def test_route_map_and_acl_contexts():
    rendered = """\
route-map RM-TEST permit 10
 match ipv6 address prefix-list PL-TEST
!
ip access-list standard NAT_ACL
 10 permit 10.0.0.0 0.255.255.255
!
"""

    commands = rendered_config_to_commands(
        rendered
    )

    assert commands == [
        "route-map RM-TEST permit 10",
        "match ipv6 address prefix-list PL-TEST",
        "exit",
        "ip access-list standard NAT_ACL",
        "10 permit 10.0.0.0 0.255.255.255",
        "exit",
    ]


def test_comments_and_end_are_not_sent():
    rendered = """\
hostname R3
!
! comment that must not be sent
!
end
"""

    assert rendered_config_to_commands(
        rendered
    ) == [
        "hostname R3",
    ]


def test_tabs_are_rejected():
    with pytest.raises(
        CiscoPreviewError,
        match="spaces for indentation",
    ):
        rendered_config_to_commands(
            "interface GigabitEthernet2\n"
            "\tip nat inside\n"
        )


def test_unexpected_exit_address_family_is_rejected():
    with pytest.raises(
        CiscoPreviewError,
        match="Unexpected",
    ):
        rendered_config_to_commands(
            "exit-address-family\n"
        )


def test_empty_rendered_config_is_rejected():
    with pytest.raises(
        CiscoPreviewError,
        match="contains no commands",
    ):
        rendered_config_to_commands(
            "\n!\nend\n"
        )

def test_revert_command_failure_is_detected():
    connection = FakePreviewConnection(
        revert_output="% Invalid input"
    )

    with pytest.raises(
        CiscoPreviewError,
        match="rejected",
    ):
        preview_commands(
            CISCO_DEVICE,
            ["hostname R3"],
            connection_factory=(
                lambda device: connection
            ),
        )

    assert connection.disconnected is True


def test_revert_completion_failure_is_detected():
    connection = FakePreviewConnection(
        post_revert_state=(
            "Rollback Confirmed Change pending"
        )
    )

    with pytest.raises(
        CiscoPreviewError,
        match="did not complete",
    ):
        preview_commands(
            CISCO_DEVICE,
            ["hostname R3"],
            connection_factory=(
                lambda device: connection
            ),
        )

    assert (
        "configure revert now"
        in connection.timing_commands
    )

    assert connection.disconnected is True


def test_running_config_normalization_ignores_metadata():
    before = """\
Building configuration...

Current configuration : 100 bytes
!
! Last configuration change at 10:00:00 UTC
hostname R3
!
end
"""

    after = """\
Building configuration...

Current configuration : 100 bytes
!
! Last configuration change at 10:05:00 UTC
hostname R3
!
end
"""

    assert (
        normalize_running_config(before)
        == normalize_running_config(after)
    )

    assert (
        running_config_hash(before)
        == running_config_hash(after)
    )


def test_preview_verifies_config_restoration():
    connection = FakePreviewConnection()

    result = preview_commands(
        CISCO_DEVICE,
        ["hostname R3"],
        connection_factory=(
            lambda device: connection
        ),
    )

    assert result.config_restored is True

    assert (
        result.before_hash
        == result.after_hash
    )


def test_preview_detects_failed_config_restoration():
    connection = FakePreviewConnection(
        after_running_config=(
            "Building configuration...\n"
            "Current configuration : 100 bytes\n"
            "!\n"
            "! Last configuration change "
            "at 10:05:00 UTC\n"
            "hostname R3-UNEXPECTED\n"
            "!\n"
            "end\n"
        )
    )

    with pytest.raises(
        CiscoPreviewError,
        match="did not return",
    ):
        preview_commands(
            CISCO_DEVICE,
            ["hostname R3"],
            connection_factory=(
                lambda device: connection
            ),
        )

    assert connection.disconnected is True


@pytest.mark.parametrize(
    "command",
    [
        "configure confirm",
        "configure revert now",
        "configure terminal revert timer 5",
        "write memory",
        "copy running-config startup-config",
        "reload",
        "erase startup-config",
    ],
)
def test_forbidden_preview_commands(
    command,
):
    with pytest.raises(
        CiscoPreviewError,
        match="Forbidden preview command",
    ):
        validate_preview_commands(
            [command]
        )


def test_multiline_preview_command_denied():
    with pytest.raises(
        CiscoPreviewError,
        match="single CLI line",
    ):
        validate_preview_commands(
            [
                "hostname R3\n"
                "hostname BAD"
            ]
        )


def test_rendered_preview_uses_translator():
    connection = FakePreviewConnection()

    rendered = """\
interface GigabitEthernet2
 description STAGE7-TEST
!
end
"""

    result = preview_rendered_config(
        CISCO_DEVICE,
        rendered,
        connection_factory=(
            lambda device: connection
        ),
    )

    commands, _ = (
        connection.config_set_calls[0]
    )

    assert commands == [
        "interface GigabitEthernet2",
        "description STAGE7-TEST",
        "exit",
    ]

    assert result.command_count == 3
    assert result.rollback_completed is True
    assert result.config_restored is True
