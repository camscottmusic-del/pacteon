---
name: project-backlog
description: Deferred features for Pacteon that are not yet scheduled for implementation
metadata:
  type: project
---

## Quote Accuracy Logging

Log each pipeline run to `data/quotes.jsonl`. Add a `pacteon record-actual <part_number> <actual_price>` CLI command so someone can fill in the PO amount when it comes back from the vendor. Compute and store the % delta. Consider SQLite over JSONL for easier in-place updates and querying.

**Why:** PO data only becomes available days/weeks after the estimate. The goal is to build a historical dataset to measure whether estimates are hitting ±10% accuracy and to identify which material prices or process time constants need calibration.

**How to apply:** Do not implement until Schneider PO data starts coming in and there is something to log against. Prioritize data enrichment first.
