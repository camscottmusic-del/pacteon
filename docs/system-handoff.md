# Pacteon Should-Cost Calculator — System Handoff

**What it does:** Reads an engineering drawing PDF and returns a line-item vendor quote estimate with a machine routing sequence — independently, without relying on historical pricing data.

---

## Running the System

```bash
# Single drawing, quantity 1
python -m pacteon.main path/to/drawing.pdf

# Single drawing, quantity of 5
python -m pacteon.main path/to/drawing.pdf 5
```

**Requires:** Python 3.11+, an `ANTHROPIC_API_KEY` in the `.env` file. Each run costs roughly $0.10–0.30 in API usage depending on drawing complexity.

---

## The Three Files You Own

These live in `/data/` and are yours to maintain — no code changes needed.

| File | What it controls | When to update |
|------|-----------------|----------------|
| `material_prices.json` | Price per sq-in for each material grade | When steel/aluminum market prices shift significantly |
| `vendor_processes.json` | Hourly rate per process (laser, press brake, paint, etc.) | When you negotiate new rates with vendors |
| `process_library.json` | Time formulas per process | After you have line-item invoice data to calibrate against |

---

## If Accuracy Drifts

1. Run the drawing through the pipeline
2. Compare the TOTAL to the actual PO price
3. Identify which line item is furthest off (material vs. laser vs. paint, etc.)
4. Adjust the corresponding rate in `vendor_processes.json` or constant in `process_library.json`
5. Re-run to confirm

Target accuracy: **±10% of actual PO price.**

---

## What Can Break It

- **API key expires or hits quota** → estimates stop running; renew at console.anthropic.com
- **New material on a drawing** → add it to `material_prices.json` and `astm_material_library.json`
- **New process type** → add it to both `vendor_processes.json` and `process_library.json`

---

## Folder Map

```
data/
  material_prices.json     ← edit to update material costs
  vendor_processes.json    ← edit to update vendor rates
  process_library.json     ← edit to tune time formulas
  sample_drawings/         ← drop test PDFs here
  quotes.jsonl             ← auto-log of every run

src/pacteon/
  main.py                  ← pipeline entry point (do not edit)
  agents/                  ← AI stages (do not edit)
  tools/                   ← deterministic math (do not edit)
```

---

*Built by wonderful.intelligence. For support or calibration assistance, contact cameron@wonderful.intelligence*
