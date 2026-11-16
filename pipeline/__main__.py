"""CLI-точка входу: python -m pipeline quality --student S03 --input runtime/source_01 --reference-time ..."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

from .db import begin_quality_run, mark_quality_run_failed, persist_validation_result, resolve_dsn
from .validator import (
    load_contract,
    parse_iso_timestamptz,
    validate_export_directory,
    validate_fixture_file,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description="CityFlow Data Quality Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    q_parser = subparsers.add_parser("quality", help="Run automated data quality checks and publish validated data")
    q_parser.add_argument("--student", required=True, help="Student ID (e.g. S03)")
    q_parser.add_argument("--input", required=True, type=Path, help="Export directory or fixture JSON file")
    q_parser.add_argument("--reference-time", required=True, help="Reference ISO timestamp with offset/Z")
    q_parser.add_argument("--scope", choices=["project", "test"], default=None, help="Execution scope")
    q_parser.add_argument("--dry-run", action="store_true", help="Validate without publishing to validated.*")
    q_parser.add_argument("--no-db", action="store_true", help="Run in memory only without database connection")
    q_parser.add_argument("--fail-before-commit", action="store_true", help="Simulate failure before COMMIT")
    q_parser.add_argument("--contract", type=Path, default=None, help="Path to contract JSON")
    q_parser.add_argument("--report-out", type=Path, default=None, help="Path to write JSON summary report")

    args = parser.parse_args(argv)
    run_id = str(uuid.uuid4())

    ref_dt = parse_iso_timestamptz(args.reference_time)
    if ref_dt is None:
        print(f"Invalid --reference-time: {args.reference_time}", file=sys.stderr)
        return 1

    contract = load_contract(args.contract)
    contract_version = str(contract.get("contract_version", "cityflow_v1"))

    input_path: Path = args.input
    if args.scope is not None:
        scope = args.scope
    elif args.dry_run or input_path.is_file():
        scope = "test"
    else:
        scope = "project"

    report_path = args.report_out or (Path("runtime") / "reports" / f"quality_{run_id}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    dsn: str | None = None
    started_in_db = False

    try:
        if not args.no_db:
            dsn = resolve_dsn()
            begin_quality_run(
                dsn,
                run_id=run_id,
                student_id=args.student,
                scope=scope,
                reference_time=ref_dt,
                contract_version=contract_version,
                source_manifest={"input": str(input_path)},
            )
            started_in_db = True

        if input_path.is_file():
            result = validate_fixture_file(
                input_path,
                reference_time=ref_dt,
                contract_version=contract_version,
            )
        else:
            result = validate_export_directory(
                input_path,
                student_id=args.student,
                reference_time=ref_dt,
                scope=scope,
                contract_version=contract_version,
            )

        if args.dry_run:
            result.publish = False

        if not args.no_db and dsn is not None:
            persist_validation_result(
                dsn,
                run_id=run_id,
                result=result,
                fail_before_commit=args.fail_before_commit,
            )
        elif args.fail_before_commit:
            raise RuntimeError("INJECTED_BEFORE_COMMIT")

        report_payload = result.to_report_dict(run_id)
        report_path.write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": result.status,
                    "scope": result.scope,
                    "published": result.publish and result.status in ("PASS", "WARN"),
                    "report_path": str(report_path),
                    "entity_summary": result.entity_summary,
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.status in ("PASS", "WARN") else 1

    except Exception as exc:
        code = str(exc) if str(exc).startswith("INJECTED_") else type(exc).__name__
        if started_in_db and dsn is not None:
            try:
                mark_quality_run_failed(dsn, run_id=run_id, error_code=code)
            except Exception:
                pass
        fail_payload = {
            "run_id": run_id,
            "student_id": args.student,
            "scope": scope,
            "reference_time": ref_dt.isoformat(),
            "contract_version": contract_version,
            "status": "FAILED",
            "error_code": code,
            "report_path": str(report_path),
        }
        report_path.write_text(
            json.dumps(fail_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": "FAILED",
                    "error_code": code,
                    "report_path": str(report_path),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())