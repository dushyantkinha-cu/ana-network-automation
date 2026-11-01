#!/bin/bash
set -euo pipefail
umask 077

DEVICE=R8
ZTP_URL=http://10.224.76.14:8000/ztp/bootstrap

CONFIG="/mnt/flash/ana-ztp-${DEVICE}.cfg"
LOG="/mnt/flash/ana-ztp-${DEVICE}.log"
TEMP="$(mktemp "/mnt/flash/.ana-ztp-${DEVICE}.XXXXXX")"

trap 'rm -f "$TEMP"' EXIT
exec >> "$LOG" 2>&1

fail() {
    echo "ERROR: $*"
    exit 1
}

echo "$(date -Is) Starting ${DEVICE} automatic provisioning"

VERSION="$(Cli -p 15 -c 'show version')"

MAC="$(printf '%s\n' "$VERSION" | awk -F: '
    /System MAC address:/ {
        gsub(/[^0-9A-Fa-f]/, "", $2)
        print tolower($2)
        exit
    }
')"

SERIAL="$(printf '%s\n' "$VERSION" | awk -F: '
    /Serial number:/ {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)
        print $2
        exit
    }
')"

[[ "$MAC" =~ ^[0-9a-f]{12}$ ]] || fail "Invalid EOS MAC"
[[ "$SERIAL" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || fail "Invalid EOS serial"

if ! curl -fsS \
    --connect-timeout 5 \
    --max-time 45 \
    --header "x-arista-systemmac: $MAC" \
    --header "x-arista-serial: $SERIAL" \
    --output "$TEMP" \
    "$ZTP_URL"; then
    fail "Bootstrap request rejected or unavailable"
fi

[[ -s "$TEMP" ]] || fail "Downloaded configuration is empty"
[[ $(wc -c < "$TEMP") -le 65536 ]] || fail "Configuration too large"

IFS= read -r FIRST_LINE < "$TEMP" || true

[[ "$FIRST_LINE" == "hostname $DEVICE" ]] || \
    fail "Downloaded hostname mismatch"

if grep -Eqi \
    '^[[:space:]]*interface[[:space:]]+Management0([[:space:]]|$)' \
    "$TEMP" || grep -Fqi 'automation-mgmt' "$TEMP"; then
    fail "Management interface modification prohibited"
fi

has_line() {
    awk -v target="$1" '
        {
            sub(/^[[:space:]]+/, "", $0)
            sub(/[[:space:]]+$/, "", $0)
        }
        $0 == target { ok=1; exit }
        END { exit(ok ? 0 : 1) }
    ' "$TEMP"
}

has_port_command() {
    local interface="$1" command="$2"

    awk -v target="$interface" -v wanted="$command" '
        function trimmed(s) {
            sub(/^[[:space:]]+/, "", s)
            sub(/[[:space:]]+$/, "", s)
            return s
        }

        /^interface[[:space:]]/ {
            active = (trimmed($2) == target)
            next
        }

        active && /^!/ { active=0 }
        active && /^[^[:space:]]/ { active=0 }
        active && trimmed($0) == wanted { ok=1 }

        END { exit(ok ? 0 : 1) }
    ' "$TEMP"
}

if [[ "$DEVICE" == R8 ]]; then
    has_line 'ip routing' || fail "IPv4 routing missing"

    has_port_command Ethernet1 'no switchport' || \
        fail "Transit port not routed"

    has_port_command Ethernet1 'ip address 10.1.0.15/31' || \
        fail "Transit address missing"

    has_port_command Ethernet1 'ip ospf network point-to-point' || \
        fail "OSPF point-to-point missing"

    has_port_command Ethernet2 'no switchport' || \
        fail "LAN gateway port not routed"

    has_port_command Ethernet2 'ip address 172.16.50.1/24' || \
        fail "LAN gateway address missing"

    has_port_command Loopback0 'ip address 10.255.255.18/32' || \
        fail "Loopback address missing"

    has_line 'router ospf 1' || fail "OSPF process missing"

    has_line 'network 10.1.0.14/31 area 0.0.0.20' || \
        fail "OSPF transit network missing"

    has_line 'network 172.16.50.0/24 area 0.0.0.20' || \
        fail "OSPF LAN network missing"

    has_line 'network 10.255.255.18/32 area 0.0.0.20' || \
        fail "OSPF loopback network missing"

    has_line 'passive-interface Ethernet2' || \
        fail "LAN passive-interface missing"

    has_line 'passive-interface Loopback0' || \
        fail "Loopback passive-interface missing"

elif [[ "$DEVICE" == S5 ]]; then
    if has_line 'ip routing' || \
        grep -Eqi '^[[:space:]]*(router|ipv6 router)[[:space:]]' \
        "$TEMP"; then
        fail "Routing configuration prohibited on S5"
    fi

    has_line 'vlan 10' || fail "VLAN 10 missing"

    for PORT in Ethernet1 Ethernet2 Ethernet3; do
        has_port_command "$PORT" 'switchport mode access' || \
            fail "$PORT access mode missing"

        has_port_command "$PORT" 'switchport access vlan 10' || \
            fail "$PORT VLAN 10 missing"
    done

else
    fail "Unsupported ZTP device"
fi

mv "$TEMP" "$CONFIG"
echo "$(date -Is) Configuration downloaded and validated"

Cli -p 15 -c "copy flash:ana-ztp-${DEVICE}.cfg running-config"
Cli -p 15 -c 'copy running-config startup-config'

echo "$(date -Is) ${DEVICE} provisioning completed"
