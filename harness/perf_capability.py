#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capability-aware perf diagnostic context (task 09-08-intel-performance-skills-review).

Whole-machine PMU gate: the probe checks `cycles`. If `cycles` is
`<not supported>` (or absent / permission-denied), the hardware PMU is
UNAVAILABLE on this host and every hardware-derived event is classified
`unavailable` no matter what number it prints. A dead counter that returns 0
with exit code 0 must never become `supported`.

Statuses:
  supported          event reported AND (software event, or hardware PMU available)
  unsupported        event line carries <not supported> / <not counted>
  permission_denied  event line carries a permission marker
  not_attempted      event never attempted / no readable output
  unavailable        hardware-derived event on a host whose PMU gate failed
                     (printed value is ignored)

Software events (work without a PMU, not gated):
  cpu-clock, task-clock, context-switches, cpu-migrations, page-faults,
  minor-faults, major-faults, alignment-faults, emulation-faults, dummy.

Design constraints (design.md): additive only; perf stays conditional and
diagnostic-only; promotion verdicts, timing, correctness and ledgers are
untouched; pattern matches are hypotheses, not outcomes.

Commands:
  probe       run the /bin/true cycles probe; write perf_capability.json and
              perf_capability.txt into the run dir
  classify    classify one event line against a cycles status
  context     render the agent-context section for a record file
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROBE_COMMAND = "perf stat -e cycles -- /bin/true"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # host capability only changes with machine/kernel/virtualization-config changes

SOFTWARE_EVENTS = {
    "cpu-clock", "task-clock", "context-switches", "cpu-migrations",
    "page-faults", "minor-faults", "major-faults", "alignment-faults",
    "emulation-faults", "dummy",
}

UNSUPPORTED_MARKERS = ("<not supported>", "<not counted>", "<not available>")
PERMISSION_MARKERS = ("<not permitted>", "permission denied", "access denied")
# Unit tokens in perf event lines: skip them to reach the event name
# (real line: '1.17 msec task-clock    # 0.604 CPUs utilized')
UNIT_TOKENS = {"msec", "msecs", "sec", "secs", "usec", "usecs", "nsec", "nsecs", "GHz"}


def parse_event_line(line: str):
    """Parse one perf event line, e.g. '1.17 msec task-clock  # 0.604 CPUs utilized'.

    Returns {"status": ..., "value": float|None, "event": str} or None for
    non-event lines. `status` here is the raw line status: reported /
    unsupported / permission_denied. Unit tokens (msec/secs/...) between the
    value and the event name are skipped; a '#' comment suffix is stripped.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if ("time elapsed" in line or "Performance counter stats" in line
            or "seconds user" in line or "seconds sys" in line):
        return None
    body = line.split("#", 1)[0].strip()
    parts = body.split()
    if len(parts) < 2:
        return None
    if any(m in body for m in PERMISSION_MARKERS):
        return {"status": "permission_denied", "value": None,
                "event": parts[-1]}
    if any(m in body for m in UNSUPPORTED_MARKERS):
        return {"status": "unsupported", "value": None,
                "event": parts[-1]}
    try:
        value = float(parts[0].replace(",", ""))
    except ValueError:
        return None
    if parts[1] in UNIT_TOKENS:
        if len(parts) < 3:
            # value + unit with no event name (truncated/interrupted
            # perf output). Contract: return None, never raise.
            return None
        event = parts[2]
    else:
        event = parts[1]
    return {"status": "reported", "value": value, "event": event}


def cycles_status_from_file(path):
    """Extract the cycles event line status from a perf stat output file."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "not_attempted"
    for ln in text.splitlines():
        rec = parse_event_line(ln)
        if rec is not None and rec["event"] == "cycles":
            return rec["status"]
    return "not_attempted"


def probe_cycles(perf_cmd="perf", timeout=30):
    """Run a microsecond-scale cycles probe (perf stat -e cycles -- /bin/true).

    Returns (status, raw_line, value). Never raises.
    """
    try:
        out = subprocess.run(
            [perf_cmd, "stat", "-e", "cycles", "--", "/bin/true"],
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return "not_attempted", "", None
    for ln in out.stderr.splitlines():
        rec = parse_event_line(ln)
        if rec is not None and rec["event"] == "cycles":
            return rec["status"], ln.strip(), rec["value"]
    return "not_attempted", "", None


def machine_pmu_available(cycles_status: str, cycles_value=None) -> bool:
    """Whole-machine PMU gate with a positive control.

    A live PMU must report a nonzero `cycles` count: running /bin/true burns
    cycles on any real machine, so `reported` alone is not enough — a dead
    counter that prints 0 with exit code 0 would otherwise slip through the
    gate exactly the way it slips past a naive consumer. unsupported /
    not_attempted / permission_denied all mean unavailable.
    """
    if cycles_status != "reported":
        return False
    return cycles_value is not None and cycles_value > 0


def classify_event(event: str, raw_status: str, cycles_status: str,
                   cycles_value=None) -> str:
    """Final event status with the whole-machine PMU gate applied."""
    if raw_status != "reported":
        return raw_status
    if event in SOFTWARE_EVENTS:
        return "supported"
    if not machine_pmu_available(cycles_status, cycles_value):
        return "unavailable"  # printed value ignored (incl. exit-0 dead zeros)
    return "supported"


def normalize_event_name(raw: str) -> str:
    """Normalize a perf event name read from artifacts ('cycles:ppp' -> 'cycles')."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    return raw.split(":", 1)[0].split(",", 1)[0].strip()


# Workload label recorded in the capability artifact. Neutral by default;
# operators may override via the PERF_WORKLOAD environment variable.
WORKLOAD_LABEL = os.environ.get("PERF_WORKLOAD", "generic-cpp-benchmark").strip() or "generic-cpp-benchmark"


def build_record(run_dir, cycles_status, raw_line, cycles_value=None,
                 host="", probed_at="", sampled_event=None,
                 sampled_event_source=""):
    """Structured diagnostic record (design.md fields). Machine-scope probe
    record only; candidate-scoped fields live on the candidate, not here."""
    pmu_available = machine_pmu_available(cycles_status, cycles_value)
    se = normalize_event_name(sampled_event or "")
    if se in SOFTWARE_EVENTS:
        hazards = ("perf_report.txt sampled a SOFTWARE %s event: symbol/time "
                   "attribution only; do not infer cache-miss, IPC, "
                   "branch-misprediction, or false-sharing from it." % se)
    elif se:
        hazards = ("perf_report.txt sampled the hardware event '%s'; the cycles "
                   "positive control is a basic liveness check only and does not "
                   "certify individual hardware events — per-event verification "
                   "and calibration are required before cache/IPC/branch "
                   "inference." % se)
    else:
        hazards = ("perf_report.txt sampling event unverified; do not infer "
                   "cache-miss, IPC, branch-misprediction, or false-sharing.")
    return {
        "workload": WORKLOAD_LABEL,
        "host": host,
        "probed_at": probed_at,
        "probe_command": PROBE_COMMAND,
        "command": PROBE_COMMAND,
        "host_capability_snapshot": {
            "cycles_status": cycles_status,
            "cycles_value": cycles_value,
            "cycles_raw_line": raw_line,
            "hardware_pmu": "available" if pmu_available else "unavailable",
            "host_note": "",
        },
        "sampled_event": se or None,
        "sampled_event_source": sampled_event_source,
        "stage": "diagnostic_probe",
        "event_source": "perf",
        "metric": "cycles",
        "confidence": "observation",
        "matching_pattern": None,
        "predicted_mechanism": None,
        "disconfirming_signal": None,
        "semantic_hazards": hazards,
        "diagnostic_artifact": "perf_capability.txt",
    }


def _probe_age_seconds(probed_at: str) -> float | None:
    """Age of an ISO timestamp in seconds; None when unparseable (=> re-probe)."""
    if not probed_at:
        return None
    try:
        stamp = time.mktime(time.strptime(probed_at[:19], "%Y-%m-%dT%H:%M:%S"))
        return max(0.0, time.time() - stamp)
    except ValueError:
        return None


def update_host_cache(cache_path, record):
    """Host-scoped cache: {host: record}. A newer probed_at overwrites the
    entry; the caller is responsible for probing on cache miss."""
    cache_path = Path(cache_path)
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    host = record.get("host") or "unknown"
    prev = cache.get(host)
    if isinstance(prev, dict) and (prev.get("probed_at") or "") >= (record.get("probed_at") or ""):
        return cache
    cache[host] = record
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    return cache


def load_host_cache(cache_path, host):
    """Read the host-scoped record. Absent => EXPLICIT not_probed status —
    never a silent 'everything is fine'."""
    try:
        cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    rec = cache.get(host)
    if isinstance(rec, dict):
        return rec
    return {"status": "not_probed", "host": host}


def render_txt(record) -> str:
    """Human-readable capability text (what the agent's log bundle carries).
    Warnings are generated from the actual probe result, not constant text."""
    hw = record["host_capability_snapshot"]
    if hw["hardware_pmu"] == "unavailable":
        if hw["cycles_status"] == "reported":
            first = ("hardware PMU: UNAVAILABLE on this host (cycles reported as %s; "
                     "dead counter: positive control failed)" % hw["cycles_value"])
        elif hw["cycles_status"] == "not_attempted":
            first = "hardware PMU: probe not run; capability UNVERIFIED, treated as unavailable"
        else:
            first = "hardware PMU: UNAVAILABLE on this host (cycles=%s)" % hw["cycles_status"]
    else:
        first = ("hardware PMU: available (cycles reported as %s; positive control passed — "
                 "basic liveness check only, does not certify individual hardware events)"
                 % hw["cycles_value"])
    lines = [first]
    se = normalize_event_name(record.get("sampled_event") or "")
    if se in SOFTWARE_EVENTS:
        lines.append("perf_report.txt sampled a SOFTWARE %s event: symbol/time attribution only." % se)
        lines.append("DO NOT infer cache-miss, IPC, branch-misprediction, or false-sharing from these artifacts.")
    elif se:
        lines.append("perf_report.txt sampled the hardware event '%s'." % se)
        lines.append("DO NOT infer cache-miss, IPC, branch-misprediction, or false-sharing without per-event verification and calibration.")
    else:
        lines.append("perf_report.txt sampling event unverified.")
        lines.append("DO NOT infer cache-miss, IPC, branch-misprediction, or false-sharing from these artifacts.")
    lines.append("perf c2c: unverified (never probed).")
    return "\n".join(lines) + "\n"


def context_section(record_path):
    """Agent-context section for a capability record.

    Returns '' when no record path is configured (old runs stay byte-compatible),
    an explicit not_probed section when the host cache has no record yet, and
    an explicit diagnostic_unavailable section when the record is malformed
    (design.md rollback). Never silently implies 'everything is fine'.
    """
    if not record_path:
        return ""
    try:
        record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("## Diagnostic Capability\n"
                "- diagnostic_unavailable (capability record missing or malformed)\n")
    if not isinstance(record, dict):
        return ("## Diagnostic Capability\n"
                "- diagnostic_unavailable (capability record missing or malformed)\n")
    if record.get("status") == "not_probed":
        return ("## Diagnostic Capability\n"
                "- status: not_probed (no capability record for this host yet; "
                "treat all hardware claims as unverified)\n")
    try:
        hw = record["host_capability_snapshot"]
        lines = [
            "## Diagnostic Capability",
            "- hardware PMU: %s (cycles probe: %s; positive control is a basic liveness check only)"
            % (hw["hardware_pmu"], hw["cycles_status"]),
            "- " + str(record.get("semantic_hazards") or ""),
        ]
        return "\n".join(lines) + "\n"
    except (KeyError, TypeError):
        return ("## Diagnostic Capability\n"
                "- diagnostic_unavailable (capability record missing or malformed)\n")


def cmd_probe(args):
    host = args.host or socket.gethostname()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cached = None
    fresh = False
    if args.host_cache:
        cached = load_host_cache(args.host_cache, host)
        fresh = (isinstance(cached, dict)
                 and cached.get("probe_command") == PROBE_COMMAND
                 and cached.get("probed_at")
                 and _probe_age_seconds(cached.get("probed_at", "")) is not None
                 and _probe_age_seconds(cached.get("probed_at", "")) < CACHE_TTL_SECONDS)
    if fresh:
        hw = cached["host_capability_snapshot"]
        cycles_status = hw["cycles_status"]
        cycles_value = hw["cycles_value"]
        raw_line = hw["cycles_raw_line"]
    else:
        cycles_status, raw_line, cycles_value = probe_cycles(perf_cmd=args.perf_cmd)
        if cycles_status == "not_attempted" and args.cycles_file:
            cycles_status = cycles_status_from_file(args.cycles_file)
            if cycles_status == "reported":
                for ln in Path(args.cycles_file).read_text(
                        encoding="utf-8", errors="replace").splitlines():
                    rec = parse_event_line(ln)
                    if rec is not None and rec["event"] == "cycles":
                        cycles_value = rec["value"]
                        break
    sampled_event = None
    sampled_event_source = ""
    if args.sampled_event_file:
        try:
            sampled_event = Path(args.sampled_event_file).read_text(
                encoding="utf-8", errors="replace").strip() or None
            sampled_event_source = "perf evlist -i perf.data"
        except OSError:
            sampled_event = None
    record = build_record(args.run_dir or ".", cycles_status, raw_line,
                          cycles_value, host=host, probed_at=now,
                          sampled_event=sampled_event,
                          sampled_event_source=sampled_event_source)
    # This round's record is ALWAYS written, overwriting any previous one: a
    # failed probe must leave an explicit unverified state for this round
    # instead of reusing a stale success record from an earlier round (fail closed).
    if args.host_cache and not fresh:
        update_host_cache(args.host_cache, record)
    if args.out:
        Path(args.out).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if args.txt:
        Path(args.txt).write_text(render_txt(record), encoding="utf-8")
    print("host=%s cache=%s cycles_status=%s cycles_value=%s hardware_pmu=%s" % (
        host, "hit" if fresh else "miss", cycles_status, cycles_value,
        "available" if machine_pmu_available(cycles_status, cycles_value) else "unavailable"))
    return 0


def cmd_classify(args):
    cycles_value = None
    if args.cycles_value is not None:
        try:
            cycles_value = float(args.cycles_value)
        except ValueError:
            cycles_value = None
    status = classify_event(args.event, args.raw_status, args.cycles_status,
                            cycles_value)
    print(status)
    return 0


def cmd_context(args):
    print(context_section(args.record), end="")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="run the /bin/true cycles probe")
    p_probe.add_argument("--out", help="write perf_capability.json here (always overwritten)")
    p_probe.add_argument("--txt", help="write perf_capability.txt here (always overwritten)")
    p_probe.add_argument("--run-dir", help="recorded run dir (metadata only)")
    p_probe.add_argument("--cycles-file", help="fallback: parse cycles from this perf stat file")
    p_probe.add_argument("--host-cache", help="host-scoped cache file ({host: record})")
    p_probe.add_argument("--host", default="", help="host identity (default: local hostname)")
    p_probe.add_argument("--sampled-event-file",
                         help="file containing the sampled event read from perf.data")
    p_probe.add_argument("--perf-cmd", default="perf",
                         help="perf binary (testability)")
    p_probe.set_defaults(func=cmd_probe)

    p_cls = sub.add_parser("classify", help="classify one event")
    p_cls.add_argument("--event", required=True)
    p_cls.add_argument("--raw-status", required=True,
                       choices=["reported", "unsupported", "permission_denied", "not_attempted"])
    p_cls.add_argument("--cycles-status", required=True,
                       choices=["reported", "unsupported", "permission_denied", "not_attempted"])
    p_cls.add_argument("--cycles-value", default=None,
                       help="cycles count from the probe (positive control)")
    p_cls.set_defaults(func=cmd_classify)

    p_ctx = sub.add_parser("context", help="render the agent-context section")
    p_ctx.add_argument("--record", default="", help="perf_capability.json path ('' => no section)")
    p_ctx.set_defaults(func=cmd_context)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
