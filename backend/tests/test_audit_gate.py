"""The dependency gate (D40): fail only on a vulnerability with a fix; fail loudly on odd JSON.

The fixtures are real `uv audit --locked --output-format json` output (uv 0.13.7, captured
2026-11-25): clean.json from this repo's uv.lock; fix_available.json from a throwaway
project pinned to requests 2.25.0 and ecdsa 0.19.1 (14 findings with a fix, 1 without);
no_fix.json from ecdsa 0.19.2 (Minerva, no fix planned) and typed-ast (archived on PyPI).
uv audit exited 0, 1 and 1 on them.
"""

import copy
import importlib.util
import io
import json
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_gate", _BACKEND / "scripts/audit_gate.py")
audit_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_gate)

FIXTURES = Path(__file__).parent / "fixtures" / "uv_audit"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def run(report: object, uv_exit: int | None = None) -> tuple[int, str]:
    out = io.StringIO()
    code = audit_gate.gate(report, uv_exit, out=out)
    return code, out.getvalue()


def test_clean_lockfile_passes() -> None:
    code, out = run(load("clean.json"), uv_exit=0)
    assert code == audit_gate.PASS
    assert "36 packages, 0 vulnerabilities (0 with a fix), 0 adverse statuses" in out


def test_finding_with_a_fix_blocks_and_every_finding_is_printed() -> None:
    code, out = run(load("fix_available.json"), uv_exit=1)
    assert code == audit_gate.BLOCK
    assert "15 vulnerabilities (14 with a fix)" in out
    assert "GHSA-j8r2-6x86-q33q (PYSEC-" in out
    assert "requests 2.25.0: fix: 2.31.0 -> BLOCKS" in out
    # The one without a fix is still listed, marked as not blocking.
    assert "GHSA-wj6h-64fc-37mp (PYSEC-2026-1325) ecdsa 0.19.1: no fix yet" in out
    assert "14 vulnerable package version(s) have a fix" in out


def test_finding_without_a_fix_and_archived_package_pass_but_are_printed() -> None:
    code, out = run(load("no_fix.json"), uv_exit=1)
    assert code == audit_gate.PASS
    assert "ecdsa 0.19.2: no fix yet -> reported, does not block" in out
    assert "status archived: typed-ast (no reason given) -> reported, does not block" in out


@pytest.mark.parametrize(
    "break_it",
    [
        lambda r: r.pop("vulnerabilities"),
        lambda r: r["schema"].update(version="1"),
        lambda r: r["summary"].update(vulnerabilities=0),
        lambda r: r["vulnerabilities"][0].pop("fix_versions"),
        lambda r: r["vulnerabilities"][1].update(fix_versions="0.19.2"),
        lambda r: r["vulnerabilities"][0]["dependency"].pop("version"),
        lambda r: (
            r["adverse_statuses"].append({"status": "archived"}),
            r["summary"].update(adverse_statuses=1),
        ),
    ],
    ids=[
        "no list",
        "new schema",
        "count mismatch",
        "no fix key",
        "fix not a list",
        "no version",
        "status without name",
    ],
)
def test_unexpected_json_fails_loudly(break_it) -> None:
    report = copy.deepcopy(load("fix_available.json"))
    break_it(report)
    code, out = run(report)
    assert code == audit_gate.BAD_INPUT
    assert "::error::audit gate: unexpected uv audit JSON" in out


@pytest.mark.parametrize("report", [[], "text", None])
def test_not_an_object_fails_loudly(report) -> None:
    assert run(report)[0] == audit_gate.BAD_INPUT


def test_exit_status_that_disagrees_with_the_json_fails_loudly() -> None:
    assert run(load("clean.json"), uv_exit=1)[0] == audit_gate.BAD_INPUT
    assert run(load("no_fix.json"), uv_exit=0)[0] == audit_gate.BAD_INPUT


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert audit_gate.main([str(FIXTURES / "fix_available.json"), "--uv-exit", "1"]) == 1
    assert audit_gate.main([str(FIXTURES / "clean.json")]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("warning: not json")
    assert audit_gate.main([str(bad)]) == audit_gate.BAD_INPUT
    assert "cannot read" in capsys.readouterr().out
