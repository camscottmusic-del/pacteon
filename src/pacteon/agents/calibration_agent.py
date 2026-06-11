"""
CalibrationAgent — Reflect phase of the Observe → Do → Reflect loop.

Analyzes historical should-cost estimates vs. real PO prices (from data/quotes.jsonl)
and recommends specific numeric changes to process_library.json constants.
Output is written to data/calibration_pending.json for human review before any
changes are applied to process_library.json.
"""
import json
import statistics
from pathlib import Path
from typing import Any

import anthropic

_PROCESS_LIB_PATH = Path(__file__).parents[3] / "data" / "process_library.json"

_SUGGEST_TOOL: dict[str, Any] = {
    "name": "suggest_calibration",
    "description": "Recommend specific numeric changes to process_library.json constants based on historical estimation errors.",
    "input_schema": {
        "type": "object",
        "properties": {
            "analysis_summary": {
                "type": "string",
                "description": "2-3 sentence summary of the overall bias direction and magnitude."
            },
            "sample_count": {"type": "integer"},
            "mean_delta_pct": {
                "type": "number",
                "description": "Mean (estimate - actual) / actual × 100. Positive = over-estimating."
            },
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "process_id": {"type": "string"},
                        "parameter_path": {
                            "type": "string",
                            "description": "Dot-path into process_library.json, e.g. 'LASER_CUT.feed_rates_in_per_min.A36_STEEL_0250'"
                        },
                        "current_value": {"type": "number"},
                        "recommended_value": {"type": "number"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "justification": {
                            "type": "string",
                            "description": "Explain which samples justify this change and why."
                        },
                        "supporting_part_numbers": {
                            "type": "array",
                            "items": {"type": "string"}
                        }
                    },
                    "required": ["process_id", "parameter_path", "current_value", "recommended_value", "confidence", "justification", "supporting_part_numbers"]
                }
            },
            "no_change_recommended": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "process_id": {"type": "string"},
                        "reason": {"type": "string"}
                    },
                    "required": ["process_id", "reason"]
                }
            },
            "new_process_gaps_observed": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Processes that appeared on real POs but are missing from process_library.json."
            }
        },
        "required": ["analysis_summary", "sample_count", "mean_delta_pct", "recommendations", "no_change_recommended"]
    }
}

_SYSTEM_PROMPT = """\
You are a manufacturing cost analyst for Schneider Packaging.

You have access to historical should-cost estimates and the real vendor PO prices they were \
compared against. Your job is to identify systematic errors in the time constants used by the \
estimating formulas and recommend specific numeric changes to process_library.json that would \
move the estimates closer to actual PO prices.

You are NOT changing pricing strategy — Schneider does not add vendor margin in this estimate. \
You are correcting the technical accuracy of the time formulas (feed rates, time_per_unit_hr, \
time_per_in_hr, setup times, etc.).

## Key formula types and what to adjust

- **geometry** (LASER_CUT, WATERJET, TUBE_LASER): adjust `feed_rates_in_per_min` by \
  material+thickness key, or `pierce_time_hr`
- **count** (DRILL, TAP, CNC_MILL, LATHE, LASER_MARK): adjust `time_per_unit_hr` \
  or setup formula constants
- **bend_geometry** (PRESS_BRAKE): adjust `time_per_bend_hr` or `time_per_in_hr`
- **linear** (WELD_TIG, WELD_MIG): adjust `time_per_in_hr`
- **area_linear** (PAINT, POWDER_COAT, ANODIZE, BLAST, ZINC_PLATE): adjust \
  `time_per_sq_in_hr` or `base_time_hr`

## Rules for recommendations

1. Every recommendation MUST cite the specific part numbers that justify it.
2. Only recommend changes with statistical support — at least 3 samples showing the same
   bias direction. Do not recommend based on 1–2 samples.
3. Confidence levels:
   - **high**: ≥5 samples, consistent direction, delta > 15%
   - **medium**: 3–4 samples, consistent direction, or 5+ samples with delta 8–15%
   - **low**: marginal evidence, mention but do not recommend applying automatically
4. Do not recommend changes that would move a constant outside physically plausible ranges
   (e.g., laser feed rate > 300 in/min or < 5 in/min is suspicious).
5. If a process consistently shows a small, random delta (<5% mean), recommend no change.
6. For new process gaps (processes on POs not in the library), note them in
   `new_process_gaps_observed` with the typical charge rate you observed.

Always call suggest_calibration. Never respond with plain text.
"""


class CalibrationAgent:
    def __init__(self, client: anthropic.Anthropic):
        self.client = client
        self.process_lib = json.loads(_PROCESS_LIB_PATH.read_text(encoding="utf-8"))

    def analyze(self, samples: list[dict]) -> dict:
        """
        Analyze calibration samples and return a structured recommendation dict.
        samples: list of dicts with keys: part_number, drawing, quote, po_actual_price, delta_pct
        """
        stats = self._compute_stats(samples)
        prompt = self._build_prompt(samples, stats)

        response = self.client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            tools=[_SUGGEST_TOOL],
            tool_choice={"type": "tool", "name": "suggest_calibration"},
            messages=[{"role": "user", "content": prompt}],
        )

        return next(b.input for b in response.content if b.type == "tool_use")

    def _compute_stats(self, samples: list[dict]) -> dict:
        deltas = [s["delta_pct"] for s in samples if s.get("delta_pct") is not None]
        if not deltas:
            return {}
        by_process: dict[str, list[float]] = {}
        for s in samples:
            for item in s.get("quote", {}).get("line_items", []):
                desc = item.get("description", "")
                for pid in self.process_lib:
                    if pid.lower() in desc.lower():
                        by_process.setdefault(pid, []).append(s["delta_pct"])
        return {
            "mean_delta_pct": round(statistics.mean(deltas), 2),
            "std_delta_pct": round(statistics.stdev(deltas), 2) if len(deltas) > 1 else 0,
            "worst_over": max(deltas),
            "worst_under": min(deltas),
            "process_means": {pid: round(statistics.mean(d), 2) for pid, d in by_process.items() if d},
        }

    def _build_prompt(self, samples: list[dict], stats: dict) -> str:
        lib_json = json.dumps(self.process_lib, indent=2)
        samples_rows = []
        for s in samples:
            q = s.get("quote", {})
            d = s.get("drawing", {})
            samples_rows.append(
                f"  part={s.get('part_number','?')} | "
                f"material={d.get('material','?')} | "
                f"form={d.get('part_form_type','?')} | "
                f"thick={d.get('thickness_in','?')}\" | "
                f"estimate=${q.get('total_price',0):.2f} | "
                f"actual=${s.get('po_actual_price',0):.2f} | "
                f"delta={s.get('delta_pct',0):+.1f}%"
            )

        return (
            f"Current process_library.json:\n```json\n{lib_json}\n```\n\n"
            f"Calibration samples ({len(samples)} total):\n"
            + "\n".join(samples_rows)
            + f"\n\nStatistics:\n{json.dumps(stats, indent=2)}\n\n"
            "Analyze the samples and recommend specific changes to process_library.json constants. "
            "Cite specific part numbers for each recommendation."
        )
