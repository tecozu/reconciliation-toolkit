"""File health check: find the encoding and formatting damage that silently corrupts loads.

Streams the file line by line (works on multi-GB files) and reports:
* U+FFFD replacement characters (bytes lost in a bad encoding conversion)
* mojibake: UTF-8 read as Latin-1 ('Ã§' instead of 'ç', 'Ã£' instead of 'ã')
* HTML entities leaking from web exports ('&#39;', '&amp;')
* control characters, a UTF-8 BOM, and rows whose field count differs from the header
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

CHECKS = {
    "replacement_char": re.compile("�"),
    "mojibake": re.compile("Ã[\u0080-¿§£©¡³ºª]|Â[ -¿]"),
    "html_entity": re.compile(r"&(#\d{2,5}|[a-z]{2,8});"),
    "control_char": re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"),
}


@dataclass
class HealthReport:
    path: str
    lines: int = 0
    bom: bool = False
    header_fields: int = 0
    issues: dict[str, list[int]] = field(default_factory=dict)  # issue -> line numbers

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, issue: str, line: int) -> None:
        self.issues.setdefault(issue, []).append(line)


def check_file(path: str | Path, delimiter: str = ";", encoding: str = "utf-8") -> HealthReport:
    report = HealthReport(str(path))
    with open(path, "rb") as fb:
        report.bom = fb.read(3) == b"\xef\xbb\xbf"
    with open(
        path, encoding="utf-8-sig" if report.bom else encoding, errors="replace", newline=""
    ) as fh:
        for n, line in enumerate(fh, start=1):
            line = line.rstrip("\r\n")
            report.lines = n
            if n == 1:
                report.header_fields = line.count(delimiter) + 1
            elif line and line.count(delimiter) + 1 != report.header_fields:
                report.add("field_count_mismatch", n)
            for name, rx in CHECKS.items():
                if rx.search(line):
                    report.add(name, n)
    return report


def render(report: HealthReport, max_lines: int = 10) -> str:
    head = f"{report.path}: {report.lines} lines, {report.header_fields} columns"
    head += ", UTF-8 BOM" if report.bom else ""
    if report.ok:
        return f"OK    {head}"
    out = [f"ISSUES {head}"]
    for issue, lines in report.issues.items():
        shown = ", ".join(map(str, lines[:max_lines])) + (" ..." if len(lines) > max_lines else "")
        out.append(f"  - {issue}: {len(lines)} line(s): {shown}")
    return "\n".join(out)
