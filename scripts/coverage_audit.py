#!/usr/bin/env python3
"""Audit module and function coverage from coverage.py data."""

import ast
import json
import os
import sys
from pathlib import Path

from coverage import Coverage

ROOT = Path(__file__).resolve().parent.parent

EXCLUDED = {
    "automation/rotate_device_passwords.py"
}


def inspect_module(cov, path):
    relative = path.relative_to(ROOT).as_posix()

    _, statements, _, missing, _ = cov.analysis2(str(path))

    statements = set(statements)
    missing = set(missing)
    executed = statements - missing

    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = []

    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue

        # Exclude function definition lines, which can run
        # merely when a module is imported.
        first_body_line = node.body[0].lineno

        body_lines = [
            line for line in statements
            if node.lineno < line <= node.end_lineno
            and line >= first_body_line
        ]

        hits = len(set(body_lines) & executed)

        functions.append({
            "name": node.name,
            "line": node.lineno,
            "executed": hits > 0,
            "lines_hit": hits,
            "lines_total": len(body_lines)
        })

    functions.sort(key=lambda f: f["line"])

    hit_count = sum(f["executed"] for f in functions)

    return {
        "module": relative,
        "lines_hit": len(executed),
        "lines_total": len(statements),
        "line_pct": round(
            100 * len(executed) / len(statements), 1
        ) if statements else 100.0,
        "functions_hit": hit_count,
        "functions_total": len(functions),
        "function_pct": round(
            100 * hit_count / len(functions), 1
        ) if functions else None,
        "functions": functions
    }


def main():
    data_path = Path(
        os.environ.get("COVERAGE_FILE", ".coverage")
    )

    if not data_path.is_file():
        sys.exit(
            f"Missing coverage data: {data_path}"
        )

    cov = Coverage(
        data_file=str(data_path),
        config_file=False
    )
    cov.load()

    modules = []

    for directory in ("automation", "webapp"):
        for path in sorted(
            (ROOT / directory).rglob("*.py")
        ):
            relative = path.relative_to(ROOT).as_posix()

            if relative in EXCLUDED:
                continue

            item = inspect_module(cov, path)

            if item["lines_total"]:
                modules.append(item)

    print(
        f"{'Module':44} {'Lines':>7} {'Functions':>12}"
    )
    print("-" * 67)

    for item in modules:
        function_pct = (
            f"{item['function_pct']}%"
            if item["function_pct"] is not None
            else "n/a"
        )

        print(
            f"{item['module'][:44]:44} "
            f"{item['line_pct']:6.1f}% "
            f"{item['functions_hit']}/"
            f"{item['functions_total']} "
            f"({function_pct})"
        )

        for fn in item["functions"]:
            if not fn["executed"]:
                print(
                    f"    UNTESTED: {fn['name']} "
                    f"(line {fn['line']})"
                )

    report = {
        "scope": ["automation", "webapp"],
        "excluded": sorted(EXCLUDED),
        "modules": modules,
        "note": (
            "Execution coverage measures code exercised "
            "by tests, not correctness of those tests."
        )
    }

    output = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "/tmp/ana-coverage-audit.json"
    )

    output.parent.mkdir(
        parents=True, exist_ok=True
    )

    output.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8"
    )

    print(f"\nSaved JSON report: {output}")
    print(
        "Function coverage measures exercised "
        "function bodies."
    )
    print(
        "Line coverage excludes branch coverage."
    )


if __name__ == "__main__":
    main()
