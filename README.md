# Deepak NDFC Lab

This repository contains Cisco NDFC learning work across Ansible playbooks and Python operational tools.


- Cisco NDFC
- Ansible
- Python
- `cisco.dcnm`
- REST API workflows
- Read-only prechecks before configuration changes

## Current Focus

The current Ansible playbooks focus on NDFC fabric, VRF, network, inventory, and port validation workflows.

The Python tool focuses on a NOC-style before/after change check:

1. Capture alarms and events before a planned change.
2. Capture alarms and events after the change.
3. Compare both snapshots.
4. Save the terminal report under a per-change run folder.

## Python Jump Host Workflow

The Python workflow is intended to run from a controlled jump host.

```bash
cd python

cp config/ndfc.yml.example config/ndfc.yml
chmod 600 config/ndfc.yml

python3 tools/ndfc_alarm_diff.py change-start \
  --change-id CHG001234 \
  --fabric AO-DC2 \
  --notes "planned network change"

# perform the planned change

python3 tools/ndfc_alarm_diff.py change-finish --change-id CHG001234
```

The tool writes output like this:

```text
python/runs/CHG001234/
  metadata.json
  before.json
  before.events.json
  after.json
  after.events.json
  report.txt
```

## Ansible Workflow

The current Ansible playbooks include workflows such as:

1. Query network attachment state from NDFC.
2. Confirm the target network exists for the target leaf.
3. Read `isLanAttached` and `lanAttachState`.
4. Test NDFC REST attachment/deployment behavior in a lab.

## Repository Structure

```text
playbooks/
  NDFC_Module/
    Attach_network_without_ports.yml
inventory/
  inventory.example.yml
  group_vars/
    ndfc/
      vars.example.yml
  host_vars/
    DC1-Leaf1.example.yml
vars/
  network_attach_request.example.yml
python/
  tools/
    ndfc_alarm_diff.py
  config/
    ndfc.yml.example
```

## Safety

This repo contains only example inventory and variable files.

Do not commit:

- real NDFC passwords
- vault files
- private inventory
- real tokens
- local virtual environments
- `python/config/ndfc.yml`
- `python/runs/`
- `python/snapshots/`

## Example Usage

Copy the example files before using the playbook in a lab:

```bash
cp inventory/inventory.example.yml inventory/inventory.yml
cp inventory/group_vars/ndfc/vars.example.yml inventory/group_vars/ndfc/vars.yml
cp inventory/host_vars/DC1-Leaf1.example.yml inventory/host_vars/DC1-Leaf1.yml
cp vars/network_attach_request.example.yml vars/network_attach_request.yml
```

Set credentials with environment variables:

```bash
export NDFC_USER='admin'
export NDFC_PASSWORD='your-password'
```

Run a playbook:

```bash
ansible-playbook -i inventory/inventory.yml playbooks/NDFC_Module/08_attach_network_without_ports.yml
```

## Notes

These playbooks are for lab learning and API behavior validation. Production workflows should add stronger validation, idempotency checks, change control handling, and post-change verification.
