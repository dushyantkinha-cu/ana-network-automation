# Jenkins CI/CD Integration

## 1. Purpose

Jenkins provides the Continuous Integration and Continuous Deployment
workflow for the ANA Network Automation Framework.

The Jenkins implementation is responsible for:

- Checking out the version-controlled automation repository from GitHub
- Creating isolated Python environments for pipeline execution
- Running syntax and regression tests
- Reporting CI status back to GitHub
- Providing guarded configuration deployment workflows
- Enforcing an automatic pre-apply preview before live configuration changes
- Supplying deployment credentials securely at runtime
- Reusing the existing vendor-independent deployment framework

Jenkins does not contain vendor-specific configuration logic.

All rendering, safety validation, preview, deployment, rollback, and
post-deployment validation remain implemented in the Python automation
framework.

---

## 2. Jenkins Host

Jenkins runs on Ubuntu VM2, the Network Management and Automation
System.

Jenkins version:

```text
2.516.2 LTS
```

Java runtime:

```text
OpenJDK 21
```

Jenkins listens only on the VM2 management address:

```text
10.224.76.14:8080
```

It is not bound to all VM interfaces.

The Jenkins service is configured through a systemd override containing:

```text
JENKINS_LISTEN_ADDRESS=10.224.76.14
```

This keeps the Jenkins interface on the management network instead of
exposing it on every interface.

Jenkins is not intentionally exposed to the public Internet.

---

## 3. GitHub Integration

The project repository is hosted publicly on GitHub:

```text
https://github.com/dushyantkinha-cu/ana-network-automation.git
```

Because the repository is public, Jenkins does not require GitHub
credentials to clone or fetch the source code.

Both Jenkins jobs are configured with:

```text
SCM: Git
Branch: */main
Credentials: none
```

The Jenkins pipeline definitions are stored in the repository itself:

```text
Jenkinsfile
Jenkinsfile.deploy
```

This keeps the CI/CD workflow version-controlled with the rest of the
automation code.

No inbound GitHub webhook is required.

Jenkins remains private on the management network and communicates
outbound to GitHub when source code or commit-status updates are needed.

The CI job automatically checks GitHub for changes by using Jenkins SCM
polling on the `main` branch. This allows code and configuration changes
committed to Git to trigger testing without exposing Jenkins through a
public inbound webhook.

Automatic SCM triggers remain intentionally disabled for the deployment
job. Deployment runs are started explicitly through the FastAPI
Automation portal or the Jenkins interface.

---

## 4. Jenkins Jobs

Two independent Jenkins jobs are used.

### 4.1 CI Job

Job name:

```text
ana-network-automation-ci
```

Pipeline definition:

```text
Jenkinsfile
```

Purpose:

- Checkout the current `main` branch
- Create a clean Python virtual environment
- Install development dependencies
- Compile Python source files
- Run the complete automated regression suite
- Verify Git working-tree cleanliness
- Report build status to GitHub

This job does not deploy configuration to network devices.

### 4.2 Deployment Job

Job name:

```text
ana-network-automation-deploy
```

Pipeline definition:

```text
Jenkinsfile.deploy
```

Purpose:

- Provide an explicitly parameterized deployment interface
- Restrict deployments to managed devices
- Support dry-run, preview, and apply operations
- Select only the credential required for the target platform
- Require typed device confirmation for live apply
- Require an automatic non-persistent preview before live apply
- Invoke the existing guarded Python deployment framework

The deployment job has no SCM trigger. It is started explicitly through the FastAPI Automation portal or the Jenkins interface.

---

## 5. Jenkins Credentials

Secrets are stored in the Jenkins Credentials store rather than in Git,
pipeline source code, or the Jenkins service account home directory.

Configured global credential IDs are:

```text
github-status-token
arista-device-credentials
cisco-device-credentials
nokia-device-credentials
netbox-url
netbox-token
```

The device credentials use Jenkins "Username with password" entries.

The NetBox URL, NetBox token, and GitHub status token use Jenkins
"Secret text" entries.

Credential values must never be committed to this repository.

The Jenkins pipeline refers only to credential IDs.

---

## 6. CI Pipeline

The CI pipeline is defined by:

```text
Jenkinsfile
```

### 6.1 Environment

The pipeline displays non-secret build information including:

- Jenkins host
- Jenkins workspace
- Python version
- Git commit being tested

### 6.2 GitHub Status Pending

Jenkins retrieves the GitHub status credential and reports:

```text
context: jenkins/ci
state: pending
```

to the commit being tested.

The token is masked by Jenkins and is not printed to the build log.

### 6.3 Python Environment

A new workspace-local virtual environment is created:

```text
.venv
```

Development dependencies are installed from:

```text
webapp/requirements-dev.txt
```

### 6.4 Python Syntax

The pipeline compiles:

```text
automation/
webapp/
tests/
```

using Python `compileall`.

### 6.5 Regression Tests

The complete pytest suite is executed with:

```text
python -m pytest -q
```

The final Stage 7G CI proof completed with:

```text
177 passed
2 warnings
```

### 6.6 Git Diff Check

The pipeline executes:

```text
git diff --check
```

and verifies that tracked repository files were not modified by the test
run.

### 6.7 GitHub Final Status

A successful pipeline reports:

```text
context: jenkins/ci
state: success
```

A failed pipeline reports:

```text
context: jenkins/ci
state: failure
```

The GitHub commit page therefore shows Jenkins CI status directly on the
tested commit.

---

## 7. Deployment Parameters

The deployment pipeline is defined by:

```text
Jenkinsfile.deploy
```

It exposes three parameters.

### 7.1 DEVICE

Allowed values:

```text
R1
R2
R3
R4
S1
S2
S3
S4
```

R5 is intentionally absent.

R5 represents the provider-edge router and is outside the managed
automation scope.

The Python deployment framework also independently denies unmanaged
devices, so Jenkins is not the only protection.

### 7.2 ACTION

Allowed values:

```text
dry-run
preview
apply
```

### 7.3 CONFIRM_DEVICE

This field is required only for:

```text
ACTION=apply
```

Its value must exactly match the selected device.

For example:

```text
DEVICE=R3
ACTION=apply
CONFIRM_DEVICE=R3
```

is accepted by the Jenkins safety gate.

A mismatch is rejected before deployment begins.

---

## 8. Deployment Safety Model

Live deployment is protected by multiple independent controls.

The workflow is:

```text
Manual Jenkins job
        |
        v
Managed DEVICE choice
        |
        v
Explicit ACTION choice
        |
        v
Typed device confirmation
        |
        v
Automatic pre-apply preview
        |
        v
deploy_config.py
        |
        v
NetBox management-policy checks
        |
        v
Vendor transaction protection
        |
        v
Post-deployment validation
```

Jenkins does not replace the Python safety framework.

Instead, Jenkins adds additional controls before the framework is
invoked.

---

## 9. Jenkins Apply Guardrails

The deployment pipeline enforces multiple controls before a live
configuration change can occur.

### 9.1 Incorrect Device Confirmation

Test parameters:

```text
DEVICE=R3
ACTION=apply
CONFIRM_DEVICE=R4
Expected and observed result:

ERROR: For apply, CONFIRM_DEVICE must exactly match DEVICE.

The request is rejected before a live deployment can begin.
No device configuration is changed.

9.2 Automatic Pre-Apply Preview
For a valid apply request, Jenkins does not immediately perform the live
deployment.
The pipeline first invokes:

automation/deploy_config.py --device <DEVICE> --preview

The preview uses the platform-specific safe transaction mechanism and
must return without leaving a permanent configuration change.
If preview returns a nonzero status:

Pre-Apply Preview: FAILURE
Deployment stage: SKIPPED
Jenkins finishes the pipeline as a failure and the live apply command is
not executed.
If preview succeeds, Jenkins proceeds automatically to the guarded live
deployment:
automation/deploy_config.py   --device <DEVICE>   --apply   --confirm-device <DEVICE>
There is no second human approval step in the final workflow.
The safety boundary is therefore:

exact device confirmation
        |
        v
automatic preview
        |
        +--> failure -> no live deployment
        |
        v
guarded live apply

---

## 10. Credential Selection

The deployment pipeline selects credentials according to the target
device.

### Arista EOS

Devices:

```text
R1
R2
S1
S2
S3
```

Credential:

```text
arista-device-credentials
```

Runtime environment:

```text
ARISTA_USERNAME
ARISTA_PASSWORD
```

### Cisco IOS-XE

Devices:

```text
R3
R4
```

Credential:

```text
cisco-device-credentials
```

Runtime environment:

```text
CISCO_USERNAME
CISCO_PASSWORD
```

### Nokia SR Linux

Device:

```text
S4
```

Credential:

```text
nokia-device-credentials
```

Runtime environment:

```text
NOKIA_USERNAME
NOKIA_PASSWORD
```

Only the credential required for the selected platform is injected into
the deployment process.

NetBox access additionally receives:

```text
NETBOX_URL
NETBOX_TOKEN
```

Jenkins masks supported secret values in the console log.

---

## 11. Deployment Modes

### 11.1 Dry Run

Dry run performs:

- NetBox inventory lookup
- Management-policy checks
- Platform detection
- Configuration rendering
- Deployment planning

Dry run does not open a network-device connection.

It does not change configuration.

Example:

```text
DEVICE=R3
ACTION=dry-run
```

Stage 7G proof confirmed:

```text
Safety gates: PASS
Mode: DRY RUN
No device connection was opened.
No device configuration was attempted.
```

### 11.2 Preview

Preview connects to the device and exercises the vendor-specific safe
transaction mechanism without leaving a permanent change.

Preview operations were validated on all three supported platforms.

### 11.3 Apply
Apply performs a real guarded deployment.
Apply requires:

A managed device.
An allowed Jenkins device choice.
Exact typed device confirmation.
Successful Jenkins safety-gate validation.
A successful automatic pre-apply preview.
Python deployment safety checks and vendor transaction protection.
Successful post-deployment validation before the operation is
considered successful.
If the automatic preview fails, Jenkins marks the pipeline as failed and
the live Deployment stage is skipped.

---

## 12. Vendor Preview Proof

Stage 7G validated preview execution through Jenkins for all supported
network operating-system families.

### 12.1 Arista EOS

Device:

```text
R1
```

Observed result:

```text
Platform: Arista EOS
Mode: PREVIEW
CLI commands staged: 59
SANITIZED SESSION DIFF: <no diff>
Preview session was aborted.
No configuration was committed.
```

### 12.2 Cisco IOS-XE

Device:

```text
R3
```

Observed result:

```text
Platform: Cisco IOS-XE
Mode: PREVIEW
CLI commands staged: 77
Rollback started: True
Rollback completed: True
Config restored: True
```

The before and after configuration hashes matched.

The pipeline also confirmed:

```text
Preview changes were rolled back.
No configuration was confirmed or saved.
```

### 12.3 Nokia SR Linux

Device:

```text
S4
```

Observed result:

```text
Platform: Nokia SR Linux
Mode: PREVIEW
CLI commands staged: 108
Validation passed: True
Discarded: True
SANITIZED CANDIDATE DIFF: <no diff>
```

The candidate was discarded and no configuration was committed.

---

## 13. Live CI Proof

The final Stage 7G CI proof ran against commit:

```text
a103de0
```

The Jenkins CI job successfully:

- Checked out the current `main` branch
- Reported GitHub status as pending
- Created a clean Python environment
- Installed development dependencies
- Passed Python syntax validation
- Passed all 177 regression tests
- Passed the Git diff check
- Reported GitHub status as success

Final result:

```text
CI RESULT: SUCCESS
Finished: SUCCESS
```

---

## 14. Live CD Proof
The final deployment design was live-proven through the FastAPI
Automation portal using managed Arista EOS device R1.
The operator submitted an Apply request with exact device confirmation.
FastAPI queued the parameterized Jenkins deployment job.
Jenkins first executed the automatic pre-apply preview:

automation/deploy_config.py --device R1 --preview

The preview completed without leaving a permanent configuration change.
Only after the preview passed did Jenkins execute:

automation/deploy_config.py   --device R1   --apply   --confirm-device R1
The deployment framework then performed its normal rendering,
collection, intent validation, drift analysis, vendor transaction
protection, and post-deployment validation.
Final result:

Deployment pipeline: SUCCESS
Validation passed: True
Committed: True
Persisted: True
Rolled back: False
The successful build was then visible from the FastAPI Automation page
in the selected-device deployment history.
This proves the final control path:

FastAPI Apply
    |
    v
exact confirmation
    |
    v
Jenkins safety gates
    |
    v
automatic pre-apply preview
    |
    v
guarded live apply
    |
    v
post-deployment validation
    |
    v
device-specific deployment history

---

## 15. Deployment Reports

The deployment framework writes machine-readable deployment reports
under:

```text
deployment-reports/
```

Validation reports are written under:

```text
validation-reports/
```

Generated and collected runtime configuration files are workspace
artifacts and are not authoritative source files.

Git remains authoritative for automation code, templates,
documentation, and intended configuration logic.

NetBox remains authoritative for managed network inventory and IPAM.

---

## 16. Operational Usage

### Run CI

Open:

```text
ana-network-automation-ci
```

and select:

```text
Build Now
```

The job should finish successfully before performing a production-style
deployment.

### Run Deployment Dry Run

Use:

```text
ACTION=dry-run
```

No confirmation value is required.

### Run Deployment Preview

Use:

```text
ACTION=preview
```

No confirmation value is required.

The device will be contacted, but the preview mechanism must return the
device to its original state.

### Run Live Deployment
Use:

ACTION=apply

Set:

CONFIRM_DEVICE

to exactly the same hostname selected in:

DEVICE

The FastAPI portal requires the same exact hostname confirmation before
the Jenkins request is submitted.
After the request is accepted, Jenkins automatically performs a
pre-apply Preview. The operator does not approve a second Jenkins prompt.
If Preview fails, the pipeline fails and the live Deployment stage is
skipped.
If Preview passes, Jenkins automatically continues into the guarded live
Apply operation.

---

## 17. Security Considerations

The Jenkins implementation follows these project security rules:

- Jenkins is bound only to the VM2 management address.
- Jenkins is not intentionally Internet-facing.
- User self-signup is disabled.
- Secrets are stored in Jenkins Credentials.
- Secrets are not stored in Git.
- Jenkins console masking protects supported credential values.
- Device credentials are selected by platform instead of loading all
  vendor credentials for every deployment.
- R5 is excluded from the Jenkins device parameter.
- The Python framework independently enforces the managed-device policy.
- Live apply requires exact typed confirmation and a successful automatic pre-apply preview.
- Vendor-specific rollback or confirmed-commit mechanisms protect
  configuration changes.
- Post-deployment validation is required for deployment success.
- Deployment builds are explicitly requested through FastAPI or
  Jenkins and are not triggered automatically by public repository
  activity.
CI builds are separate from deployment builds and automatically test
Git changes through Jenkins SCM polling.

---

## 18. Source-of-Truth Responsibilities

The CI/CD implementation preserves the existing authority boundaries.

NetBox is authoritative for:

- Device inventory
- Device status
- Platform metadata
- Management IP addresses
- Automation-management eligibility
- IPAM

Git is authoritative for:

- Automation source code
- Jinja2 templates
- Jenkins pipeline definitions
- Project documentation
- Golden configuration history
- Version-controlled infrastructure configuration

Jenkins is an execution and orchestration system.

Jenkins is not a source of truth.

---

## 19. Final CI/CD and FastAPI Integration Result
The completed implementation combines automatic CI, source-of-truth
management, guarded deployment, and operator-visible deployment history.
GitHub main branch
    |
    v
Jenkins CI
    |
    +--> automatic SCM change detection
    +--> Python syntax validation
    +--> automated regression tests
    +--> repository cleanliness check
    +--> GitHub commit status


FastAPI Automation portal
    |
    +--> NetBox intended-state changes
    |       |
    |       v
    |   automatic device validation
    |
    +--> Dry Run
    |
    +--> Preview
    |
    +--> Apply
            |
            v
        exact device confirmation
            |
            v
        Jenkins safety gates
            |
            v
        automatic pre-apply preview
            |
            +--> failure -> live deployment skipped
            |
            v
        guarded live deployment
            |
            v
        post-deployment validation
            |
            v
        selected-device deployment history
The current automated regression suite contains 193 passing tests,
including FastAPI workflow tests covering intent changes, automatic
validation, deployment requests, selected-device build history, and
managed-scope enforcement.
R5 remains outside the managed automation scope and is excluded from the
Jenkins deployment device choices.
New devices created through the portal are initially staged in NetBox
with automation disabled. They are not automatically validated or
deployed as managed devices.
The resulting system preserves the authority boundaries of the project:

NetBox defines network inventory, IPAM, metadata, and intended state.
Git stores automation code, templates, documentation, and pipeline
definitions.
Jenkins tests repository changes and orchestrates guarded deployments.
FastAPI provides the unified operator workflow.
The Python deployment framework performs rendering, safety checks,
vendor-specific transaction handling, rollback behavior, and
post-deployment validation.
