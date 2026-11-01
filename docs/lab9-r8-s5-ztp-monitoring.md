# Lab 9 — R8 and S5 ZTP and Network Automation Integration

**Status:** Core implementation and monitoring verified; final CI and Git integration pending.

**Last updated:** 2026-11-01

## 1. Objective

Lab 9 expands the existing multi-vendor Containerlab network with
Arista EOS router R8 and access switch S5.

The implementation reuses the existing ANA network automation framework:

- Device-initiated ZTP using the existing FastAPI and NetBox workflow.
- NetBox-backed device intent and Arista Jinja2 templates.
- Dynamic routing and Layer 2 configuration.
- Existing Arista gNMI streaming telemetry.
- Telegraf and gNMIc collectors.
- InfluxDB canonical monitoring measurements.
- Grafana network monitoring dashboards.
- Git and Jenkins CI validation.

The objective is to integrate the new devices into established
automation systems, rather than build a separate monitoring or
provisioning framework.

## 2. Scope and Boundaries

### Included

- Arista router R8.
- Arista access switch S5.
- R2-to-R8 routed connection.
- R8-to-S5 access-network connection.
- R8 OSPF Area 20 adjacency.
- R8/S5 telemetry and Grafana monitoring.
- Repository integration and testing.

### Explicitly excluded

- Configuration of H5, H6, or H7.
- RIP configuration.
- Changes to R6 or R7.
- Configuration of external/provider-owned R5.
- Deployment of the proposed R8/S5 Graphviz topology update.

Existing network nodes, links, services, and unrelated working-tree
changes must be preserved.

## 3. System Architecture

### VM1 — Network and ZTP clients

VM1 hosts the Containerlab network and Arista EOS containers.

R8 and S5 initiate their bootstrap workflow using their EOS
ANA-ZTP event handlers and router-local flash scripts.

The new nodes and their management connectivity are created by the
lab environment before device-side provisioning begins.

This is an EOS event-handler-based ZTP-style implementation, not
native DHCP-based factory ZTP.

### VM2 — Automation and monitoring services

VM2 hosts:

- NetBox as the network source of truth.
- FastAPI and the `/ztp/bootstrap` endpoint.
- Existing Python/Jinja2 configuration rendering.
- Telegraf and gNMIc telemetry collectors.
- InfluxDB with the `ana-lab` organization and bucket.
- Grafana dashboards.
- Jenkins CI and the automation Git repository.

Repository location:

`~/ana-network-automation`

## 4. R8 and S5 Network Integration

| Device | Role | Management IP |
|---|---|---|
| R8 | Arista EOS router | 172.20.20.20 |
| S5 | Arista EOS access switch | 172.20.20.21 |

### Routed connection: R2 to R8

| Endpoint | Interface | Address |
|---|---|---|
| R2 | Ethernet2 | 10.1.0.14/31 |
| R8 | Ethernet1 | 10.1.0.15/31 |

- Routing protocol: OSPF.
- Area: 20.
- R8 router ID: 10.255.255.18.
- R2 router ID: 10.255.255.12.
- Verified R2/R8 adjacency state: FULL.

Existing routing adjacencies were retained.

### Access connection: R8 to S5

| Endpoint | Interface | Configuration |
|---|---|---|
| R8 | Ethernet2 | 172.16.50.1/24 |
| S5 | Ethernet1 | VLAN 10 |

S5 uses VLAN 10 on Ethernet1, Ethernet2, and Ethernet3.

S5 is an access switch and does not participate in OSPF.

H6/H7 connections may exist physically in the lab topology, but
their host configurations are deliberately deferred.

## 5. Device-Initiated ZTP

R8 and S5 reuse the device-initiated ZTP pattern introduced
in the preceding lab.

### Bootstrap sequence

1. Containerlab creates the devices and management connectivity.
2. EOS provides the existing management/bootstrap prerequisites.
3. The `ANA-ZTP` event handler invokes `/mnt/flash/ana-ztp.sh`.
4. The device contacts the VM2 FastAPI bootstrap endpoint.
5. The server uses device identity and existing NetBox records.
6. The existing renderer produces device-specific Arista configuration.
7. The device retrieves and applies its bootstrap configuration.
8. The resulting network state is verified.

### Device-side artifacts

- `/mnt/flash/ana-ztp.sh`
- EOS event handler `ANA-ZTP`
- Running and startup configurations

The handler and its on-boot configuration were saved.

Device-initiated bootstrap execution succeeded.

**Verification limitation:** A complete reboot-triggered execution
of the final R8/S5 handler configuration has not been demonstrated.
Do not claim that the final reboot test was completed.

Avoid unnecessary Docker container restarts: earlier lab work
identified risks to Containerlab data-plane links.

## 6. NetBox and Infrastructure as Code

The existing NetBox-backed automation and rendering workflow
was reused for R8 and S5.

Relevant existing Arista templates include:

- `templates/arista/ztp_router.j2`
- `templates/arista/ztp_switch.j2`

The existing FastAPI bootstrap, NetBox device records, and
configuration-rendering framework were reused rather than
reimplemented.

The deployment follows existing automation safeguards.

Device-side gNMI listeners were already configured and persisted
in running and startup configurations.

The current ZTP templates do not independently establish all
gNMI prerequisites on an entirely uninitialized EOS device.
The verified implementation relies on persistent device-side
management configuration.

## 7. Streaming Telemetry

### Collection architecture

Arista EOS gNMI, TCP/6030
    -> Telegraf
    -> existing Starlark normalizers
    -> InfluxDB `ana-lab`
    -> Grafana

R8 and S5 were added to the established Arista target lists.

### Updated Telegraf collectors

- `configs/telegraf/telegraf.d/arista.conf`
- `configs/telegraf/telegraf.d/arista_health.conf`
- `configs/telegraf/telegraf.d/arista_ifstate.conf`

### Updated normalizers

- `configs/telegraf/telegraf.d/interface_normalize.conf`
- `configs/telegraf/telegraf.d/device_health_normalize.conf`

New source-to-device mappings:

| Management IP | Device |
|---|---|
| 172.20.20.20 | R8 |
| 172.20.20.21 | S5 |

All original Arista target and normalizer settings were preserved.

Live and repository configurations were updated independently
where pre-existing settings differed.

### Verified canonical measurements

| Measurement | Verified fields |
|---|---|
| `net_ifcounters` | `in_octets`, `out_octets` |
| `net_ifstate` | `admin_status`, `oper_status` |
| `net_device_health` | `cpu_util_pct`, `memory_util_pct`, `uptime_seconds` |

A read-only InfluxDB query confirmed:

- R2: 7/7 required fields fresh.
- R8: 7/7 required fields fresh.
- S5: 7/7 required fields fresh.

This verifies the existing telemetry path end to end.

### OSPF monitoring

R8 was added to:

`configs/gnmic/arista-ospf.yml`

The existing `gnmic-arista-ospf` service was restarted successfully,
with its original three targets preserved.

InfluxDB confirmed fresh R8 OSPF data in:

- `arista_ospfv2_adj`
- `net_neighbor_state`

R8's canonical neighbor telemetry reported `session_state=FULL`.

The existing R1, R2, and S3 OSPF measurements remained present.
The separate numerical `session_up` value was not recorded in
the final acceptance output.

S5 does not require an OSPF collector target.

No changes were made to the existing Arista IS-IS collector.

## 8. Grafana Visualization

### ANA Lab — Interfaces

Dashboard UID: `ana-interfaces`

R8 and S5 were added to the existing custom device selector.

Live Grafana API verification confirmed:

- R8 selectable.
- S5 selectable.
- Existing 13 panels retained.
- Existing panel queries and defaults preserved.

### ANA Lab — Routing

Dashboard UID: `ana-routing`

R8 was added to the existing custom device selector.

Live Grafana API verification confirmed:

- R8 selectable.
- Existing 9 panels retained.
- Existing panel queries and defaults preserved.

S5 is not added to the routing selector because it is not
a routing participant.

### ANA Lab — Network Overview

Dashboard UID: `ana-network-overview`

The existing seven panel queries exclude R5 but do not use
a fixed R1–R4/S1–S4 inclusion list.

Consequently, R8/S5 canonical measurements are eligible for
the existing overview queries without editing the dashboard.

A separate visual acceptance test of all overview panels
has not been recorded.

### Network Topology — Deferred

Dashboard UID: `adqzdvd`

The production dashboard is database-managed through Grafana,
not deployed from the provisioned dashboard file directory.

The active dashboard remained at version 12 at the last check.

A proposed extension was prepared for:

- R2 Ethernet2 <-> R8 Ethernet1.
- R8 Ethernet2 <-> S5 Ethernet1.

Candidate Flux queries B, C, and D were executed against InfluxDB.

Both new links returned operational status UP; traffic rows
were also returned.

A pre-existing missing comma in Query D was corrected in the
candidate and the corrected query executed successfully.

**Deployment is deliberately deferred.**

The user chose not to modify the live Graphviz dashboard at
this stage. The repository topology JSON was likewise not
modified by the attempted deployment.

The attempted deployment stopped during terminal input
handling, before the Grafana write operation.

Candidate/backup workspace on VM2:

`~/ana-lab9-topology-preview-OzP4Dhfl/`

Do not represent the candidate as a deployed dashboard.

## 9. DevOps and Jenkins

The initial Lab 9 implementation was committed and pushed:

- `33757ca` — Lab 9 implementation.
- `855f59e` — Jenkinsfile adjustment.

Jenkins CI build #24 succeeded for commit `855f59e`.

Recorded test outcome:

- 810 tests passed.
- `coverage.xml` generated.
- `coverage-audit.json` generated.
- `coverage-audit.txt` generated.

**Important:** This CI result predates the later telemetry,
normalization, and Grafana selector modifications.

The final monitoring changes still require code review,
regression validation, commit, and push.

The separate deployment job must not be run merely to
validate documentation or CI.

## 10. Outstanding Work

The remaining agreed tasks are:

1. Complete and review Lab 9 documentation.
2. Add or run appropriate monitoring regression checks.
3. Run CI for the final reviewed commit.
4. Commit and push only the intended Lab 9 changes.

Optional/deferred work:

- Deploy the validated R8/S5 Graphviz topology candidate.
- Perform a controlled reboot-triggered ZTP demonstration,
  only with an authorized maintenance plan and link checks.
- Configure H5/H6/H7 in a later scope.

## 11. Safety and Operations

Do not:

- Reconfigure R5.
- Modify R6/R7 without explicit authorization.
- Configure H5/H6/H7 as part of this task.
- Enable RIP.
- Restart Containerlab or EOS containers casually.
- Expose passwords, API tokens, or bootstrap credentials.
- Stage the entire repository with `git add .`.
- Overwrite unrelated working-tree modifications.

Use targeted validation and targeted Git staging.

## 12. Acceptance Summary

| Objective | Evidence | Status |
|---|---|---|
| R8/S5 provisioning | Device-initiated bootstrap completed | Verified |
| R2/R8 routing | OSPF adjacency FULL | Verified |
| S5 access configuration | VLAN 10 and relevant interfaces | Verified |
| Interface telemetry | 7/7 fresh fields per new device | Verified |
| R8 OSPF telemetry | Fresh canonical FULL neighbor state | Verified |
| Grafana Interfaces | Live device selector includes R8/S5 | Verified |
| Grafana Routing | Live selector includes R8 | Verified |
| Grafana Overview | Existing dynamic queries support new devices | Query inspection |
| Graphviz topology | Candidate queries passed; not deployed | Deferred |
| Initial Jenkins CI | Build #24, 810 tests passed | Verified |
| Final monitoring CI/Git | Not yet completed | Pending |
| Final reboot ZTP demonstration | Not performed | Deferred |

This document describes the implementation and evidence obtained
through 2026-11-01, not an unverified future deployment.
