# Engineering Review — Open Questions

**Purpose:** Every assumption the WonderVision pipeline makes that requires confirmation from a manufacturing engineer or process expert before the system is used for real quoting decisions.

Each item lists: the current assumption, the specific question, and the risk if the assumption is wrong.

Bring this to Andrew Fureno or a designated engineering contact at Schneider. Goal: lock in answers before calibration against real PO data.

---

## 1. Feature Extraction — What to Count

### 1.1 Cosmetic annotations excluded from feature list
**Current assumption:** The following are excluded from cost analysis entirely:
- "As fabricated" / "no finish" / "mill finish"
- Removable labels and part markings
- Revision notes and general tolerance notes

**Question for engineering:** Are there cases at Schneider where a "removable label" or marking callout actually requires a specific vendor operation (e.g. laser etching, silkscreen, stamping)? Or is it always a sticker the buyer applies themselves?

**Risk if wrong:** Vendor operations that should cost money get excluded from the quote.

---

### 1.2 Inspection and GD&T callouts excluded
**Current assumption:** GD&T callouts (flatness, perpendicularity, true position, etc.) are captured as features but not routed to a cost line item — we assume the vendor absorbs inspection into their standard rate.

**Question for engineering:** Do tight-tolerance callouts (e.g. true position ±0.005") require separate CMM inspection that vendors charge for? Is there a tolerance threshold above which inspection becomes a line item?

**Risk if wrong:** Parts with tight GD&T are underquoted — missing CMM or inspection cost.

---

### 1.3 Bend counting for compound profiles
**Current assumption:** Each distinct bend LINE is counted as one press brake operation. An L-bracket = 1 hit. A Z/offset profile = 2 hits.

**Question for engineering:** Is a Z/offset profile ever done in a single progressive die hit rather than two separate press brake operations? If so, how should it be priced? Does Schneider's vendor base primarily use press brakes or progressive dies for sheet metal?

**Risk if wrong:** Z-profiles could be over-costed if done as a single die hit, or under-costed if we collapse them to one.

---

### 1.4 Corner radii as a feature
**Current assumption:** Corner radii (R.13 TYP, etc.) are noted as features but assumed to be included in the laser cut profile — no separate cost line item.

**Question for engineering:** Are corner radii ever a separate operation (e.g. filed by hand, or requiring a specific tool change on the laser)? Or are they always just part of the laser cut path?

**Risk if wrong:** Small cost omission, low risk. Likely fine.

---

## 2. Process Routing — Machine Assignment

### 2.1 Sheet metal is always fully outsourced
**Current assumption:** All `sheet_metal` form type parts are quoted as complete vendor jobs (laser → press brake → tap → weld → finish). None of these operations are performed in-house at Schneider.

**Question for engineering:** Confirmed by Joe/Andrew — Schneider buys complete from vendors. But: do any vendors quote sheet metal piecemeal (laser only, then hand off to another vendor for forming)? If so, does Schneider ever manage that split, or is it always one vendor's problem?

**Risk if wrong:** Split-vendor jobs need a different costing model.

---

### 2.2 Tapping included in laser/sheet metal vendor scope
**Current assumption:** Tapped holes on sheet metal parts are done by the same vendor who does the laser cut and forming — no separate line item needed.

**Question for engineering:** Do Schneider's sheet metal vendors typically tap in-house, or is this often subbed out and billed separately? Is there a hole size threshold (e.g. M4 vs M12) that changes this?

**Risk if wrong:** Missing a separate tapping cost on certain parts.

---

### 2.3 Weld symbols — included vs. separate vendor
**Current assumption:** Weld callouts on a single-piece part (e.g. a stitch weld to attach a nut or bracket) are included in the vendor's sheet metal quote.

**Question for engineering:** When a weld callout appears on a drawing, is that weld always done by the primary sheet metal vendor? Or do some parts go to a separate weld shop? If separate, how should we identify which parts?

**Risk if wrong:** Missing a separate weld cost for parts that route to a weld shop.

---

## 3. Process Library — Time Constants

> The following constants are sourced from Machinery's Handbook and vendor industry benchmarks. They need calibration against Schneider's actual vendor invoice data to be accurate.

### 3.1 Laser cut — pierce time and feed rates
**Current values:**
- Pierce time: 0.0008 hr per pierce
- Feed rate: 75 in/min (A36 steel, 0.25")

**Question for engineering / vendor data:** What laser power and machine type do Schneider's primary sheet metal vendors run? (Fiber vs CO2, kW rating.) Feed rates vary 2–3× between machine types. A single real laser cut invoice with part dimensions would let us calibrate this.

---

### 3.2 Press brake — time per bend
**Current value:** 0.05 hr per bend (3 minutes per hit)

**Question for engineering:** Is 3 minutes per press brake hit reasonable for the part sizes Schneider typically buys? Does setup time change significantly for small vs large runs (1 pc vs 50 pcs)?

---

### 3.3 Tapping — time per hole
**Current value:** 0.012 hr per tapped hole (~45 seconds)

**Question for engineering:** Reasonable for standard taps (M6–M12 range)? Does this hold for large taps (M20+) or unusual thread forms?

---

### 3.4 TIG weld — time per weld
**Current value:** 0.083 hr per weld (~5 minutes)

**Question for engineering:** What's the typical weld length on Schneider parts? 5 min is reasonable for a 3–4" weld bead, but weld time scales with length. Should this be a per-inch formula rather than per-weld?

---

## 4. Material Pricing

### 4.1 Pricing unit for plate vs. sheet metal
**Current assumption:**
- `plate` (A36, stainless): priced per square inch of flat blank
- `sheet_metal`: priced per square inch of flat blank, same formula

**Question for engineering:** Do Schneider's vendors quote sheet metal material as a separate line item, or is material bundled into their per-piece price? If bundled, our material cost line is double-counting.

**Risk if wrong:** Material cost is counted twice on sheet metal parts — significant overestimate.

---

### 4.2 Scrap and nesting not modeled
**Current assumption:** Material cost = blank_length × blank_width × price/sq_in. No scrap factor, no nesting efficiency.

**Question for engineering:** Do Schneider's vendors charge for the full sheet even if the part only uses 30% of it? Or do they nest parts efficiently and charge for actual material used? Industry standard scrap factor is ~15–20%.

**Risk if wrong:** We underestimate material cost for small parts cut from large sheets.

---

## 5. Overhead and Margin

### 5.1 Overhead rate (currently 15%)
**Question for engineering/finance:** Is 15% a reasonable overhead estimate for Schneider's vendor base? Does this vary significantly by process type (machining vs. sheet metal vs. welding)?

### 5.2 Margin rate (currently 10%)
**Question for engineering/finance:** Is 10% a typical vendor margin for this type of work? Or does it vary by job size and relationship?

---

## 6. Drawing Interpretation Edge Cases

### 6.1 Multi-sheet drawings
**Current assumption:** The pipeline reads all pages of a PDF but treats the part as a single entity.

**Question for engineering:** Do Schneider drawings ever have multiple parts on multiple sheets (assembly drawings with sub-components)? If so, should each component be quoted separately?

---

### 6.2 Drawing zones
**Current assumption:** Drawing zones (A, B, C, D + 1, 2, 3, 4) are extracted as-is from zone labels on the drawing.

**Question for engineering:** Are these standard ASME-style zones, or does Schneider use a custom zone grid? Do zones meaningfully map to machine routing sequences for Schneider's vendors?

---

### 6.3 Revision history
**Current assumption:** Revision letter is extracted but has no effect on costing.

**Question for engineering:** Are there cases where a revision change (e.g. Rev A → Rev B) changes the part geometry significantly enough that an old quote would be invalid? Should the system warn if a drawing revision has changed since the last quote?

---

## 7. Vendor Database

### 7.1 Vendor capability matching
**Current assumption:** The foreman assigns process types (LASER_CUT, PRESS_BRAKE, etc.) generically — it does not yet match to a specific vendor.

**Question for engineering/purchasing:** Does Schneider have preferred vendors per process type? (e.g. "Vendor X does all laser cutting, Vendor Y does all welding.") If so, should the output include a vendor recommendation, not just a process type?

---

### 7.2 Vendor rate validation
**Question for engineering/purchasing:** Are the process rates in `vendor_processes.json` in the right ballpark for Schneider's vendor base? Key ones to validate:
- Laser Cut: $110/hr
- Press Brake: $60/hr
- TIG Weld: $70/hr
- CNC Mill: $85/hr

A single vendor rate card or recent invoice with line-item pricing would calibrate these in one shot.

---

## 8. Drawing Standards Reference — AI-Generated Content

### 8.1 Source of drawing_standards.json
**Current situation:** The file `data/drawing_standards.json` — which is injected into every drawing reader prompt — was generated by an AI model from training data about ANSI/ASME Y14.5 (GD&T and drawing notation conventions). It was not sourced from a licensed copy of the standard and has not been reviewed by a credentialed manufacturing engineer.

**This is distinct from ASTM:** ASTM publishes material *testing* standards (tensile, hardness, etc.) and has strict policies against AI use of their content. We are referencing ASME Y14.5 (geometric dimensioning and tolerancing) — a different organization. Material designations (A36, 304SS, 6061) used in our pricing files are public identifiers with no IP concern.

**Question for engineering:** Can a manufacturing engineer or drafter review `data/drawing_standards.json` for accuracy? Specifically:
- Are the GD&T symbol definitions correct per current Y14.5?
- Are the hole callout terms (THRU, C'BORE, C'SINK, TAP, etc.) correctly described?
- Are the weld symbol interpretations accurate per AWS A2.4?
- Are the material form codes (PL, SHT, HSS, RD, etc.) consistent with how Schneider's vendors use them?

**Risk if wrong:** The drawing reader uses these definitions to identify features. An incorrect GD&T or hole callout definition leads to wrong process assignment and wrong cost.

**Recommended action:** Have a drafter or ME spend 30 minutes reviewing the file against a copy of ASME Y14.5. Flag any definition that looks wrong. This is a one-time review that improves every future quote.

---

*Last updated: June 2026 — wonderful.intelligence*
