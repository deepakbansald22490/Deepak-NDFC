# Learning Python and NDFC

- take an alarm and event snapshot before a change
- take an alarm and event snapshot after a change
- compare both snapshots in the terminal

## Setup

Copy the example config and update it with your lab NDFC values:

```bash
python3 -m pip install -r requirements.txt
cp config/ndfc.yml.example config/ndfc.yml
chmod 600 config/ndfc.yml
```

Do not commit or share `config/ndfc.yml`.

## Commands

Probe candidate read-only alarm endpoints:

```bash
python3 tools/ndfc_alarm_diff.py probe
```

How to run the Script:

```bash
python3 tools/ndfc_alarm_diff.py change-start \
  --change-id CHG004567 \
  --notes "planned network change"

# perform the planned change

python3 tools/ndfc_alarm_diff.py change-finish --change-id CHG004567
```

The tool stores all output for that change under:

```text
runs/CHG001234/
  metadata.json
  before.json
  before.events.json
  after.json
  after.events.json
  report.txt
```

## Switch Log Collection

Use Ansible to collect full switch logs from the target switches, then use Python to filter the saved logs by change-window timestamp.

Copy the example inventory and update it with the switches you want to check:

```bash
cp inventory/switches.example.yml inventory/switches.yml
```

Set switch credentials as environment variables:

```bash
export NXOS_USER='admin'
export NXOS_PASSWORD='your-password'
```

Collect full logs from every switch in the `target_switches` group:

```bash
ansible-playbook -i inventory/switches.yml playbooks/collect_switch_logs.yml \
  -e change_id=CHG001234
```

The playbook runs:

```text
show logging logfile
```

Raw switch logs are saved under:

```text
runs/CHG001234/switch-logs/
  DC1-LEAF-101.log
  DC1-LEAF-102.log
```

Generate a filtered report for the change window:

```bash
python3 tools/switch_log_report.py \
  --change-id CHG001234 \
  --start "2026 Jun 4 01:51:29" \
  --end "2026 Jun 4 02:30:00"
```

The report is saved here:

```text
runs/CHG001234/switch-log-report.txt
```

The lower-level commands are still useful while learning or troubleshooting.

Take combined change snapshots manually:

```bash
python3 tools/ndfc_alarm_diff.py change-snapshot --name before-change
python3 tools/ndfc_alarm_diff.py change-snapshot --name after-change
```

Compare alarms and events together manually:

```bash
python3 tools/ndfc_alarm_diff.py change-diff \
  --before before-change \
  --after after-change
```

Take alarm snapshots:

```bash
python3 tools/ndfc_alarm_diff.py snapshot --name before-change
python3 tools/ndfc_alarm_diff.py snapshot --name after-change
```

Compare alarm snapshots:

```bash
python3 tools/ndfc_alarm_diff.py diff \
  --before snapshots/before-change.json \
  --after snapshots/after-change.json
```

Use the event-only commands when you only want to inspect NDFC events.

```bash
python3 tools/ndfc_alarm_diff.py event-snapshot --name before-change
python3 tools/ndfc_alarm_diff.py event-snapshot --name after-change

python3 tools/ndfc_alarm_diff.py event-diff \
  --before snapshots/before-change.events.json \
  --after snapshots/after-change.events.json
```
