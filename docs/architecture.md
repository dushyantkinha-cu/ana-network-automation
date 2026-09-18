# ANA Network Automation Framework Architecture

## 1. Purpose

The ANA Network Automation Framework provides a version-controlled
Infrastructure-as-Code (IaC) platform for managing the network.

The framework is responsible for:

- Network Source of Truth (NSOT)
- IP address management (IPAM)
- Device inventory
- Configuration templates
- Configuration generation
- Configuration change management
- Golden configuration storage
- Monitoring integration
- Network visualization
- Automation workflows

Git is used as the authoritative version-control system for the
automation framework and its machine-readable configuration.

---

## 2. Lab Architecture

### Ubuntu VM1

Ubuntu VM1 hosts the simulated network devices using Containerlab.

Containerlab is used only to provide virtual equivalents of physical
network devices for the lab.

Containerlab lifecycle management is outside the scope of this
automation framework.

The automation system must treat network devices as though they were
physical devices installed in a production network.

### Ubuntu VM2

Ubuntu VM2 hosts the Network Management and Automation System (NMAS).

Current services include:

- Git-based automation repository
- Telegraf
- gNMIc
- InfluxDB
- Grafana
- Network telemetry normalization
- Future NSOT/IPAM services
- Future configuration automation services
- Future web interface

VM2 has management connectivity to the network devices.

---

## 3. Device Onboarding Boundary

The automation framework does not perform physical installation or
initial out-of-band provisioning.

A new device is assumed to have already been:

1. Physically installed and powered on.
2. Connected to the management network.
3. Given basic management configuration.
4. Assigned a reachable management IP address.

Once management connectivity exists, the device can be onboarded into
the Network Source of Truth and managed by the automation framework.

---

## 4. Managed Device Scope

The following devices are managed by the organization:

- R1
- R2
- R3
- R4
- S1
- S2
- S3
- S4

### R5

R5 represents the service-provider Provider Edge (PE) router.

R5 is assumed to be owned and managed by the ISP.

Therefore R5:

- remains visible in the topology for network context;
- is not part of the managed-device inventory;
- is not configured by the automation framework;
- is not part of the organization's monitoring scope;
- does not receive golden configuration management.

---

## 5. Supported Network Platforms

The current environment contains multiple network operating systems:

- Arista EOS / cEOS
- Cisco IOS-XE
- Nokia SR Linux

Configuration generation and deployment must remain vendor-aware.

Vendor-specific configuration syntax must not be assumed to be
interchangeable between platforms.

---

## 6. Current Monitoring Architecture

The current monitoring pipeline is:

Network Devices
    |
    v
Telemetry Collection
(Telegraf and gNMIc)
    |
    v
Cross-Vendor Normalization
    |
    v
InfluxDB
    |
    v
Grafana

The current implementation contains normalized measurements for:

- Device health
- Interface state
- Interface counters
- Routing neighbor/session state

Grafana currently provides:

- Network Overview dashboard
- Interfaces dashboard
- Routing dashboard
- Dynamic real-time topology visualization

The existing monitoring implementation is considered a known-good
system and should be integrated into the future web interface rather
than unnecessarily redesigned.

---

## 7. Infrastructure-as-Code Architecture

The intended IaC workflow is:

Source of Truth
    |
    v
YAML Data
    |
    v
Jinja2 Templates
    |
    v
Configuration Renderer
    |
    v
Validation
    |
    v
Candidate Configuration
    |
    v
Configuration Diff / Review
    |
    v
Deployment
    |
    v
Golden Configuration
    |
    v
Git Change History

The Network Source of Truth will contain the desired state of the
managed network.

Configuration syntax will be generated from the NSOT using reusable
vendor-specific Jinja2 templates.

---

## 8. Repository Layout

### inventory/

Network Source of Truth device and site information.

### ipam/

IP address, subnet, VLAN, loopback, and address allocation data.

### templates/

Vendor-specific Jinja2 network configuration templates.

### automation/

Python automation modules for validation, rendering, deployment, and
configuration retrieval.

### webapp/

Web-based interface for interacting with the NSOT and automation
framework.

### golden-configs/

Timestamped known-good device configurations.

### generated-configs/

Temporary rendered candidate configurations.

Generated files are not committed to Git.

### configs/

Version-controlled infrastructure configuration for components such as:

- Telegraf
- gNMIc
- Grafana
- InfluxDB
- systemd

Runtime credentials are not stored in Git.

### topology/

Network topology reference information.

### docs/

Project architecture, procedures, and implementation documentation.

### scripts/

Administrative and maintenance scripts.

### tests/

Validation and automated testing.

---

## 9. Secret Management

Passwords, API tokens, private keys, and other credentials must not be
committed to Git.

Repository configuration should reference environment variables or
external protected credential files.

Examples include:

- INFLUX_TOKEN
- GRAFANA_INFLUX_TOKEN
- ARISTA_USERNAME
- ARISTA_PASSWORD
- SRLINUX_USERNAME
- SRLINUX_PASSWORD

Real credential values remain outside the repository.

---

## 10. Source of Truth Principle

The long-term design goal is:

Git + NSOT = Desired Network State

Network devices represent deployed state.

The automation framework compares, generates, validates, and deploys
configuration based on the desired state stored in the repository.
