# Change Management Workflow

## 1. Purpose

The ANA Network Automation Framework separates software changes,
intended network-state changes, and live device deployment so that each
type of change can be tested and audited before it affects the managed
network.

The project uses:

- Git and GitHub for version-controlled automation source.
- NetBox for network inventory, IPAM, metadata, and intended state.
- Jenkins for Continuous Integration and guarded deployment
  orchestration.
- FastAPI as the unified operator interface.
- The Python deployment framework for rendering, validation,
  vendor-specific transaction protection, rollback behavior, and
  post-deployment validation.

---

## 2. Repository

Primary branch:

```text
main
```
Remote:

```text
origin
```

The repository is hosted on GitHub.
Automation code, Jinja2 templates, Jenkins pipeline definitions,
documentation, and tests are version-controlled in the repository.
Secrets are not stored in Git.
3. Code and Template Change Workflow
Before beginning work:

```bash
git status
git pull
```
After making a code, template, test, or documentation change, the normal
local checks include:
```bash
python3 -m pytest -q
python3 -m compileall -q automation webapp tests
git diff --check
```
After review, changes are committed and pushed to the main branch.
Example:

```bash
git add <files>
git commit -m "Describe the change"
git push origin main
```
4. Automatic Jenkins CI
The Jenkins CI job monitors the Git repository for changes.
When Jenkins detects a new commit on main, it automatically:

Checks out the current repository state.
Creates a clean Python virtual environment.
Installs development dependencies.
Compiles the Python source.
Runs the automated regression suite.
Runs git diff --check.
Reports the Jenkins CI result to GitHub.
The CI job does not deploy configuration to network devices.
The CI and deployment pipelines are intentionally separate.
5. Intended Network-State Changes
Managed-device intent is changed through the FastAPI Automation portal.
Supported operator changes include:

Configuration profile.
Routing-protocol intent.
Managed WAN addresses.
Device site metadata.
FastAPI writes the requested intended state to NetBox.
After a successful managed-device intent update, FastAPI automatically
runs device-specific validation.
The workflow is:

```text
FastAPI intent change
        |
        v
NetBox updated
        |
        v
automatic validation
        |
        +--> PASS
        |
        +--> drift or validation failure
```
A validation failure does not automatically undo the NetBox intended
state.
This is deliberate because newly changed intended state may differ from
the currently running device configuration until the operator previews
or applies the change.
No live deployment is automatically started after an intent change.
6. Deployment Actions
The Automation portal exposes three deployment actions for managed
devices:
Dry Run
Preview
Apply
Dry Run
Dry Run performs rendering and planning without opening a device
configuration session.
Preview
Preview connects to the target device and exercises the platform's safe
transaction mechanism without leaving a permanent configuration change.
Apply
Apply performs a guarded live deployment.
The operator must type the exact selected hostname before FastAPI will
queue an Apply operation.
The Jenkins workflow is:

```text
Apply request
    |
    v
exact device confirmation
    |
    v
managed-device safety gates
    |
    v
platform credential selection
    |
    v
automatic pre-apply Preview
    |
    +--> Preview failure
    |       |
    |       v
    |   pipeline FAILURE
    |   live Deployment skipped
    |
    v
Preview success
    |
    v
guarded live Apply
    |
    v
post-deployment validation
```
There is no second Jenkins human-approval prompt in the final workflow.
7. Deployment Safety and Rollback
The common deployment framework independently verifies that the target
device is inside the managed automation scope.
Vendor-specific transaction protection is used during preview and live
deployment.
The framework validates the resulting device state after deployment.
Where the network operating system supports transactional or confirmed
configuration behavior, that mechanism is used to prevent an unsafe
change from being permanently accepted.
A post-deployment validation failure is reported explicitly rather than
silently treated as success.
8. Deployment History
Jenkins retains deployment build records.
The FastAPI Automation portal retrieves recent Jenkins builds for the
currently selected device and displays:
Build number.
Deployment action.
Build state.
Final result.
History is filtered by device so that activity for another managed
device is not presented as the selected device's latest deployment.
Historical failed or aborted Jenkins builds remain visible for audit
purposes.
9. New Device Onboarding
A device created through the FastAPI onboarding workflow is initially
created in NetBox as:
status = staged
automation_managed = false
Its management address, platform, role, site, routing intent, and
configuration profile can be recorded without immediately placing the
device under live automation control.
The onboarding workflow does not automatically run live validation or
deployment against the staged device.
This prevents a newly entered or not-yet-reachable device from
accidentally entering the managed deployment workflow.
10. Managed Scope
The managed devices are:

R1
R2
R3
R4
S1
S2
S3
S4
R5 is intentionally outside the automation-managed scope.
R5 is excluded from:

Managed configuration deployment.
Jenkins deployment choices.
Managed intent updates.
Managed validation and golden-configuration workflows.
The Python automation framework also independently enforces this policy,
so exclusion does not rely only on the web interface.
11. Source-of-Truth Responsibilities
NetBox is authoritative for:

Device inventory.
Device status.
Platform and role metadata.
Site membership.
Management addressing.
IPAM.
Automation-management eligibility.
Routing-protocol intent.
Configuration-profile intent.
Git is authoritative for:

Python automation source code.
Jinja2 templates.
Jenkins pipeline definitions.
Automated tests.
Documentation.
Version history.
Jenkins is responsible for:

Automatic CI execution.
GitHub commit-status reporting.
Deployment orchestration.
Credential injection.
Deployment build history.
FastAPI is the operator interface that connects these components.
12. Operational Change Sequence
The normal managed change sequence is:

1. Change intended state in FastAPI / NetBox
2. Automatic validation runs
3. Review validation result
4. Run Dry Run if needed
5. Run Preview
6. Confirm exact target hostname for Apply
7. Jenkins runs automatic pre-apply Preview
8. Live Apply runs only if Preview passes
9. Post-deployment validation runs
10. Review Jenkins result and device-specific history
For repository changes:

1. Edit code/template/documentation
2. Run local tests
3. Commit to Git
4. Push to GitHub
5. Jenkins CI detects the change automatically
6. Review GitHub/Jenkins CI result
This separation prevents a Git commit or an intended-state edit from
automatically becoming an uncontrolled live network change.
