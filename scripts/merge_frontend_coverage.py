"""Merge Vitest's lcov and the browser lcov into the one report SonarCloud reads.

Vitest's record stands for every file it reports; the browser report supplies the rest.
Both name files from the repository root (`frontend/src/...`), so no path is rewritten.
"""

import sys
from pathlib import Path

SOURCE_PREFIX = "frontend/src/"
SOURCE_FILE = "SF:"
END_OF_RECORD = "end_of_record"
COUNTER_PAIRS = {"lines": ("LF", "LH"), "branches": ("BRF", "BRH"), "functions": ("FNF", "FNH")}
USAGE = "usage: merge_frontend_coverage.py <vitest lcov> <browser lcov> <merged lcov>"

type Records = dict[str, list[str]]


def read_records(report: Path) -> Records:
    if not report.is_file():
        raise SystemExit(f"merge_frontend_coverage: {report} is missing")
    records: Records = {}
    current: list[str] = []
    for line in report.read_text().splitlines():
        current.append(line)
        if line == END_OF_RECORD:
            source = next((entry for entry in current if entry.startswith(SOURCE_FILE)), None)
            if source is None:
                raise SystemExit(f"merge_frontend_coverage: {report} has a record without an {SOURCE_FILE} line")
            source = source.removeprefix(SOURCE_FILE)
            if source in records:
                raise SystemExit(f"merge_frontend_coverage: {report} names {source} twice")
            if not source.startswith(SOURCE_PREFIX):
                raise SystemExit(f"merge_frontend_coverage: {report} names {source}, outside {SOURCE_PREFIX}")
            records[source] = current
            current = []
    if current:
        raise SystemExit(f"merge_frontend_coverage: {report} ends without {END_OF_RECORD}")
    return records


def merge(vitest: Records, browser: Records) -> Records:
    return vitest | {source: record for source, record in browser.items() if source not in vitest}


def counters(record: list[str]) -> dict[str, int]:
    return {key: int(value) for key, _, value in (line.partition(":") for line in record) if value.isdigit()}


def describe(merged: Records) -> list[str]:
    found = {"lines": [0, 0], "branches": [0, 0], "functions": [0, 0]}
    below_full = []
    for source, record in merged.items():
        values = counters(record)
        for kind, (total_key, hit_key) in COUNTER_PAIRS.items():
            found[kind][0] += values.get(total_key, 0)
            found[kind][1] += values.get(hit_key, 0)
            if values.get(hit_key, 0) < values.get(total_key, 0):
                below_full.append(f"{source}: {kind} {values.get(hit_key, 0)}/{values.get(total_key, 0)}")
    totals = [f"total {kind}: {hit}/{total}" for kind, (total, hit) in found.items()]
    return [*below_full, *totals]


def main(arguments: list[str]) -> int:
    if len(arguments) != len(("vitest", "browser", "merged")):
        raise SystemExit(USAGE)
    vitest_report, browser_report, merged_report = (Path(argument) for argument in arguments)
    merged = merge(read_records(vitest_report), read_records(browser_report))
    merged_report.write_text("".join(f"{line}\n" for record in merged.values() for line in record))
    sys.stdout.write("\n".join(describe(merged)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
