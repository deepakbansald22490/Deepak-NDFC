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

Production-style jump host workflow:

```bash
python3 tools/ndfc_alarm_diff.py change-start \
  --change-id CHG001234 \
  --fabric AO-DC2 \
  --notes "planned network change"

# perform the planned change

python3 tools/ndfc_alarm_diff.py change-finish --change-id CHG001234
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
