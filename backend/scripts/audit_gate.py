"""The dependency gate (D40): block a merge only when a vulnerable package has a fix.

`uv audit` lists known vulnerabilities in uv.lock (from OSV), but it has no flag for
"only findings with a fix": it exits 1 on any finding. This script reads its JSON,
prints every finding, and fails only when at least one finding has a fixed version.
Findings with no fix yet, and adverse project statuses (archived, ...), are printed but
do not block; the nightly scan (nightly-scans.yml) fails on them instead.

    uv audit --locked --output-format json > audit.json; status=$?
    python3 scripts/audit_gate.py audit.json --uv-exit "$status"

Exit codes: 0 pass, 1 a finding has a fix (blocks), 2 the JSON is not what this script
knows. `uv audit` and its JSON are experimental, so any change in shape fails loudly
here instead of passing silently. Standard library only: it runs before `uv sync`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PASS, BLOCK, BAD_INPUT = 0, 1, 2
KNOWN_SCHEMA_VERSIONS = {"preview"}  # what uv 0.13.7 writes


class BadAuditJson(ValueError):
    """The input is not the `uv audit --output-format json` shape this script was built for."""


def _get(obj: Any, key: str, kind: type, where: str) -> Any:
    if not isinstance(obj, dict) or key not in obj:
        raise BadAuditJson(f"{where}: missing key {key!r}")
    value = obj[key]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise BadAuditJson(f"{where}.{key}: expected {kind.__name__}, got {type(value).__name__}")
    return value


def parse(report: Any) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    """Check the shape and return (packages audited, vulnerabilities, adverse statuses)."""
    version = _get(_get(report, "schema", dict, "report"), "version", str, "schema")
    if version not in KNOWN_SCHEMA_VERSIONS:
        raise BadAuditJson(f"unknown schema version {version!r} (known: {KNOWN_SCHEMA_VERSIONS})")
    summary = _get(report, "summary", dict, "report")
    audited = _get(summary, "audited_packages", int, "summary")
    vulns = _get(report, "vulnerabilities", list, "report")
    statuses = _get(report, "adverse_statuses", list, "report")
    if _get(summary, "vulnerabilities", int, "summary") != len(vulns):
        raise BadAuditJson(
            f"summary says {summary['vulnerabilities']} vulnerabilities, list has {len(vulns)}"
        )
    if _get(summary, "adverse_statuses", int, "summary") != len(statuses):
        raise BadAuditJson(
            f"summary says {summary['adverse_statuses']} adverse statuses, list has {len(statuses)}"
        )

    findings = []
    for i, vuln in enumerate(vulns):
        where = f"vulnerabilities[{i}]"
        dep = _get(vuln, "dependency", dict, where)
        fixes = _get(vuln, "fix_versions", list, where)
        if not all(isinstance(f, str) for f in fixes):
            raise BadAuditJson(f"{where}.fix_versions: expected a list of strings")
        findings.append(
            {
                "id": _get(vuln, "id", str, where),
                "display_id": vuln.get("display_id") or "",
                "package": _get(dep, "name", str, f"{where}.dependency"),
                "version": _get(dep, "version", str, f"{where}.dependency"),
                "fix_versions": fixes,
                "summary": vuln.get("summary") or "",
            }
        )
    adverse = []
    for i, status in enumerate(statuses):
        where = f"adverse_statuses[{i}]"
        adverse.append(
            {
                "package": _get(status, "name", str, where),
                "status": _get(status, "status", str, where),
                "reason": status.get("reason") or "no reason given",
            }
        )
    return audited, findings, adverse


def gate(report: Any, uv_exit: int | None = None, out: Any = None) -> int:
    """Print the findings and return the exit code (PASS, BLOCK or BAD_INPUT)."""
    out = out or sys.stdout
    try:
        audited, findings, adverse = parse(report)
        # uv audit exits 0 when clean and 1 when it found something. If that ever
        # disagrees with the JSON, one of them changed meaning: stop and look.
        if uv_exit is not None and uv_exit != (1 if findings or adverse else 0):
            raise BadAuditJson(
                f"uv audit exited {uv_exit}, but the JSON has {len(findings)} vulnerabilities"
                f" and {len(adverse)} adverse statuses"
            )
    except BadAuditJson as exc:
        print(f"::error::audit gate: unexpected uv audit JSON: {exc}", file=out)
        print("The gate cannot judge this output, so it fails. Check `uv audit` changes.", file=out)
        return BAD_INPUT

    fixable = [f for f in findings if f["fix_versions"]]
    print(
        f"uv audit scanned uv.lock: {audited} packages, {len(findings)} vulnerabilities"
        f" ({len(fixable)} with a fix), {len(adverse)} adverse statuses",
        file=out,
    )
    for f in findings:
        label = f["id"] + (f" ({f['display_id']})" if f["display_id"] else "")
        if f["fix_versions"]:
            verdict = f"fix: {', '.join(f['fix_versions'])} -> BLOCKS"
        else:
            verdict = "no fix yet -> reported, does not block"
        print(f"  {label} {f['package']} {f['version']}: {verdict}. {f['summary']}", file=out)
    for a in adverse:
        print(
            f"  status {a['status']}: {a['package']} ({a['reason']}) -> reported, does not block",
            file=out,
        )
    if fixable:
        print(
            f"::error::audit gate: {len(fixable)} vulnerable package version(s) have a fix."
            " Upgrade them (uv lock --upgrade-package NAME), or add a dated --ignore with a"
            " reason in the workflow.",
            file=out,
        )
        return BLOCK
    print("audit gate: PASS (no vulnerability with a fix available)", file=out)
    return PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", type=Path, help="output of uv audit --output-format json")
    parser.add_argument("--uv-exit", type=int, help="uv audit's exit status, cross-checked")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::audit gate: cannot read {args.report}: {exc}")
        return BAD_INPUT
    return gate(report, args.uv_exit)


if __name__ == "__main__":
    sys.exit(main())
