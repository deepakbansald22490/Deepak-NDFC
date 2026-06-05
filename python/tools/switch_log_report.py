#!/usr/bin/env python3
"""Filter collected NX-OS switch logs by change-window timestamp."""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
from dataclasses import dataclass


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = ROOT / "runs"

LOG_TS_RE = re.compile(
    r"<?(?P<timestamp>\d{4}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})>?"
)
SYSLOG_SEVERITY_RE = re.compile(r"%[A-Z0-9_]+-(?P<severity>[0-7])-")

SEVERITY_NAMES = {
    "0": "EMERGENCY",
    "1": "ALERT",
    "2": "CRITICAL",
    "3": "ERROR",
    "4": "WARNING",
    "5": "NOTICE",
    "6": "INFO",
    "7": "DEBUG",
}


@dataclass
class LogEntry:
    switch: str
    timestamp: dt.datetime
    severity: str
    line: str


def parse_timestamp(value: str) -> dt.datetime:
    normalized = " ".join(value.strip().strip("<>").split())
    try:
        return dt.datetime.strptime(normalized, "%Y %b %d %H:%M:%S")
    except ValueError as exc:
        raise SystemExit(
            f"Invalid timestamp: {value}\n"
            "Use a format like: '2026 Jun 4 01:51:29'"
        ) from exc


def extract_timestamp(line: str) -> dt.datetime | None:
    match = LOG_TS_RE.search(line)
    if not match:
        return None
    return parse_timestamp(match.group("timestamp"))


def extract_severity(line: str) -> str:
    match = SYSLOG_SEVERITY_RE.search(line)
    if not match:
        return "UNKNOWN"
    return SEVERITY_NAMES.get(match.group("severity"), f"LEVEL-{match.group('severity')}")


def collect_entries(log_dir: pathlib.Path, start: dt.datetime, end: dt.datetime) -> tuple[list[LogEntry], dict[str, int]]:
    entries: list[LogEntry] = []
    scanned = {
        "files": 0,
        "lines": 0,
        "timestamped_lines": 0,
        "matched_lines": 0,
    }

    for path in sorted(log_dir.glob("*.log")):
        scanned["files"] += 1
        switch = path.stem
        for line in path.read_text(errors="replace").splitlines():
            scanned["lines"] += 1
            timestamp = extract_timestamp(line)
            if not timestamp:
                continue
            scanned["timestamped_lines"] += 1
            if start <= timestamp <= end:
                scanned["matched_lines"] += 1
                entries.append(
                    LogEntry(
                        switch=switch,
                        timestamp=timestamp,
                        severity=extract_severity(line),
                        line=line,
                    )
                )

    entries.sort(key=lambda entry: (entry.timestamp, entry.switch, entry.line))
    return entries, scanned


def build_report(change_id: str, log_dir: pathlib.Path, start: dt.datetime, end: dt.datetime, entries: list[LogEntry], scanned: dict[str, int], limit: int) -> str:
    severity_counts: dict[str, int] = {}
    switch_counts: dict[str, int] = {}
    for entry in entries:
        severity_counts[entry.severity] = severity_counts.get(entry.severity, 0) + 1
        switch_counts[entry.switch] = switch_counts.get(entry.switch, 0) + 1

    lines = [
        "Switch Log Change Report",
        "========================",
        f"Change ID:        {change_id}",
        f"Log directory:    {log_dir}",
        f"Window start:     {start}",
        f"Window end:       {end}",
        f"Files scanned:    {scanned['files']}",
        f"Lines scanned:    {scanned['lines']}",
        f"Timestamped logs: {scanned['timestamped_lines']}",
        f"Matched logs:     {scanned['matched_lines']}",
        "",
        "Severity Summary",
        "----------------",
    ]

    if severity_counts:
        for severity in sorted(severity_counts):
            lines.append(f"{severity}: {severity_counts[severity]}")
    else:
        lines.append("None")

    lines.extend(["", "Switch Summary", "--------------"])
    if switch_counts:
        for switch in sorted(switch_counts):
            lines.append(f"{switch}: {switch_counts[switch]}")
    else:
        lines.append("None")

    lines.extend(["", "Matched Logs", "------------"])
    if entries:
        shown = entries[:limit]
        for entry in shown:
            lines.append(f"{entry.timestamp} | {entry.switch} | {entry.severity} | {entry.line}")
        if len(entries) > limit:
            lines.append(f"... {len(entries) - limit} more not shown")
    else:
        lines.append("None")

    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Filter collected NX-OS switch logs by timestamp window")
    parser.add_argument("--change-id", required=True, help="Change ID, e.g. CHG001234")
    parser.add_argument("--start", required=True, help="Start timestamp, e.g. '2026 Jun 4 01:51:29'")
    parser.add_argument("--end", required=True, help="End timestamp, e.g. '2026 Jun 4 02:30:00'")
    parser.add_argument("--run-dir", type=pathlib.Path, default=DEFAULT_RUN_DIR, help="Run directory root")
    parser.add_argument("--input-dir", type=pathlib.Path, help="Override switch log directory")
    parser.add_argument("--output", type=pathlib.Path, help="Override report output path")
    parser.add_argument("--limit", type=int, default=200, help="Max matching log lines to print")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    if end < start:
        raise SystemExit("--end must be greater than or equal to --start")

    run_path = args.run_dir / args.change_id
    log_dir = args.input_dir or run_path / "switch-logs"
    if not log_dir.exists():
        raise SystemExit(f"Switch log directory not found: {log_dir}")

    entries, scanned = collect_entries(log_dir, start, end)
    report = build_report(args.change_id, log_dir, start, end, entries, scanned, args.limit)

    output_path = args.output or run_path / "switch-log-report.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)

    print(report, end="")
    print(f"Report saved: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
