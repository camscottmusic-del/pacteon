"""
Pacteon Batch Runner — process a directory or zip file of drawing PDFs.

Usage:
    pacteon-batch drawings/          # full pipeline on all PDFs in directory
    pacteon-batch drawings.zip       # unzip and process
    pacteon-batch drawings/ --out results/ --workers 3 --resume
    pacteon-batch drawings/ --extract-only   # Stage 1 (vision) only — build extraction library

Checkpoint:
    results/batch_state.json tracks status per file. --resume skips completed entries.
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_RETRY_DELAYS = [10, 30, 60]  # seconds between Anthropic API retries


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class BatchRunner:
    def __init__(
        self,
        input_path: Path,
        out_dir: Path,
        quantity: int = 1,
        workers: int = 1,
        resume: bool = False,
        extract_only: bool = False,
    ):
        self.input_path = input_path
        self.out_dir = out_dir
        self.quantity = quantity
        self.workers = workers
        self.resume = resume
        self.extract_only = extract_only

        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "extractions").mkdir(exist_ok=True)
        if not extract_only:
            (self.out_dir / "quotes").mkdir(exist_ok=True)

        self._state_path = out_dir / "batch_state.json"
        self._lock = threading.Lock()

    def run(self) -> dict:
        pdfs = self._collect_pdfs()
        state = self._load_or_init_state(pdfs)
        self._save_state(state)

        pending = [
            fname for fname, entry in state["entries"].items()
            if entry["status"] in ("pending", "running")
        ]

        print(f"\nBatch: {len(pdfs)} PDFs found, {len(pending)} to process.")
        if self.extract_only:
            print("Mode: extract-only (Stage 1 vision pass only)")
        print(f"Workers: {self.workers}")
        print()

        if not pending:
            print("Nothing to do — all entries already completed or skipped.")
            self._write_report(state)
            return state

        if self.workers == 1:
            for fname in pending:
                self._process_one(fname, state)
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {pool.submit(self._process_one, fname, state): fname for fname in pending}
                for future in as_completed(futures):
                    future.result()  # surface any unexpected exceptions

        self._write_report(state)
        return state

    def _process_one(self, fname: str, state: dict):
        pdf_path = Path(state["_tmp_dir"]) / fname if state.get("_tmp_dir") else self.input_path / fname

        with self._lock:
            state["entries"][fname]["status"] = "running"
            self._save_state(state)

        print(f"  Processing: {fname}")
        start = time.time()
        last_exc = None

        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                if self.extract_only:
                    result = self._run_extract_only(fname, pdf_path)
                else:
                    result = self._run_full_pipeline(fname, pdf_path)

                elapsed = round(time.time() - start, 1)
                with self._lock:
                    state["entries"][fname].update(result)
                    state["entries"][fname]["status"] = "done"
                    state["entries"][fname]["completed_at"] = _timestamp()
                    self._save_state(state)

                total = result.get("total_price") or ""
                price_str = f" → ${total:.2f}" if total else ""
                print(f"  ✓ {fname}{price_str} ({elapsed}s)")
                return

            except _AnthropicApiError as exc:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    delay = _RETRY_DELAYS[attempt]
                    print(f"  ! {fname}: API error (attempt {attempt+1}) — retrying in {delay}s")
                    time.sleep(delay)

            except _EmptyDrawingError as exc:
                with self._lock:
                    state["entries"][fname]["status"] = "failed"
                    state["entries"][fname]["error"] = str(exc)
                    state["entries"][fname]["completed_at"] = _timestamp()
                    self._save_state(state)
                print(f"  ✗ {fname}: {exc}")
                return

            except Exception as exc:
                tb = traceback.format_exc()
                with self._lock:
                    state["entries"][fname]["status"] = "failed"
                    state["entries"][fname]["error"] = f"{type(exc).__name__}: {exc}"
                    state["entries"][fname]["traceback"] = tb
                    state["entries"][fname]["completed_at"] = _timestamp()
                    self._save_state(state)
                print(f"  ✗ {fname}: {type(exc).__name__}: {exc}")
                return

        # All retries exhausted
        with self._lock:
            state["entries"][fname]["status"] = "failed"
            state["entries"][fname]["error"] = f"Anthropic API error after {len(_RETRY_DELAYS)+1} attempts: {last_exc}"
            state["entries"][fname]["completed_at"] = _timestamp()
            self._save_state(state)
        print(f"  ✗ {fname}: all API retries failed")

    def _run_extract_only(self, fname: str, pdf_path: Path) -> dict:
        import anthropic
        from .agents.drawing_reader import DrawingReaderAgent

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        try:
            drawing = DrawingReaderAgent(client).read(pdf_path)
        except anthropic.APIError as e:
            raise _AnthropicApiError(str(e)) from e

        if not drawing.features and not drawing.material:
            raise _EmptyDrawingError("no features extracted — likely a BOM or title sheet, not a part drawing")

        drawing_path = self.out_dir / "extractions" / f"{Path(fname).stem}.json"
        drawing_path.write_text(drawing.model_dump_json(indent=2), encoding="utf-8")

        return {
            "drawing_json": str(drawing_path.relative_to(self.out_dir)),
            "quote_json": None,
            "total_price": None,
            "part_number": drawing.part_number,
            "part_name": drawing.part_name,
        }

    def _run_full_pipeline(self, fname: str, pdf_path: Path) -> dict:
        import anthropic
        from .main import run_pipeline

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        try:
            quote = run_pipeline(pdf_path, quantity=self.quantity)
        except anthropic.APIError as e:
            raise _AnthropicApiError(str(e)) from e

        if quote.total_price == 0 and not quote.line_items:
            raise _EmptyDrawingError("no cost lines produced — likely not a part drawing")

        drawing_path = self.out_dir / "extractions" / f"{Path(fname).stem}.json"
        quote_path   = self.out_dir / "quotes"      / f"{Path(fname).stem}.json"

        quote_path.write_text(quote.model_dump_json(indent=2), encoding="utf-8")

        return {
            "drawing_json": str(drawing_path.relative_to(self.out_dir)) if drawing_path.exists() else None,
            "quote_json": str(quote_path.relative_to(self.out_dir)),
            "total_price": round(quote.total_price, 2),
            "part_number": quote.part_number,
            "part_name": quote.part_name,
        }

    def _collect_pdfs(self) -> list[str]:
        """Collect PDF filenames. If input is a zip, extract to a temp dir and return the dir."""
        if self.input_path.suffix.lower() == ".zip":
            tmp = tempfile.mkdtemp(prefix="pacteon_batch_")
            with zipfile.ZipFile(self.input_path, "r") as zf:
                zf.extractall(tmp)
            self._tmp_dir = Path(tmp)
            return sorted(
                str(p.relative_to(tmp))
                for p in Path(tmp).rglob("*.pdf")
            )
        else:
            self._tmp_dir = None
            return sorted(p.name for p in self.input_path.glob("*.pdf"))

    def _load_or_init_state(self, pdf_files: list[str]) -> dict:
        if self.resume and self._state_path.exists():
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            # Add any new files not in the existing state
            for fname in pdf_files:
                if fname not in state["entries"]:
                    state["entries"][fname] = {"status": "pending", "error": None}
            # Reset any "running" entries (they were interrupted)
            for entry in state["entries"].values():
                if entry["status"] == "running":
                    entry["status"] = "pending"
        else:
            state = {
                "batch_id": f"b-{datetime.now().strftime('%Y%m%d-%H%M')}",
                "started_at": _timestamp(),
                "input_source": str(self.input_path),
                "quantity": self.quantity,
                "extract_only": self.extract_only,
                "_tmp_dir": str(self._tmp_dir) if self._tmp_dir else None,
                "entries": {
                    fname: {
                        "status": "pending",
                        "completed_at": None,
                        "drawing_json": None,
                        "quote_json": None,
                        "total_price": None,
                        "part_number": None,
                        "part_name": None,
                        "error": None,
                    }
                    for fname in pdf_files
                },
            }
        return state

    def _save_state(self, state: dict):
        self._state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _write_report(self, state: dict):
        entries = state["entries"]
        done    = [f for f, e in entries.items() if e["status"] == "done"]
        failed  = [f for f, e in entries.items() if e["status"] == "failed"]
        skipped = [f for f, e in entries.items() if e["status"] == "skipped"]

        lines = [
            "=" * 60,
            f"Pacteon Batch Report  [{_timestamp()}]",
            f"Input:   {state['input_source']}",
            f"Mode:    {'extract-only' if state['extract_only'] else 'full pipeline'}",
            f"Quantity: {state['quantity']}",
            "=" * 60,
            f"  Total:    {len(entries)}",
            f"  Success:  {len(done)}",
            f"  Failed:   {len(failed)}",
            f"  Skipped:  {len(skipped)}",
            "",
        ]

        if done:
            lines.append("Completed (sorted by total price desc):")
            priced = sorted(
                [(f, entries[f]) for f in done if entries[f].get("total_price")],
                key=lambda x: x[1]["total_price"],
                reverse=True,
            )
            no_price = [(f, entries[f]) for f in done if not entries[f].get("total_price")]
            for fname, e in priced + no_price:
                price_str = f"  ${e['total_price']:.2f}" if e.get("total_price") else ""
                part = e.get("part_number") or e.get("part_name") or ""
                lines.append(f"  ✓ {fname}{price_str}  {part}")

        if failed:
            lines.append("")
            lines.append("Failed:")
            for fname in failed:
                lines.append(f"  ✗ {fname}: {entries[fname].get('error', 'unknown error')}")

        report_path = self.out_dir / "batch_report.txt"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n" + "\n".join(lines))
        print(f"\nFull results: {self.out_dir / 'batch_state.json'}")


class _AnthropicApiError(Exception):
    pass


class _EmptyDrawingError(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(
        prog="pacteon-batch",
        description="Process a directory or zip file of drawing PDFs through the Pacteon pipeline.",
    )
    parser.add_argument("input", help="Directory of PDFs or a .zip file")
    parser.add_argument("--out", default="batch_output", help="Output directory (default: batch_output)")
    parser.add_argument("--qty", type=int, default=1, help="Part quantity for cost calculation")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default: 1; recommend 3 for large batches)")
    parser.add_argument("--resume", action="store_true", help="Resume from existing batch_state.json checkpoint")
    parser.add_argument("--extract-only", action="store_true", help="Run Stage 1 (vision extraction) only — no routing or costing")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    runner = BatchRunner(
        input_path=input_path,
        out_dir=Path(args.out),
        quantity=args.qty,
        workers=args.workers,
        resume=args.resume,
        extract_only=args.extract_only,
    )
    runner.run()


if __name__ == "__main__":
    main()
