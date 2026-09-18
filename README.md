# ANA Network Automation Framework

Infrastructure-as-Code and Network Source of Truth framework for the
ANA Multi-Protocol Network Lab.

## Purpose

This repository provides version-controlled management of:

- Network Source of Truth
- IP address management
- Device inventory
- Jinja2 configuration templates
- Automated configuration generation
- Network configuration deployment
- Golden configuration backups
- Change management
- Monitoring integration
- Network visualization
- Supporting automation infrastructure configuration

## Architecture

### Ubuntu VM1
Hosts the simulated network environment.

Containerlab is used only to simulate the physical network devices and
is outside the scope of the automation framework.

### Ubuntu VM2
Hosts the Network Management and Automation System, including:

- Automation framework
- Network Source of Truth
- Telegraf
- InfluxDB
- Grafana
- gNMIc services
- Web interface

## Device Management Model

The automation framework assumes that a new physical device has already:

1. Been physically installed.
2. Been powered on.
3. Received basic management connectivity.
4. Received a reachable management IP address.

The automation framework begins managing the device after management
connectivity exists.

## Managed Network Scope

Managed devices:

- R1
- R2
- R3
- R4
- S1
- S2
- S3
- S4

R5 represents the provider-edge router and is externally managed by the
service provider. R5 is topology context only and is not managed by this
automation framework.

## Repository Structure

- `inventory/` - Network Source of Truth device/site inventory
- `ipam/` - IP address management data
- `templates/` - Vendor-specific Jinja2 configuration templates
- `automation/` - Automation and configuration-generation code
- `webapp/` - Web-based NSOT/IaC interface
- `golden-configs/` - Timestamped known-good configurations
- `generated-configs/` - Temporary rendered candidate configurations
- `configs/` - Automation/monitoring infrastructure configuration
- `topology/` - Network topology resources
- `docs/` - Project documentation
- `scripts/` - Maintenance and helper scripts
- `tests/` - Automation and template tests

## Security

Passwords, API tokens, private keys, environment files, and other
credentials must never be committed to this repository.
