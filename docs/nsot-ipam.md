# Network Source of Truth and IPAM

## Purpose

NetBox is the authoritative Network Source of Truth (NSOT) and IP Address
Management (IPAM) system for the ANA lab.

Git remains authoritative for automation code, Jinja2 templates, documentation,
golden configurations, monitoring configuration, and other version-controlled
artifacts.

The automation framework reads network intent from NetBox rather than
maintaining a duplicate YAML inventory.

---

## NetBox

NetBox version:

- 4.7.0

Lab site:

- ANA-Lab

NetBox stores:

- Sites
- Manufacturers
- Device types
- Platforms
- Device roles
- Devices
- Interfaces
- IP addresses
- Prefixes
- VLANs
- FHRP/VRRP information
- Automation-specific intent fields

---

## Device Scope

The organization manages the following eight network devices:

| Device | Platform | Role | Config Profile |
|---|---|---|---|
| R1 | Arista EOS | Router | distribution |
| R2 | Arista EOS | Router | distribution |
| R3 | Cisco IOS-XE | Router | edge |
| R4 | Cisco IOS-XE | Router | edge |
| S1 | Arista EOS | Multilayer Switch | access |
| S2 | Arista EOS | Multilayer Switch | access |
| S3 | Arista EOS | Multilayer Switch | core |
| S4 | Nokia SR Linux | Multilayer Switch | core |

R5 is an ISP/provider-edge device. It is represented in NetBox for topology
and addressing context but is intentionally excluded from organizational
automation.

NMAS and Web Server also exist in NetBox but are not network devices managed
by the network automation framework.

---

## Platforms

The following native NetBox Platform objects are used:

- Arista EOS
- Cisco IOS-XE
- Nokia SR Linux

Platform information is used by automation to determine the appropriate
vendor-specific behavior and, in later stages, configuration templates.

---

## Device Roles

The following relevant device roles exist:

- Router
- Multilayer Switch
- NMAS Server
- Server

S1 through S4 use the `Multilayer Switch` role because they perform both
Layer-2 switching and Layer-3 routing functions.

Device role and configuration profile are intentionally separate.

For example:

- S1 and S2 are Multilayer Switches with the `access` profile.
- S3 and S4 are Multilayer Switches with the `core` profile.

---

## Automation Safety Boundary

A Boolean NetBox custom field named:

`automation_managed`

defines whether a device is eligible for organizational automation.

Managed:

- R1
- R2
- R3
- R4
- S1
- S2
- S3
- S4

Not managed:

- R5
- NMAS
- Web Server

Automation must treat:

`automation_managed = true`

as a hard safety gate before configuration generation or deployment.

This prevents provider-owned or infrastructure-context devices from being
accidentally modified.

---

## Configuration Profiles

The `config_profile` custom field uses the following values:

- edge
- core
- distribution
- access

Current assignments:

- R1: distribution
- R2: distribution
- R3: edge
- R4: edge
- S1: access
- S2: access
- S3: core
- S4: core

The profile describes the function a device performs in this topology.

---

## Routing Protocol Intent

The `routing_protocols` custom field records intended routing functionality.

Current values:

- R1: OSPFv2, OSPFv3, IS-IS
- R2: OSPFv2, OSPFv3, IS-IS
- R3: BGP, OSPFv2, OSPFv3
- R4: BGP, OSPFv2, OSPFv3
- S1: IS-IS
- S2: IS-IS
- S3: OSPFv2, OSPFv3
- S4: OSPFv2, OSPFv3

R5 has no organizational automation intent assigned.

---

## Management Endpoints

Each managed device has a logical NetBox interface named:

`automation-mgmt`

This represents the management endpoint reachable by the NMAS automation host.

It is intentionally logical because the NMAS-reachable address is not always
the same as the interface addressing visible inside the network operating
system.

For example, Cisco IOS-XE devices may use a different device-native management
interface address internally while being reached by NMAS through the
Containerlab management network.

Canonical IPv4 automation endpoints:

| Device | Management IPv4 |
|---|---|
| R1 | 172.20.20.14/24 |
| R2 | 172.20.20.5/24 |
| R3 | 172.20.20.9/24 |
| R4 | 172.20.20.13/24 |
| S1 | 172.20.20.8/24 |
| S2 | 172.20.20.11/24 |
| S3 | 172.20.20.7/24 |
| S4 | 172.20.20.4/24 |

These are configured as the primary IPv4 addresses for the managed devices in
NetBox.

The management prefix is:

`172.20.20.0/24`

Management IPv6 is not currently used as the canonical automation endpoint and
is therefore not modeled on the logical `automation-mgmt` interfaces.

---

## IPAM Prefix Hierarchy

NetBox contains the production addressing used by the lab, including:

### IPv4 internal point-to-point addressing

- 10.1.0.0/28
- 10.3.0.0/29

Child `/31` prefixes represent individual point-to-point links.

### IPv4 loopback addressing

- 10.255.255.0/24

Individual loopback addresses remain IP Address objects rather than separate
`/32` Prefix objects.

### IPv6 internal point-to-point addressing

- fd12:3456:7890:a::/124
- fd12:3456:7890:c::/125

Child `/127` prefixes represent individual point-to-point links.

### IPv6 loopback addressing

- fd12:3456:7890:ffff::/64

Individual loopback addresses remain IP Address objects rather than separate
`/128` Prefix objects.

### Infrastructure and management

- 10.224.76.0/22
- 172.20.20.0/24

### Provider-facing descriptive IPAM

Provider-facing prefixes are represented in NetBox for topology and IPAM
context even though R5 is not automation managed.

This includes:

- 203.0.113.0/31
- 203.0.114.0/30 and child links
- 2001:db8::/127
- 2001:db8:1::/126 and child links

---

## VLANs

The following VLAN objects exist at ANA-Lab:

| VLAN ID | Name |
|---|---|
| 10 | VLAN10 |
| 20 | VLAN20 |
| 30 | VLAN30 |
| 50 | VLAN50 |

VLAN names are intentionally neutral because the live network configuration
defines VLAN numbers without semantic VLAN names.

---

## VLAN Prefixes

VLAN 10:

- 172.16.10.0/24
- fd12:3456:7890:10::/64

VLAN 20:

- 172.16.20.0/24
- fd12:3456:7890:20::/64

VLAN 30:

- fd12:3456:7890:30::/64

VLAN 30 is IPv6-only in the current design.

VLAN 50:

- 10.3.0.0/31
- fd12:3456:7890:c::/127

---

## SVI Addressing

### S1

Vlan10:

- 172.16.10.4/24
- fd12:3456:7890:10::4/64

Vlan20:

- 172.16.20.4/24
- fd12:3456:7890:20::4/64

Vlan30:

- fd12:3456:7890:30::4/64

Vlan50:

- 10.3.0.0/31
- fd12:3456:7890:c::/127

### S2

Vlan10:

- 172.16.10.5/24
- fd12:3456:7890:10::5/64

Vlan20:

- 172.16.20.5/24
- fd12:3456:7890:20::5/64

Vlan30:

- fd12:3456:7890:30::5/64

Vlan50:

- 10.3.0.1/31
- fd12:3456:7890:c::1/127

---

## Layer-2 VLAN Intent

NetBox models the S1 and S2 Layer-2 access and trunk configuration.

### S1

- Et2: tagged trunk carrying VLANs 10, 20, 30, 50
- Et3: access VLAN 10
- Et4: access VLAN 20

### S2

- Et2: tagged trunk carrying VLANs 10, 20, 30, 50
- Et3: access VLAN 10
- Et4: access VLAN 30

NetBox uses abbreviated interface names such as `Et2`, while the corresponding
Arista EOS interface name is `Ethernet2`.

---

## VRRP and FHRP

VRRP is modeled using native NetBox FHRP groups.

### VLAN 10 IPv4

- Protocol: VRRPv2
- Group: 10
- VIP: 172.16.10.1/24
- S1 Vlan10 priority: 150
- S2 Vlan10 priority: 100

### VLAN 10 IPv6

- Protocol: VRRPv3
- Group: 10
- VIP: fd12:3456:7890:10::1/64
- S1 Vlan10 priority: 150
- S2 Vlan10 priority: 100

### VLAN 20 IPv4

- Protocol: VRRPv2
- Group: 20
- VIP: 172.16.20.1/24
- S1 Vlan20 priority: 150
- S2 Vlan20 priority: 100

### VLAN 20 IPv6

- Protocol: VRRPv3
- Group: 20
- VIP: fd12:3456:7890:20::1/64
- S1 Vlan20 priority: 150
- S2 Vlan20 priority: 100

### VLAN 30 IPv6

- Protocol: VRRPv3
- Group: 30
- VIP: fd12:3456:7890:30::1/64
- S1 Vlan30 priority: 150
- S2 Vlan30 priority: 100

The current Master/Backup state is operational state and is intentionally not
stored as configuration intent in NetBox.

---

## NetBox API Credentials

The NetBox API token is not stored in Git.

Runtime environment variables are loaded from:

`~/.config/ana-network-automation/netbox.env`

Required variables:

- `NETBOX_URL`
- `NETBOX_TOKEN`

The credential file must remain outside the repository.

---

## NetBox Inventory Client

The repository contains:

`automation/netbox_inventory.py`

This is a read-only NetBox API client.

It retrieves:

- Devices
- Interfaces
- IP addresses
- Platform
- Manufacturer
- Device role
- Primary management IPv4
- Configuration profile
- Routing protocol intent
- Layer-2 VLAN membership
- Interface IP addressing

The script applies the `automation_managed` field as a hard filter.

Therefore its current output contains exactly:

- R1
- R2
- R3
- R4
- S1
- S2
- S3
- S4

It automatically excludes:

- R5
- NMAS
- Web Server

The script normalizes NetBox choice fields into simple values appropriate for
later automation processing.

Example execution:

```bash
source ~/.config/ana-network-automation/netbox.env
./automation/netbox_inventory.py
