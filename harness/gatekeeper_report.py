#!/usr/bin/env python3
"""Generate GateKeeper run reports as DOCX or Markdown/PDF.

The script intentionally uses only the Python standard library so it can run in
fresh worktrees and on Windows Git Bash without extra package installation.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


def read_text(path: Path, limit: int | None = None) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip() + "\n\n[truncated]"
    return text


def run_id(run_dir: Path) -> str:
    return run_dir.resolve().name


def first_match(text: str, pattern: str, default: str = "") -> str:
    found = re.search(pattern, text, flags=re.MULTILINE)
    return found.group(1).strip() if found else default


def latest_attempt_dirs(run_dir: Path) -> list[Path]:
    def key(path: Path) -> int:
        try:
            return int(path.name.split("-", 1)[1])
        except Exception:
            return 0

    return sorted([p for p in run_dir.glob("attempt-*") if p.is_dir()], key=key)


def collect_run(run_dir: Path) -> dict[str, object]:
    final_decision = read_text(run_dir / "final_decision.md")
    attempts = []
    for attempt_dir in latest_attempt_dirs(run_dir):
        decision = read_text(attempt_dir / "decision.md")
        attempts.append(
            {
                "name": attempt_dir.name,
                "verdict": first_match(decision, r"^## Verdict:\s*(.+)$", "UNKNOWN"),
                "gate": first_match(decision, r"^## Gate:\s*(.+)$", "UNKNOWN"),
                "summary": read_text(attempt_dir / "summary.txt")
                or first_match(decision, r"^## Summary\s*\n(.+)$", ""),
                "patch": attempt_dir / "patch.diff",
                "eval": attempt_dir / "eval.log",
                "critic": attempt_dir / "critic.md",
                "retry_evidence": attempt_dir / "retry_evidence.md",
            }
        )
    return {
        "run_id": run_id(run_dir),
        "run_dir": run_dir.resolve(),
        "brief": read_text(run_dir / "brief.md", 6000),
        "checklist": read_text(run_dir / "critic_checklist.md", 6000),
        "final_decision": final_decision,
        "final_verdict": first_match(final_decision, r"^## Final verdict:\s*(.+)$", "UNKNOWN"),
        "attempts_used": first_match(final_decision, r"^## Attempts used:\s*(.+)$", ""),
        "attempts": attempts,
    }


def tail_text(path: Path, max_lines: int = 80) -> str:
    text = read_text(path)
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(["[truncated: showing last lines]", *lines[-max_lines:]])


def build_markdown(data: dict[str, object]) -> str:
    attempts = data["attempts"]
    assert isinstance(attempts, list)
    lines: list[str] = []
    lines.extend(
        [
            f"# GateKeeper Quality Report - {data['run_id']}",
            "",
            "## Summary",
            "",
            f"- Final verdict: **{data['final_verdict']}**",
            f"- Attempts used: {data['attempts_used'] or 'N/A'}",
            f"- Run directory: `{data['run_dir']}`",
            f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
            "",
            "## Task Brief",
            "",
            "```markdown",
            str(data["brief"]) or "(missing brief.md)",
            "```",
            "",
            "## Critic Checklist",
            "",
            "```markdown",
            str(data["checklist"]) or "(missing critic_checklist.md)",
            "```",
            "",
            "## Attempt Results",
            "",
            "| Attempt | Verdict | Gate | Summary |",
            "|---|---|---|---|",
        ]
    )
    for attempt in attempts:
        assert isinstance(attempt, dict)
        summary = str(attempt.get("summary") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            "| {name} | {verdict} | {gate} | {summary} |".format(
                name=attempt.get("name", ""),
                verdict=attempt.get("verdict", ""),
                gate=attempt.get("gate", ""),
                summary=summary,
            )
        )
    lines.extend(["", "## Executor Evidence", ""])
    for attempt in attempts:
        assert isinstance(attempt, dict)
        eval_path = attempt.get("eval")
        if not isinstance(eval_path, Path):
            continue
        lines.extend(
            [
                f"### {attempt.get('name', '')}",
                "",
                f"- Executor log: `{eval_path}`",
                f"- Patch diff: `{attempt.get('patch', '')}`",
                f"- Critic review: `{attempt.get('critic', '')}`",
                f"- Retry evidence: `{attempt.get('retry_evidence', '')}`",
                "",
                "```text",
                tail_text(eval_path),
                "```",
                "",
            ]
        )
        retry_path = attempt.get("retry_evidence")
        if isinstance(retry_path, Path) and retry_path.exists():
            lines.extend(
                [
                    "#### Retry Evidence",
                    "",
                    "```markdown",
                    read_text(retry_path, 4000),
                    "```",
                    "",
                ]
            )
    lines.extend(
        [
            "## Final Decision",
            "",
            "```markdown",
            str(data["final_decision"]) or "(missing final_decision.md)",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def report_paths(run_dir: Path) -> tuple[Path, Path, Path]:
    report_dir = run_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"gatekeeper_report_{run_id(run_dir)}"
    return report_dir / f"{stem}.md", report_dir / f"{stem}.pdf", report_dir / f"{stem}.docx"


def markdown_to_html(markdown: str) -> str:
    body: list[str] = []
    in_code = False
    code: list[str] = []
    in_table = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                body.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
                code = []
                in_code = False
            else:
                if in_table:
                    body.append("</tbody></table>")
                    in_table = False
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        if line.startswith("# "):
            if in_table:
                body.append("</tbody></table>")
                in_table = False
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_table:
                body.append("</tbody></table>")
                in_table = False
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_table:
                body.append("</tbody></table>")
                in_table = False
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("- "):
            if in_table:
                body.append("</tbody></table>")
                in_table = False
            body.append(f"<p>{html.escape(line)}</p>")
        elif line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", " "} for cell in cells):
                continue
            if not in_table:
                body.append("<table><tbody>")
                in_table = True
                tag = "th"
            else:
                tag = "td"
            body.append("<tr>" + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in cells) + "</tr>")
        elif line.strip():
            if in_table:
                body.append("</tbody></table>")
                in_table = False
            body.append(f"<p>{html.escape(line)}</p>")
        else:
            if in_table:
                body.append("</tbody></table>")
                in_table = False
    if in_table:
        body.append("</tbody></table>")
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif; margin: 28px 36px; color: #1f2937; line-height: 1.45; }}
h1 {{ font-size: 24px; margin-bottom: 16px; }}
h2 {{ font-size: 18px; margin-top: 24px; border-bottom: 1px solid #d1d5db; padding-bottom: 4px; }}
h3 {{ font-size: 15px; margin-top: 18px; }}
p {{ margin: 7px 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0 16px; font-size: 12px; }}
th, td {{ border: 1px solid #cbd5e1; padding: 5px 6px; vertical-align: top; }}
th {{ background: #f1f5f9; text-align: left; }}
pre {{ background: #f8fafc; border: 1px solid #cbd5e1; padding: 10px; white-space: pre-wrap; font-size: 11px; }}
@page {{ margin: 14mm 12mm; }}
</style>
</head>
<body>
{chr(10).join(body)}
</body>
</html>
"""


def find_browser() -> Path | None:
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def write_pdf(markdown: str, pdf_path: Path) -> None:
    browser = find_browser()
    if browser is None:
        raise RuntimeError("Chrome/Edge not found; cannot generate PDF")
    with tempfile.TemporaryDirectory(prefix="gatekeeper_report_") as tmp:
        html_path = Path(tmp) / "report.html"
        html_path.write_text(markdown_to_html(markdown), encoding="utf-8")
        completed = subprocess.run(
            [
                str(browser),
                "--headless",
                "--disable-gpu",
                f"--print-to-pdf={pdf_path.resolve()}",
                html_path.resolve().as_uri(),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
    if not pdf_path.exists() or not pdf_path.read_bytes().startswith(b"%PDF-"):
        output = completed.stdout.decode("utf-8", errors="replace")
        raise RuntimeError(f"PDF was not generated: {pdf_path}\n{output}")


def paragraph(text: str, style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{style_xml}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def write_docx(markdown: str, docx_path: Path) -> None:
    body: list[str] = []
    in_code = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if line.startswith("# "):
            body.append(paragraph(line[2:], "Title"))
        elif line.startswith("## "):
            body.append(paragraph(line[3:], "Heading1"))
        elif line.startswith("### "):
            body.append(paragraph(line[4:], "Heading2"))
        elif line.startswith("|"):
            body.append(paragraph(line))
        elif in_code:
            body.append(paragraph(line, "Code"))
        elif line.strip():
            body.append(paragraph(line))
        else:
            body.append(paragraph(""))

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
{chr(10).join(body)}
<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1200" w:bottom="1440" w:left="1200"/></w:sectPr>
</w:body></w:document>'''
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr><w:rPr><w:rFonts w:ascii="Segoe UI" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:spacing w:after="220"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Segoe UI" w:eastAsia="Microsoft YaHei"/><w:sz w:val="34"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:spacing w:before="220" w:after="120"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Segoe UI" w:eastAsia="Microsoft YaHei"/><w:sz w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:spacing w:before="180" w:after="100"/></w:pPr><w:rPr><w:b/><w:rFonts w:ascii="Segoe UI" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:rPr><w:rFonts w:ascii="Consolas" w:eastAsia="Consolas"/><w:sz w:val="18"/></w:rPr></w:style>
</w:styles>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(docx_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a GateKeeper run report.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--format", choices=["docx", "md-pdf", "all"], default="all")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    if not run_dir.exists():
        raise SystemExit(f"run_dir not found: {run_dir}")
    data = collect_run(run_dir)
    markdown = build_markdown(data)
    md_path, pdf_path, docx_path = report_paths(run_dir)

    if args.format in ("md-pdf", "all"):
        md_path.write_text(markdown, encoding="utf-8")
        print(f"markdown={md_path}")
        write_pdf(markdown, pdf_path)
        print(f"pdf={pdf_path}")
    if args.format in ("docx", "all"):
        write_docx(markdown, docx_path)
        print(f"docx={docx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
