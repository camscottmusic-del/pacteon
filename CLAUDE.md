# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This System Does

Pacteon is a **should-cost calculator** for Schneider Packaging. The core problem: engineers submit custom part drawings to vendors and need accurate price quotes, but doing this manually is slow and inconsistent. Claude web chat alone gets the pricing wrong because there's no structured context or deterministic math grounding it.

This system solves that by running a structured 3-stage AI pipeline:
1. **Drawing Reader** — a vision-capable Claude agent reads the engineering PDF, extracts the Bill of Materials (raw material + dimensions) and identifies every manufacturing feature by drawing zone (holes, welds, bends, taps, etc.)
2. **Shop Foreman** — a Claude agent acts as a virtual shop foreman, reads the features + the available machine inventory, and assigns each feature to the optimal machine with an estimated run time
3. **Cost Engine** — deterministic math: `material_cost = length × width × price_per_sq_in` + `machine_cost = (run_time + setup_time) × hourly_rate` + overhead + margin = final vendor quote

The output is a line-item quote **and** a machine routing sequence that can be uploaded to the ERP to generate shop orders.

Target accuracy: **±10%** of actual purchase/job cost.

## Commands

```bash
# Install (from repo root)
pip install -e ".[dev]"

# Run the pipeline against a drawing PDF
python -m pacteon.main path/to/drawing.pdf
python -m pacteon.main path/to/drawing.pdf 5   # quantity of 5

# Run tests (no API key needed — tests only cover deterministic math)
pytest tests/

# Run a single test
pytest tests/test_cost_calculator.py::test_material_cost_basic -v
```

## Architecture

```
src/pacteon/
├── main.py                # Pipeline entry point — orchestrates all 3 stages
├── agents/
│   ├── drawing_reader.py  # Stage 1: PDF → ExtractedDrawing (vision + tool_use)
│   ├── foreman.py         # Stage 2: ExtractedDrawing → [MachineProcess] (tool_use)
│   └── machine_worker.py  # Optional Stage 3: refine cycle-time per feature
├── models/
│   ├── drawing.py         # ExtractedDrawing, DrawingFeature (Pydantic)
│   ├── machine.py         # MachineProcess
│   └── quote.py           # Quote, LineItem — total_price computed from subtotal + overhead + margin
└── tools/
    ├── pdf_extractor.py   # pdfplumber: extract text + render pages as base64 images
    └── cost_calculator.py # Deterministic math — reads data/ JSON files, no AI
```

## Key Data Files

- `data/material_prices.json` — per-sq-in (plate/sheet) or per-linear-ft (bar/tube) pricing. Keep current with market rates.
- `data/vendor_processes.json` — vendor process types with hourly rates and setup times. Process IDs here must match vendor capability tags in the vendor database exactly.
- `data/process_library.json` — **the brain**: deterministic time formulas per process (geometry, count, area_tier). Sourced from Machinery's Handbook and vendor specs. Calibrate constants against real PO history to hit ±10%.
- `data/sample_drawings/` — place test PDFs here (gitignored by extension).

## Agent Models

- Drawing Reader uses `claude-opus-4-8` (vision required, complex parsing)
- Shop Foreman uses `claude-opus-4-8` (routing logic is nuanced)
- Machine Worker uses `claude-sonnet-4-6` (per-feature refinement, called many times)

All agents use `tool_choice: {type: "tool"}` to force structured output — never free-text responses.

## Pricing Formula

```
material_cost  = length_in × width_in × price_per_sq_in × quantity
machine_cost   = Σ (run_time_hr + setup_time_hr) × rate_per_hr  × quantity
subtotal       = material_cost + machine_cost
overhead       = subtotal × 0.15   (adjust in Quote model)
margin         = (subtotal + overhead) × 0.10
total_price    = subtotal + overhead + margin
```

Overhead and margin percentages live in `src/pacteon/models/quote.py` as defaults on the `Quote` model.

## Environment

Requires `ANTHROPIC_API_KEY` in `.env` (see `.env.example`).
