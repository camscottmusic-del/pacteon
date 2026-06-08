---
name: project-context
description: Core goals and design philosophy for the Pacteon should-cost calculator
metadata:
  type: project
---

## Purpose

Pacteon is a should-cost calculator for Schneider Packaging. Engineers submit custom part drawings to vendors and need accurate price quotes. The system runs a 3-stage AI pipeline to produce a structured estimate.

## AI Routing Philosophy

The AI shop foreman's job is NOT just to find a valid process sequence — it is to find the **optimal** one. The goal is for Schneider to assume the vendor is being efficient and not overcharging for unnecessary processes or wasted time. The estimate represents what a competent vendor *should* charge, not what any vendor *would* charge.

**Why:** This lets Schneider hold vendors to an efficiency standard when negotiating or auditing quotes.

**How to apply:** When enriching process data or tuning the foreman prompt, always ask: does this help the AI pick the most cost-efficient valid process, not just any valid process?

## Calibration Target

±10% of actual PO cost. Primary calibration data will come from historical Schneider PO data (not yet available). Until then, use industry-standard time constants from Machinery's Handbook and vendor spec sheets.
