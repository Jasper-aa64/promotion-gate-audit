#!/usr/bin/env python3
"""Patch agent for the headless auto-loop.

This script is the external --patch-command that the auto-loop invokes to
generate real source code modifications in a candidate workspace. It bridges
the harness (which handles build/compare/timing/verdict) with an LLM agent
(which generates the actual optimization code).

Interface contract (called by headless_auto_loop.py):
  - Receives candidate context via neutral harness environment variables
  - Modifies files in CANDIDATE_WORKSPACE
  - Exits 0 if changes were made, non-zero otherwise

Environment variables consumed:
  CANDIDATE_ID           - unique candidate identifier
  CANDIDATE_LANE         - evidence / insight / combination
  CANDIDATE_TARGET       - stage or function to optimize
  CANDIDATE_TOUCHED_FILES - pipe-separated predicted files
  CANDIDATE_METADATA_JSON - full candidate dict as JSON
  CANDIDATE_WORKSPACE    - path to the isolated workspace
  SOURCE_ROOT            - original source tree (read-only reference)
  RUN_DIR                - run root for logs/artifacts
  ITERATION              - current iteration number
  CANDIDATE_LEDGER       - optional task-level ledger with blocked classes

Configuration:
  PATCH_AGENT_MODE       - "api" (default), "cli", "codex", or "template"
  ANTHROPIC_API_KEY          - required for "api" mode
  PATCH_AGENT_MODEL      - model to use (default: claude-sonnet-4-6)
  PATCH_AGENT_MAX_TOKENS - max tokens for response (default: 4096)
  CODEX_PATCH_TIMEOUT    - max seconds for codex exec mode (default: 900)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_candidate_context() -> dict[str, Any]:
    metadata_json = os.environ.get("CANDIDATE_METADATA_JSON", "{}")
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        metadata = {}

    return {
        "candidate_id": os.environ.get("CANDIDATE_ID", ""),
        "lane": os.environ.get("CANDIDATE_LANE", ""),
        "target": os.environ.get("CANDIDATE_TARGET", ""),
        "touched_files": [
            f for f in os.environ.get("CANDIDATE_TOUCHED_FILES", "").split("|") if f
        ],
        "workspace": os.environ.get("CANDIDATE_WORKSPACE", ""),
        "source_root": os.environ.get("SOURCE_ROOT", ""),
        "run_dir": os.environ.get("RUN_DIR", ""),
        "iteration": os.environ.get("ITERATION", "0"),
        "candidate_ledger": os.environ.get("CANDIDATE_LEDGER", ""),
        "hypothesis": metadata.get("hypothesis", ""),
        "expected_effect": metadata.get("expected_effect", ""),
        "semantic_risk": metadata.get("semantic_risk", ""),
        "stack_members": metadata.get("stack_members", []),
        "source_evidence": metadata.get("source_evidence", {}),
        # Diagnostic capability context (additive; empty => old runs stay byte-compatible)
        "perf_capability_file": os.environ.get("PERF_CAPABILITY_FILE", ""),
    }


def load_candidate_ledger(path: str) -> dict[str, Any]:
    if not path:
        return {}
    ledger_path = Path(path)
    if not ledger_path.exists():
        return {}
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def ledger_prompt_constraints(ledger: dict[str, Any]) -> str:
    if not ledger:
        return ""

    positive_retry = [
        str(row.get("candidate_id") or "").strip()
        for row in ledger.get("quiet_window_retry_queue", []) or []
        if str(row.get("candidate_id") or "").strip()
    ]
    blocked = [
        str(row.get("candidate_id") or "").strip()
        for section in ("non_retry_candidates", "not_run_candidates")
        for row in ledger.get(section, []) or []
        if str(row.get("candidate_id") or "").strip()
    ]
    blocked_classes = [
        str(row.get("class") or "").strip()
        for row in ledger.get("blocked_candidate_classes", []) or []
        if str(row.get("class") or "").strip()
    ]

    lines = ["## Candidate Ledger Constraints"]
    if positive_retry:
        lines.append(
            "- These candidates are positive but NOISY_PENDING and may only be repeated by the harness quiet-window retry path: "
            + ", ".join(positive_retry)
            + ". Do not recreate them as a new immediate candidate."
        )
    if blocked:
        lines.append(
            "- These candidates are blocked or non-retry and must not be regenerated: "
            + ", ".join(blocked)
            + "."
        )
    if blocked_classes:
        lines.append(
            "- These candidate classes are blocked until redesigned: "
            + ", ".join(blocked_classes)
            + "."
        )
    if "projection_prune_with_manual_column_remap" in blocked_classes:
        lines.append(
            "- Specifically, do not prune a projection column list and manually remap table->column indices. "
            "Leave the projection contract unchanged unless the harness explicitly asks for a redesigned projection-index abstraction."
        )
    return "\n".join(lines)


def find_relevant_sources(workspace: Path, target: str, touched_files: list[str]) -> dict[str, str]:
    """Find and read source files relevant to the optimization target."""
    sources: dict[str, str] = {}

    cpp_extensions = {".cpp", ".h", ".hpp", ".cc", ".cxx"}
    candidates: list[Path] = []

    for touched in touched_files:
        path = workspace / touched
        if path.exists() and path.suffix in cpp_extensions:
            candidates.append(path)

    if not candidates:
        target_clean = target.replace(".", "/").replace("::", "/")
        for ext in cpp_extensions:
            for match in workspace.rglob(f"*{ext}"):
                if any(part in str(match) for part in target_clean.split("/")):
                    candidates.append(match)

    if not candidates:
        for ext in cpp_extensions:
            for match in workspace.rglob(f"*{ext}"):
                candidates.append(match)
                if len(candidates) > 20:
                    break
            if len(candidates) > 20:
                break

    for path in candidates[:10]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            rel = str(path.relative_to(workspace)).replace("\\", "/")
            sources[rel] = content
        except (OSError, ValueError):
            continue

    return sources


WORK_SHIFTING_CONSTRAINTS = """## Work-Shifting Constraints

The gate times a paired window and its estimand is the MEDIAN paired difference.

- Moving work OUT of the timed window (to startup, warmup, compile time,
  configuration load, or a first-access path) may improve in-window metrics,
  but does NOT mean total work decreased. If it applies, you must state what
  the total work becomes.
- A rare expensive path (first access, lazy build, cache rebuild, invalidation)
  barely moves the median, but it raises the paired stdev — which feeds
  noise_flag, MDE, power, and the bootstrap CI width. It is more likely to make
  the candidate look NOISY than to make it look bad. If it applies, you must
  state its frequency and maximum cost.
- If first access affects request success rate, tail latency, or resource
  peaks, it must still be checked as an independent regression item — never
  judged by the median alone."""


WORK_SHIFTING_ANSWER_CONTRACT = """## Required Structured Answer

Before the patch blocks, output one fenced JSON block describing the change
against the measurement window:

```work_shifting_info
{
  "timing_window_relation": "inside_window" | "outside_window" | "unknown",
  "tail_cost": {"frequency": "<how often>", "max_cost": "<worst case>", "path": "<code path>"} | "none" | "unknown",
  "total_work_change": "<required when timing_window_relation is outside_window; state what the total work becomes>"
}
```

Constraints:
- timing_window_relation must be one of inside_window / outside_window / unknown.
- If timing_window_relation is outside_window, total_work_change is REQUIRED
  (non-empty).
- If tail_cost is a structured object (not "none" or "unknown"), frequency and
  max_cost are REQUIRED.
- If you cannot answer a required field, use "unknown" — missing fields mark
  the candidate needs_review and are never silently passed."""


def parse_work_shifting_info(response: str) -> dict[str, Any]:
    """Extract the work_shifting_info JSON block from an LLM response.

    Returns the parsed dict, or {} when no block is present.
    """
    pattern = r"```work_shifting_info\s*\n(.*?)```"
    match = re.search(pattern, response, re.DOTALL)
    if not match:
        return {}
    try:
        info = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return {}
    return info if isinstance(info, dict) else {}


def write_work_shifting_fields(run_dir: str, candidate_id: str, info: dict[str, Any]) -> Path | None:
    """Persist the parsed fields for the auto-loop to merge and validate.

    Always writes the file (even {"missing": true}) so the materialize-time
    check can distinguish new-pipeline candidates from legacy ones.
    """
    if not run_dir:
        return None
    path = Path(run_dir) / "work_shifting_fields.json"
    payload = {"candidate_id": candidate_id,
               "missing": not info,
               **info}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def build_optimization_prompt(ctx: dict[str, Any], sources: dict[str, str]) -> str:
    """Build the prompt that asks the LLM to generate optimized code."""

    source_blocks = []
    for path, content in sources.items():
        lines = content.split("\n")
        if len(lines) > 500:
            content = "\n".join(lines[:500]) + f"\n// ... ({len(lines) - 500} more lines)"
        source_blocks.append(f"### {path}\n```cpp\n{content}\n```")

    sources_text = "\n\n".join(source_blocks)
    ledger_constraints = ledger_prompt_constraints(load_candidate_ledger(ctx.get("candidate_ledger", "")))
    try:
        from perf_capability import context_section
        capability_section = context_section(ctx.get("perf_capability_file", ""))
    except ImportError:  # legacy deployment lacks the module: stay byte-compatible, add no section
        capability_section = ""
    stack_members = ctx.get("stack_members") or []
    stack_context = ""
    if ctx.get("lane") == "combination" or stack_members:
        stack_context = f"""
## Combination Candidate
- Stack members: {"|".join(str(member) for member in stack_members) or "(metadata omitted)"}
- Treat the stack as one optimization, not as separate patches.
- If member ideas overlap in the same file or line range, resolve the overlap in the workspace and output the final unified file contents.
- The harness will validate only the final workspace diff, then build, compare, and time it.
"""

    return f"""You are a C++ performance optimization expert working on a high-frequency trading high-frequency trading system.

## Task
Generate an optimized version of the target code. The optimization must be:
1. Semantically equivalent (same output for same input)
2. Focused on reducing wall-clock time
3. Minimal in scope (touch as few lines as possible)

## Candidate Context
- Target: {ctx['target']}
- Hypothesis: {ctx['hypothesis']}
- Expected effect: {ctx['expected_effect']}
- Semantic risk: {ctx['semantic_risk']}
- Lane: {ctx['lane']}
{stack_context}

## Source Evidence
{json.dumps(ctx.get('source_evidence', {}), indent=2)}

{capability_section}
{WORK_SHIFTING_CONSTRAINTS}
{WORK_SHIFTING_ANSWER_CONTRACT}

{ledger_constraints}

## Source Files
{sources_text}

## Output Format
For each file you modify, output a block in this exact format:

```patch_file:<relative/path/to/file>
<complete new file content>
```

Rules:
- Output the COMPLETE file content, not a diff
- Only output files you actually changed
- Do NOT add comments explaining the optimization
- Do NOT change function signatures or public interfaces
- Do NOT change output behavior
- Focus on: branch prediction, cache locality, avoiding redundant computation, loop optimization, avoiding allocations in hot paths
- If you cannot find a valid optimization, output nothing

Generate the optimized code now."""


def build_direct_edit_prompt(ctx: dict[str, Any], sources: dict[str, str]) -> str:
    prompt = build_optimization_prompt(ctx, sources)
    return (
        prompt
        + "\n\n## Direct Edit Mode\n"
        + "Edit the candidate workspace files directly. Do not commit. Do not print patch_file blocks. "
        + "Do not run builds, configure CMake, create build directories, or write generated artifacts. "
        + "Keep the patch minimal and leave the workspace unchanged if no safe optimization exists.\n"
    )


def parse_llm_response(response: str) -> dict[str, str]:
    """Parse the LLM response into file path -> content pairs."""
    patches: dict[str, str] = {}

    pattern = r"```patch_file:([^\n]+)\n(.*?)```"
    for match in re.finditer(pattern, response, re.DOTALL):
        path = match.group(1).strip()
        content = match.group(2)
        if path and content.strip():
            patches[path] = content

    return patches


def call_anthropic_api(prompt: str) -> str | None:
    """Call the Anthropic API directly using the SDK."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed. pip install anthropic", file=sys.stderr)
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return None

    model = os.environ.get("PATCH_AGENT_MODEL", "claude-sonnet-4-6")
    max_tokens = int(os.environ.get("PATCH_AGENT_MAX_TOKENS", "4096"))

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text if message.content else None
    except Exception as exc:
        print(f"ERROR: Anthropic API call failed: {exc}", file=sys.stderr)
        return None


def call_claude_cli(prompt: str, workspace: Path) -> str | None:
    """Call claude CLI as a fallback."""
    prompt_file = workspace / ".patch_prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    claude_bin = shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
    if not claude_bin:
        print("ERROR: claude CLI not found on PATH", file=sys.stderr)
        prompt_file.unlink(missing_ok=True)
        return None

    try:
        result = subprocess.run(
            [claude_bin, "--print"],
            cwd=str(workspace),
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        print(
            "ERROR: claude CLI returned no usable response "
            f"rc={result.returncode} stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}",
            file=sys.stderr,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"ERROR: claude CLI failed: {exc}", file=sys.stderr)
    finally:
        prompt_file.unlink(missing_ok=True)

    return None


def workspace_has_changes(workspace: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def call_codex_cli(prompt: str, workspace: Path, run_dir: Path, candidate_id: str) -> bool:
    """Call Codex CLI to edit the candidate workspace directly."""
    codex_bin = shutil.which("codex.cmd") or shutil.which("codex.exe") or shutil.which("codex")
    if not codex_bin:
        print("ERROR: codex CLI not found on PATH", file=sys.stderr)
        return False

    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = log_dir / f"codex_patch_prompt_{candidate_id}.md"
    output_path = log_dir / f"codex_patch_output_{candidate_id}.txt"
    exec_log_path = log_dir / f"codex_patch_exec_{candidate_id}.log"
    prompt_path.write_text(prompt, encoding="utf-8")

    command = [
        codex_bin,
        "exec",
        "--cd",
        str(workspace),
        "--sandbox",
        "workspace-write",
        "--output-last-message",
        str(output_path),
        "-",
    ]
    timeout_seconds = int(os.environ.get("CODEX_PATCH_TIMEOUT", "900"))
    try:
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"ERROR: codex CLI failed: {exc}", file=sys.stderr)
        return False
    exec_log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        print(f"ERROR: codex CLI failed rc={completed.returncode}; see {exec_log_path}", file=sys.stderr)
        return False
    if not workspace_has_changes(workspace):
        print("ERROR: codex CLI completed but left no workspace changes", file=sys.stderr)
        return False
    print(f"patch_agent: codex direct edit completed; prompt={prompt_path}; output={output_path}")
    return True


def apply_patches(workspace: Path, patches: dict[str, str]) -> list[str]:
    """Write patched files to the workspace. Returns list of modified paths."""
    modified: list[str] = []
    for rel_path, content in patches.items():
        target = workspace / rel_path
        if not target.exists():
            continue
        original = target.read_text(encoding="utf-8", errors="replace")
        if content.strip() == original.strip():
            continue
        target.write_text(content, encoding="utf-8")
        modified.append(rel_path)
    return modified


def main() -> int:
    ctx = load_candidate_context()
    workspace = Path(ctx["workspace"])

    if not workspace.exists():
        print(f"ERROR: workspace does not exist: {workspace}", file=sys.stderr)
        return 1

    print(f"patch_agent: candidate={ctx['candidate_id']} target={ctx['target']} lane={ctx['lane']}")

    sources = find_relevant_sources(workspace, ctx["target"], ctx["touched_files"])
    if not sources:
        print("ERROR: no relevant source files found in workspace", file=sys.stderr)
        return 1

    print(f"patch_agent: found {len(sources)} source files")

    mode = os.environ.get("PATCH_AGENT_MODE", "api")
    response: str | None = None

    if mode == "codex":
        prompt = build_direct_edit_prompt(ctx, sources)
        run_dir = Path(ctx["run_dir"]) if ctx["run_dir"] else workspace
        return 0 if call_codex_cli(prompt, workspace, run_dir, ctx["candidate_id"]) else 1

    prompt = build_optimization_prompt(ctx, sources)
    if mode == "api":
        response = call_anthropic_api(prompt)
        if response is None and mode == "api":
            print("patch_agent: API mode failed, trying CLI fallback", file=sys.stderr)
            response = call_claude_cli(prompt, workspace)
    elif mode == "cli":
        response = call_claude_cli(prompt, workspace)
    elif mode == "template":
        print("ERROR: template mode not yet implemented", file=sys.stderr)
        return 1
    else:
        print(f"ERROR: unknown PATCH_AGENT_MODE={mode}", file=sys.stderr)
        return 1

    if not response:
        print("ERROR: no response from LLM", file=sys.stderr)
        return 1

    info = parse_work_shifting_info(response)
    if ctx["run_dir"]:
        write_work_shifting_fields(ctx["run_dir"], ctx["candidate_id"], info)

    patches = parse_llm_response(response)
    if not patches:
        print("ERROR: LLM response contained no valid patches", file=sys.stderr)
        log_dir = Path(ctx["run_dir"]) / "logs" if ctx["run_dir"] else workspace
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"patch_agent_raw_{ctx['candidate_id']}.txt").write_text(
            response, encoding="utf-8"
        )
        return 1

    modified = apply_patches(workspace, patches)
    if not modified:
        print("ERROR: patches parsed but no files were actually modified", file=sys.stderr)
        return 1

    print(f"patch_agent: modified {len(modified)} files: {', '.join(modified)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
