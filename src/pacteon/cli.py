"""
Pacteon CLI — calibration loop commands.

Usage:
    pacteon record-actual <part_number> <actual_price>   # log a real PO price
    pacteon calibrate [--min-samples N]                  # run calibration analysis
    pacteon calibrate apply                              # apply high-confidence recommendations
    pacteon calibrate review                             # interactive accept/reject
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_QUOTES_LOG = Path(__file__).parents[2] / "data" / "quotes.jsonl"
_CALIBRATION_PENDING = Path(__file__).parents[2] / "data" / "calibration_pending.json"
_CALIBRATION_LOG = Path(__file__).parents[2] / "data" / "calibration_log.json"
_PROCESS_LIB = Path(__file__).parents[2] / "data" / "process_library.json"


def _load_quotes() -> list[dict]:
    if not _QUOTES_LOG.exists():
        return []
    return [json.loads(line) for line in _QUOTES_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def _save_quotes(quotes: list[dict]):
    _QUOTES_LOG.write_text(
        "\n".join(json.dumps(q) for q in quotes) + "\n",
        encoding="utf-8",
    )


def cmd_record_actual(args):
    part_number = args.part_number
    actual_price = args.actual_price

    quotes = _load_quotes()
    # Find most recent run with this part number that has no actual yet
    updated = False
    for q in reversed(quotes):
        if q.get("part_number") == part_number and q.get("po_actual_price") is None:
            estimate = q.get("quote", {}).get("total_price", 0) or 0
            delta_pct = ((estimate - actual_price) / actual_price * 100) if actual_price else None
            q["po_actual_price"] = actual_price
            q["delta_pct"] = round(delta_pct, 2) if delta_pct is not None else None
            q["actual_recorded_at"] = datetime.now(timezone.utc).isoformat()
            updated = True
            print(f"Recorded: {part_number} | estimate=${estimate:.2f} | actual=${actual_price:.2f} | delta={delta_pct:+.1f}%")
            break

    if not updated:
        print(f"No pending run found for part number '{part_number}'. Run the pipeline first.")
        sys.exit(1)

    _save_quotes(quotes)


def cmd_calibrate(args):
    import anthropic
    from .agents.calibration_agent import CalibrationAgent

    action = getattr(args, "action", None)

    if action == "apply":
        _apply_calibration(auto=True)
        return
    if action == "review":
        _apply_calibration(auto=False)
        return

    # Default: run the analysis
    min_samples = getattr(args, "min_samples", 10)
    quotes = _load_quotes()
    samples = [q for q in quotes if q.get("po_actual_price") is not None]

    if len(samples) < min_samples:
        print(f"Not enough calibration samples: {len(samples)} available, {min_samples} required.")
        print("Use 'pacteon record-actual' to add real PO prices, or lower --min-samples.")
        sys.exit(1)

    print(f"Running calibration analysis on {len(samples)} samples...")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    agent = CalibrationAgent(client)
    result = agent.analyze(samples)

    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["sample_count"] = len(samples)
    _CALIBRATION_PENDING.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"\nAnalysis complete. {len(result.get('recommendations', []))} recommendations written to:")
    print(f"  {_CALIBRATION_PENDING}")
    print(f"\nSummary: {result.get('analysis_summary', '')}")
    print(f"Mean delta: {result.get('mean_delta_pct', 0):+.1f}%\n")

    for r in result.get("recommendations", []):
        conf = r["confidence"].upper()
        print(f"  [{conf}] {r['process_id']} → {r['parameter_path']}")
        print(f"    {r['current_value']} → {r['recommended_value']}  ({r['justification'][:80]}...)")
        print()

    print("To apply high-confidence recommendations: pacteon calibrate apply")
    print("To review each one interactively:         pacteon calibrate review")


def _apply_calibration(auto: bool):
    if not _CALIBRATION_PENDING.exists():
        print("No pending calibration. Run 'pacteon calibrate' first.")
        sys.exit(1)

    pending = json.loads(_CALIBRATION_PENDING.read_text(encoding="utf-8"))
    recs = pending.get("recommendations", [])
    if not recs:
        print("No recommendations in calibration_pending.json.")
        return

    process_lib = json.loads(_PROCESS_LIB.read_text(encoding="utf-8"))
    applied = []
    skipped = []

    for r in recs:
        if auto and r["confidence"] != "high":
            skipped.append(r)
            continue

        if not auto:
            print(f"\n[{r['confidence'].upper()}] {r['process_id']} → {r['parameter_path']}")
            print(f"  {r['current_value']} → {r['recommended_value']}")
            print(f"  Reason: {r['justification']}")
            answer = input("  Apply? [y/N] ").strip().lower()
            if answer != "y":
                skipped.append(r)
                continue

        # Apply the change via dot-path
        parts = r["parameter_path"].split(".")
        obj = process_lib
        for part in parts[:-1]:
            obj = obj[part]
        obj[parts[-1]] = r["recommended_value"]
        applied.append(r)
        print(f"  Applied: {r['parameter_path']} = {r['recommended_value']}")

    if applied:
        _PROCESS_LIB.write_text(json.dumps(process_lib, indent=2), encoding="utf-8")

        # Append to calibration log
        log_entry = {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "source_analysis_generated_at": pending.get("generated_at"),
            "sample_count": pending.get("sample_count"),
            "applied": applied,
            "skipped_count": len(skipped),
        }
        existing_log = []
        if _CALIBRATION_LOG.exists():
            existing_log = json.loads(_CALIBRATION_LOG.read_text(encoding="utf-8"))
        existing_log.append(log_entry)
        _CALIBRATION_LOG.write_text(json.dumps(existing_log, indent=2), encoding="utf-8")

        print(f"\nApplied {len(applied)} change(s) to process_library.json.")
        print(f"Log written to {_CALIBRATION_LOG}")
    else:
        print("\nNo changes applied.")

    _CALIBRATION_PENDING.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(prog="pacteon", description="Pacteon calibration loop CLI")
    subparsers = parser.add_subparsers(dest="command")

    # record-actual
    record_p = subparsers.add_parser("record-actual", help="Log a real PO price for a previously estimated part")
    record_p.add_argument("part_number", help="Part number (must match a run in quotes.jsonl)")
    record_p.add_argument("actual_price", type=float, help="Actual vendor PO price in USD")

    # calibrate
    cal_p = subparsers.add_parser("calibrate", help="Run calibration analysis and apply recommendations")
    cal_p.add_argument("action", nargs="?", choices=["apply", "review"], help="apply (auto high-conf) or review (interactive)")
    cal_p.add_argument("--min-samples", type=int, default=10, help="Minimum samples required (default: 10)")

    args = parser.parse_args()

    if args.command == "record-actual":
        cmd_record_actual(args)
    elif args.command == "calibrate":
        cmd_calibrate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
