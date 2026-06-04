#!/usr/bin/env python3
"""NDFC alarm snapshot and before/after diff utility."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import getpass
import io
import json
import pathlib
import sys
from typing import Any
from urllib.parse import urljoin

import requests
import urllib3
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "ndfc.yml"


@dataclasses.dataclass
class NdfcConfig:
    base_url: str
    username: str
    password: str
    domain: str = "local"
    verify_ssl: bool = False
    timeout: int = 30
    alarm_endpoint: str = ""
    event_endpoint: str = ""
    candidate_endpoints: list[str] = dataclasses.field(default_factory=list)
    snapshot_dir: pathlib.Path = ROOT / "snapshots"
    run_dir: pathlib.Path = ROOT / "runs"


class NdfcClient:
    def __init__(self, config: NdfcConfig):
        self.config = config
        self.session = requests.Session()
        self.session.verify = config.verify_ssl
        self.timeout = config.timeout
        if not config.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def login(self) -> None:
        attempts = [
            {
                "userName": self.config.username,
                "userPasswd": self.config.password,
                "domain": self.config.domain,
            },
            {
                "username": self.config.username,
                "password": self.config.password,
                "domain": self.config.domain,
            },
        ]

        errors = []
        for payload in attempts:
            try:
                response = self.session.post(
                    self.url("/login"),
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                errors.append(str(exc))
                continue

            if not response.ok:
                errors.append(f"HTTP {response.status_code}: {response.text[:160]}")
                continue

            token = self._extract_token(response)
            if not token:
                errors.append("login succeeded but token was not found in response")
                continue

            self.session.headers.update(
                {
                    "Authorization": f"Bearer {token}",
                    "Cookie": f"AuthCookie={token}",
                    "AuthCookie": token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )
            return

        raise RuntimeError("NDFC login failed: " + " | ".join(errors))

    def get(self, path: str) -> tuple[int, Any]:
        response = self.session.get(self.url(path), timeout=self.timeout)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    @staticmethod
    def _extract_token(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            body = {}

        token = (
            body.get("token")
            or body.get("jwttoken")
            or body.get("Dcnm-Token")
            or body.get("DATA", {}).get("token")
            or body.get("DATA", {}).get("jwttoken")
        )
        if token:
            return str(token)

        cookie_token = response.cookies.get("AuthCookie")
        if cookie_token:
            return cookie_token

        return ""


def load_config(path: pathlib.Path) -> NdfcConfig:
    if not path.exists():
        raise SystemExit(
            f"Config file not found: {path}\n"
            f"Create it from config/ndfc.yml.example and update the values."
        )

    data = yaml.safe_load(path.read_text()) or {}
    ndfc = data.get("ndfc", {})
    alarms = data.get("alarms", {})
    events = data.get("events", {})
    output = data.get("output", {})

    password = ndfc.get("password", "")
    if password == "PROMPT":
        password = getpass.getpass("NDFC password: ")

    snapshot_dir = pathlib.Path(output.get("snapshot_dir", "snapshots"))
    if not snapshot_dir.is_absolute():
        snapshot_dir = ROOT / snapshot_dir

    run_dir = pathlib.Path(output.get("run_dir", "runs"))
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir

    config = NdfcConfig(
        base_url=str(ndfc.get("base_url", "")).rstrip("/"),
        username=str(ndfc.get("username", "")),
        password=str(password),
        domain=str(ndfc.get("domain", "local")),
        verify_ssl=bool(ndfc.get("verify_ssl", False)),
        timeout=int(ndfc.get("timeout", 30)),
        alarm_endpoint=str(alarms.get("endpoint", "")),
        event_endpoint=str(events.get("endpoint", "")),
        candidate_endpoints=list(alarms.get("candidate_endpoints", [])),
        snapshot_dir=snapshot_dir,
        run_dir=run_dir,
    )

    missing = []
    if not config.base_url:
        missing.append("ndfc.base_url")
    if not config.username:
        missing.append("ndfc.username")
    if not config.password or config.password == "CHANGE_ME":
        missing.append("ndfc.password")
    if missing:
        raise SystemExit("Missing config values: " + ", ".join(missing))

    return config


def unwrap_records(body: Any) -> list[dict[str, Any]]:
    candidates = []
    if isinstance(body, dict):
        candidates.extend(
            [
                body.get("DATA"),
                body.get("lastOperDataObject"),
                body.get("data"),
                body.get("items"),
                body.get("records"),
                body.get("alarms"),
                body.get("faults"),
                body.get("response"),
            ]
        )
    else:
        candidates.append(body)

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            for value in candidate.values():
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def alarm_key(alarm: dict[str, Any]) -> str:
    fields = [
        first_value(alarm, ["fabric", "fabricName", "fabric_name", "siteName", "groupName"]),
        first_value(alarm, ["device", "deviceName", "switchName", "nodeName", "hostName", "source"]),
        first_value(alarm, ["severity", "level", "priority"]),
        first_value(alarm, ["type", "category", "alarmType", "faultType", "mnemonic"]),
        first_value(alarm, ["object", "entity", "affectedObject", "dn", "resource", "deviceAttributes", "sensorIndex"]),
        first_value(alarm, ["message", "description", "details", "summary"]),
    ]
    normalized = [normalize_text(value) for value in fields]
    return "|".join(normalized)


def normalize_alarm(alarm: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": alarm_key(alarm),
        "severity": first_value(alarm, ["severity", "level", "priority"], "unknown"),
        "fabric": first_value(alarm, ["fabric", "fabricName", "fabric_name", "siteName", "groupName"], ""),
        "device": first_value(alarm, ["device", "deviceName", "switchName", "nodeName", "hostName", "source"], ""),
        "type": first_value(alarm, ["type", "category", "alarmType", "faultType", "mnemonic"], ""),
        "object": first_value(alarm, ["object", "entity", "affectedObject", "dn", "resource", "deviceAttributes", "sensorIndex"], ""),
        "message": first_value(alarm, ["message", "description", "details", "summary"], ""),
        "raw": alarm,
    }


def event_key(event: dict[str, Any]) -> str:
    fields = [
        first_value(event, ["id", "eventId", "eventID", "Event ID", "uuid"]),
        first_value(event, ["fabric", "fabricName", "fabric_name", "groupName", "Group", "scope"]),
        first_value(event, ["device", "deviceName", "switchName", "Switch", "nodeName", "hostName", "source", "Source", "EventSwitch"]),
        first_value(event, ["severity", "Severity", "eventSeverity", "EventSeverity", "level"]),
        first_value(event, ["category", "eventType", "EventType", "type", "Type", "mnemonic"]),
        first_value(event, ["deviceAttributes", "deviceAttr", "EventAttribute", "object", "entity", "affectedObject"]),
        first_value(event, ["description", "Description", "message", "details", "summary", "MoreData"]),
    ]
    normalized = [normalize_text(value) for value in fields if value not in (None, "")]
    return "|".join(normalized)


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": event_key(event),
        "severity": first_value(event, ["severity", "Severity", "eventSeverity", "EventSeverity", "level"], "unknown"),
        "fabric": first_value(event, ["fabric", "fabricName", "fabric_name", "groupName", "Group", "scope"], ""),
        "device": first_value(event, ["device", "deviceName", "switchName", "Switch", "nodeName", "hostName", "source", "Source", "EventSwitch"], ""),
        "type": first_value(event, ["category", "eventType", "EventType", "type", "Type", "mnemonic"], ""),
        "object": first_value(event, ["deviceAttributes", "deviceAttr", "EventAttribute", "object", "entity", "affectedObject"], ""),
        "message": first_value(event, ["description", "Description", "message", "details", "summary", "MoreData"], ""),
        "timestamp": first_value(event, ["Last Seen", "LastSeenLong", "serverTimeStamp", "timeStamp", "timestamp", "eventTime"], ""),
        "raw": event,
    }


def first_value(data: dict[str, Any], keys: list[str], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).lower().split())


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def cmd_probe(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = NdfcClient(config)
    client.login()

    if args.endpoint:
        endpoints = [args.endpoint]
    else:
        endpoints = []
        if config.alarm_endpoint:
            endpoints.append(config.alarm_endpoint)
        endpoints.extend(endpoint for endpoint in config.candidate_endpoints if endpoint not in endpoints)

    print("Endpoint probe results")
    print("======================")
    for endpoint in endpoints:
        status, body = client.get(endpoint)
        records = unwrap_records(body)
        body_type = type(body).__name__
        print(f"{status:>3}  records={len(records):<5} type={body_type:<5} {endpoint}")
        if args.show_sample and records:
            print(json.dumps(records[0], indent=2, sort_keys=True)[:2000])
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.endpoint:
        config.alarm_endpoint = args.endpoint
    client = NdfcClient(config)
    client.login()
    path, snapshot = capture_alarm_snapshot(config, client, args.name)

    print(f"Snapshot saved: {path}")
    print(f"Alarm count: {snapshot['count']}")
    print_severity_summary(snapshot["alarms"])
    return 0


def cmd_event_snapshot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.endpoint:
        config.event_endpoint = args.endpoint
    client = NdfcClient(config)
    client.login()
    path, snapshot = capture_event_snapshot(config, client, args.name)

    print(f"Event snapshot saved: {path}")
    print(f"Event count: {snapshot['count']}")
    print_severity_summary(snapshot["events"])
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    before = load_snapshot(args.before)
    after = load_snapshot(args.after)

    diff = compare_records(before.get("alarms", []), after.get("alarms", []))

    print("NDFC Alarm Diff")
    print("===============")
    print_diff_header(before, after, args.before, args.after, diff)
    print_alarm_diff_sections(diff, args.limit)
    return 0


def cmd_event_diff(args: argparse.Namespace) -> int:
    before = load_snapshot(args.before)
    after = load_snapshot(args.after)

    diff = compare_records(before.get("events", []), after.get("events", []))

    print("NDFC Event Diff")
    print("===============")
    print_diff_header(before, after, args.before, args.after, diff)
    print_event_diff_sections(diff, args.limit)
    return 0


def cmd_change_snapshot(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = NdfcClient(config)
    client.login()

    alarm_path, alarm_snapshot = capture_alarm_snapshot(config, client, args.name)
    event_path, event_snapshot = capture_event_snapshot(config, client, args.name)

    print("NDFC Change Snapshot")
    print("====================")
    print(f"Alarm snapshot: {alarm_path}")
    print(f"Alarm count:    {alarm_snapshot['count']}")
    print_severity_summary(alarm_snapshot["alarms"])
    print("")
    print(f"Event snapshot: {event_path}")
    print(f"Event count:    {event_snapshot['count']}")
    print_severity_summary(event_snapshot["events"])
    return 0


def cmd_change_diff(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    before_alarm = load_snapshot(resolve_alarm_snapshot(args.before, config.snapshot_dir))
    after_alarm = load_snapshot(resolve_alarm_snapshot(args.after, config.snapshot_dir))
    before_event = load_snapshot(resolve_event_snapshot(args.before, config.snapshot_dir))
    after_event = load_snapshot(resolve_event_snapshot(args.after, config.snapshot_dir))

    alarm_diff = compare_records(before_alarm.get("alarms", []), after_alarm.get("alarms", []))
    event_diff = compare_records(before_event.get("events", []), after_event.get("events", []))

    print("NDFC Change Diff")
    print("================")
    print("")
    print("Alarm Diff")
    print("----------")
    print_diff_header(before_alarm, after_alarm, args.before, args.after, alarm_diff)
    print_alarm_diff_sections(alarm_diff, args.limit)
    print("")
    print("Event Diff")
    print("----------")
    print_diff_header(before_event, after_event, args.before, args.after, event_diff)
    print_event_diff_sections(event_diff, args.limit)
    return 0


def cmd_change_start(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    change_id = normalize_change_id(args.change_id)
    run_path = config.run_dir / change_id
    before_alarm_path = run_path / "before.json"
    before_event_path = run_path / "before.events.json"
    metadata_path = run_path / "metadata.json"

    if run_path.exists() and not args.force:
        raise SystemExit(f"Run already exists: {run_path}. Use --force only if you want to overwrite before snapshots.")

    client = NdfcClient(config)
    client.login()

    metadata = {
        "change_id": change_id,
        "fabric": args.fabric or "",
        "operator": args.operator or getpass.getuser(),
        "notes": args.notes or "",
        "started_at": now_utc(),
        "finished_at": "",
    }

    _, alarm_snapshot = capture_alarm_snapshot(config, client, "before", run_path, "before.json")
    _, event_snapshot = capture_event_snapshot(config, client, "before", run_path, "before.events.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))

    print("NDFC Change Start")
    print("=================")
    print(f"Change ID:      {change_id}")
    print(f"Run folder:     {run_path}")
    print(f"Metadata:       {metadata_path}")
    print(f"Alarm snapshot: {before_alarm_path}  count={alarm_snapshot['count']}")
    print(f"Event snapshot: {before_event_path}  count={event_snapshot['count']}")
    print("")
    print("After the change, run:")
    print(f"python3 tools/ndfc_alarm_diff.py change-finish --change-id {change_id}")
    return 0


def cmd_change_finish(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    change_id = normalize_change_id(args.change_id)
    run_path = config.run_dir / change_id
    metadata_path = run_path / "metadata.json"
    report_path = run_path / "report.txt"

    before_alarm_path = run_path / "before.json"
    before_event_path = run_path / "before.events.json"
    if not before_alarm_path.exists() or not before_event_path.exists():
        raise SystemExit(f"Before snapshots not found for change {change_id}. Run change-start first.")

    metadata = load_metadata(metadata_path)
    metadata["finished_at"] = now_utc()

    client = NdfcClient(config)
    client.login()
    _, after_alarm = capture_alarm_snapshot(config, client, "after", run_path, "after.json")
    _, after_event = capture_event_snapshot(config, client, "after", run_path, "after.events.json")

    before_alarm = load_snapshot(before_alarm_path)
    before_event = load_snapshot(before_event_path)
    report = build_change_report(metadata, before_alarm, after_alarm, before_event, after_event, args.limit)

    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True))
    report_path.write_text(report)

    print(report, end="")
    print("")
    print(f"Report saved: {report_path}")
    return 0


def load_snapshot(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def capture_alarm_snapshot(
    config: NdfcConfig,
    client: NdfcClient,
    name: str,
    output_dir: pathlib.Path | None = None,
    filename: str | None = None,
) -> tuple[pathlib.Path, dict[str, Any]]:
    if not config.alarm_endpoint:
        raise SystemExit("No alarm endpoint configured. Run probe first, then set alarms.endpoint in config/ndfc.yml.")

    status, body = client.get(config.alarm_endpoint)
    if status < 200 or status >= 300:
        raise SystemExit(f"Alarm endpoint failed with HTTP {status}: {body}")

    records = unwrap_records(body)
    normalized = [normalize_alarm(record) for record in records]
    snapshot = {
        "name": name,
        "type": "alarms",
        "created_at": now_utc(),
        "endpoint": config.alarm_endpoint,
        "count": len(normalized),
        "alarms": normalized,
    }
    path = write_snapshot(output_dir or config.snapshot_dir, filename or f"{name}.json", snapshot)
    return path, snapshot


def capture_event_snapshot(
    config: NdfcConfig,
    client: NdfcClient,
    name: str,
    output_dir: pathlib.Path | None = None,
    filename: str | None = None,
) -> tuple[pathlib.Path, dict[str, Any]]:
    if not config.event_endpoint:
        raise SystemExit("No event endpoint configured. Set events.endpoint in config/ndfc.yml.")

    status, body = client.get(config.event_endpoint)
    if status < 200 or status >= 300:
        raise SystemExit(f"Event endpoint failed with HTTP {status}: {body}")

    records = unwrap_records(body)
    normalized = [normalize_event(record) for record in records]
    snapshot = {
        "name": name,
        "type": "events",
        "created_at": now_utc(),
        "endpoint": config.event_endpoint,
        "count": len(normalized),
        "events": normalized,
    }
    path = write_snapshot(output_dir or config.snapshot_dir, filename or f"{name}.events.json", snapshot)
    return path, snapshot


def write_snapshot(snapshot_dir: pathlib.Path, filename: str, snapshot: dict[str, Any]) -> pathlib.Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / filename
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    return path


def resolve_alarm_snapshot(value: pathlib.Path, snapshot_dir: pathlib.Path) -> pathlib.Path:
    return resolve_snapshot_path(value, snapshot_dir, ".json")


def resolve_event_snapshot(value: pathlib.Path, snapshot_dir: pathlib.Path) -> pathlib.Path:
    return resolve_snapshot_path(value, snapshot_dir, ".events.json")


def resolve_snapshot_path(value: pathlib.Path, snapshot_dir: pathlib.Path, suffix: str) -> pathlib.Path:
    if value.exists():
        return value

    named_path = snapshot_dir / f"{value}{suffix}"
    if named_path.exists():
        return named_path

    raise SystemExit(f"Snapshot not found: {value} or {named_path}")


def compare_records(before_records: list[dict[str, Any]], after_records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    before_map = {record["key"]: record for record in before_records}
    after_map = {record["key"]: record for record in after_records}

    before_keys = set(before_map)
    after_keys = set(after_map)

    return {
        "new": [after_map[key] for key in sorted(after_keys - before_keys)],
        "cleared": [before_map[key] for key in sorted(before_keys - after_keys)],
        "unchanged": [after_map[key] for key in sorted(before_keys & after_keys)],
    }


def normalize_change_id(change_id: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in change_id.strip())
    cleaned = cleaned.strip("._-")
    if not cleaned:
        raise SystemExit("Change ID cannot be empty.")
    return cleaned


def load_metadata(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "change_id": path.parent.name,
            "fabric": "",
            "operator": "",
            "notes": "",
            "started_at": "",
            "finished_at": "",
        }
    return json.loads(path.read_text())


def build_change_report(
    metadata: dict[str, Any],
    before_alarm: dict[str, Any],
    after_alarm: dict[str, Any],
    before_event: dict[str, Any],
    after_event: dict[str, Any],
    limit: int,
) -> str:
    alarm_diff = compare_records(before_alarm.get("alarms", []), after_alarm.get("alarms", []))
    event_diff = compare_records(before_event.get("events", []), after_event.get("events", []))

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        print("NDFC Change Report")
        print("==================")
        print(f"Change ID:   {metadata.get('change_id', '')}")
        print(f"Fabric:      {metadata.get('fabric', '') or 'not-specified'}")
        print(f"Operator:    {metadata.get('operator', '') or 'not-specified'}")
        print(f"Started at:  {metadata.get('started_at', '') or 'not-recorded'}")
        print(f"Finished at: {metadata.get('finished_at', '') or 'not-recorded'}")
        if metadata.get("notes"):
            print(f"Notes:       {metadata['notes']}")
        print("")
        print("Alarm Diff")
        print("----------")
        print_diff_header(before_alarm, after_alarm, pathlib.Path("before.json"), pathlib.Path("after.json"), alarm_diff)
        print_alarm_diff_sections(alarm_diff, limit)
        print("")
        print("Event Diff")
        print("----------")
        print_diff_header(before_event, after_event, pathlib.Path("before.events.json"), pathlib.Path("after.events.json"), event_diff)
        print_event_diff_sections(event_diff, limit)
    return buffer.getvalue()


def print_diff_header(
    before: dict[str, Any],
    after: dict[str, Any],
    before_label: pathlib.Path,
    after_label: pathlib.Path,
    diff: dict[str, list[dict[str, Any]]],
) -> None:
    before_count = len(diff["cleared"]) + len(diff["unchanged"])
    after_count = len(diff["new"]) + len(diff["unchanged"])
    print(f"Before: {before.get('name', before_label)}  {before.get('created_at', '')}  count={before_count}")
    print(f"After:  {after.get('name', after_label)}  {after.get('created_at', '')}  count={after_count}")
    print("")


def print_alarm_diff_sections(diff: dict[str, list[dict[str, Any]]], limit: int) -> None:
    print(f"New alarms:       {len(diff['new'])}")
    print(f"Cleared alarms:   {len(diff['cleared'])}")
    print(f"Unchanged alarms: {len(diff['unchanged'])}")
    print("")
    print_alarm_section("New alarms", diff["new"], limit)
    print_alarm_section("Cleared alarms", diff["cleared"], limit)
    print_alarm_section("Unchanged alarms", diff["unchanged"], limit)


def print_event_diff_sections(diff: dict[str, list[dict[str, Any]]], limit: int) -> None:
    print(f"New events:       {len(diff['new'])}")
    print(f"Cleared events:   {len(diff['cleared'])}")
    print(f"Unchanged events: {len(diff['unchanged'])}")
    print("")
    print_record_section("New events", diff["new"], limit, format_event)
    print_record_section("Cleared events", diff["cleared"], limit, format_event)
    print_record_section("Unchanged events", diff["unchanged"], limit, format_event)


def print_severity_summary(alarms: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for alarm in alarms:
        severity = str(alarm.get("severity") or "unknown").upper()
        counts[severity] = counts.get(severity, 0) + 1
    if not counts:
        print("Severity summary: none")
        return
    print("Severity summary:")
    for severity in sorted(counts):
        print(f"  {severity}: {counts[severity]}")


def print_alarm_section(title: str, alarms: list[dict[str, Any]], limit: int) -> None:
    print_record_section(title, alarms, limit, format_alarm)


def print_record_section(title: str, records: list[dict[str, Any]], limit: int, formatter) -> None:
    print(title)
    print("-" * len(title))
    if not records:
        print("None")
        print("")
        return

    shown = records[:limit]
    for record in shown:
        print(formatter(record))
    if len(records) > limit:
        print(f"... {len(records) - limit} more not shown")
    print("")


def format_alarm(alarm: dict[str, Any]) -> str:
    severity = str(alarm.get("severity") or "unknown").upper()
    device = alarm.get("device") or "unknown-device"
    object_name = alarm.get("object") or alarm.get("type") or "unknown-object"
    message = alarm.get("message") or "no message"
    fabric = alarm.get("fabric") or "unknown-fabric"
    return f"- {severity} | {fabric} | {device} | {object_name} | {message}"


def format_event(event: dict[str, Any]) -> str:
    severity = str(event.get("severity") or "unknown").upper()
    timestamp = event.get("timestamp") or "no-time"
    device = event.get("device") or "unknown-device"
    object_name = event.get("object") or event.get("type") or "unknown-object"
    message = event.get("message") or "no message"
    fabric = event.get("fabric") or "unknown-fabric"
    return f"- {severity} | {timestamp} | {fabric} | {device} | {object_name} | {message}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NDFC alarm snapshot and before/after diff utility")
    parser.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG, help="Path to config/ndfc.yml")

    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Probe configured candidate alarm endpoints")
    probe.add_argument("--endpoint", help="Probe one endpoint path instead of all candidates")
    probe.add_argument("--show-sample", action="store_true", help="Print first returned record for endpoints with records")
    probe.set_defaults(func=cmd_probe)

    snapshot = subparsers.add_parser("snapshot", help="Capture current active alarms into a JSON snapshot")
    snapshot.add_argument("--name", required=True, help="Snapshot name, e.g. before-change")
    snapshot.add_argument("--endpoint", help="Override alarms.endpoint from config")
    snapshot.set_defaults(func=cmd_snapshot)

    diff = subparsers.add_parser("diff", help="Compare before and after alarm snapshots")
    diff.add_argument("--before", type=pathlib.Path, required=True, help="Before snapshot JSON")
    diff.add_argument("--after", type=pathlib.Path, required=True, help="After snapshot JSON")
    diff.add_argument("--limit", type=int, default=50, help="Max alarms to print per section")
    diff.set_defaults(func=cmd_diff)

    event_snapshot = subparsers.add_parser("event-snapshot", help="Capture current NDFC events into a JSON snapshot")
    event_snapshot.add_argument("--name", required=True, help="Snapshot name, e.g. before-change")
    event_snapshot.add_argument("--endpoint", help="Override events.endpoint from config")
    event_snapshot.set_defaults(func=cmd_event_snapshot)

    event_diff = subparsers.add_parser("event-diff", help="Compare before and after event snapshots")
    event_diff.add_argument("--before", type=pathlib.Path, required=True, help="Before event snapshot JSON")
    event_diff.add_argument("--after", type=pathlib.Path, required=True, help="After event snapshot JSON")
    event_diff.add_argument("--limit", type=int, default=50, help="Max events to print per section")
    event_diff.set_defaults(func=cmd_event_diff)

    change_snapshot = subparsers.add_parser("change-snapshot", help="Capture alarm and event snapshots for a change")
    change_snapshot.add_argument("--name", required=True, help="Snapshot name, e.g. before-change")
    change_snapshot.set_defaults(func=cmd_change_snapshot)

    change_diff = subparsers.add_parser("change-diff", help="Compare alarm and event snapshots for a change")
    change_diff.add_argument("--before", type=pathlib.Path, required=True, help="Before snapshot name or path")
    change_diff.add_argument("--after", type=pathlib.Path, required=True, help="After snapshot name or path")
    change_diff.add_argument("--limit", type=int, default=50, help="Max records to print per section")
    change_diff.set_defaults(func=cmd_change_diff)

    change_start = subparsers.add_parser("change-start", help="Start a jump-host style change validation run")
    change_start.add_argument("--change-id", required=True, help="Change ID, e.g. CHG001234")
    change_start.add_argument("--fabric", help="Fabric or site name for metadata")
    change_start.add_argument("--operator", help="Operator name for metadata")
    change_start.add_argument("--notes", help="Short notes for the change run")
    change_start.add_argument("--force", action="store_true", help="Overwrite existing before snapshots for this change")
    change_start.set_defaults(func=cmd_change_start)

    change_finish = subparsers.add_parser("change-finish", help="Finish a change run, diff before/after, and write report.txt")
    change_finish.add_argument("--change-id", required=True, help="Change ID used with change-start")
    change_finish.add_argument("--limit", type=int, default=50, help="Max records to print per section")
    change_finish.set_defaults(func=cmd_change_finish)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except requests.RequestException as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
