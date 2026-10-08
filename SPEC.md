# Pushpak Q&A — Spec (Phase 0)

Last updated: 2026-10-07 (per all 4 source docs). Scope: answer ONLY from the 4 Pushpak docs.
Non-doc answers must be verbatim: “Not specified in the provided documents.”

## 1. Answering rules
- Always cite: document name, section, page (pages from `data/chunks.jsonl`).
- Never calculate in prose; call the rules engine (`Return Fresh = Expected − Delivered`,
  photo counts, delivery/pickup validation, trip-completion check) and explain the result.
- Never confuse: Delivery vs Pickup · Return Fresh (quantity) vs Shortage Reason (why) ·
  Heavy Soil (stain/dirt) vs Damaged (physical) · Transit damage (fresh linen) vs Pickup damage (used linen) ·
  Pending hotel (blocks End Trip) vs Pending pickup (does NOT block End Trip).
- Do not guess on open questions (list below). Log them for human review.

## 2. Deterministic rules (enforced by app + rules engine)
**Delivery / Return Fresh** (`Pushpak — Delivery Items`): `Return Fresh = Expected − Delivered`;
`Delivered ≤ Expected`; `Return Fresh ≤ Expected`; shortage ≥ 1 requires Return Fresh (even 500/499 → 1);
`Return Fresh = 0` valid when exact; per-product entry; app blocks progression when required
Return Fresh missing. Editable before Save and Continue; editable after DC generation but before OTP
(must regenerate DC + new OTP); immutable after OTP.
**Shortage handling:** short delivery → Save and Continue → select Shortage Reason
(Customer Refused, Packed Less, Damaged in Transit, Soiled Quantity Less, Other) → Generate DC.
Hotel refuses part → Delivered = accepted, rest = Return Fresh, Reason = Customer Refused.
Transit damage → good units delivered, damaged = Return Fresh, Reason = Damaged in Transit, damaged
fresh returned to warehouse. Warehouse packed extra (e.g. 520 vs 500) → deliver max 500.
**DC / OTP** (`Pushpak — Digital DC and OTP`): Digital DC auto-generated from entered quantities
(Delivered, Return Fresh, Pickup, Heavy Soil, Damaged); driver never types it; no paper/OCR (old process
used handwriting + photo + OCR). Flow: entries → Save and Continue → Shortage Reason if short →
Generate DC → Review → Authenticate DC → select authorized hotel contact → Send OTP (to hotel
person’s registered phone, never driver) → enter OTP → accepted/verified/visit completed/immutable/hotel
gets copy. Cannot proceed to next hotel until authenticated. Wrong OTP → retry; repeated failure or
refusal → contact team; alternate authorized contact allowed. Regenerated DC → new OTP required; old OTP
invalid. Remarks entered before Generate DC.
**Trip / Visit** (`Pushpak — Trip Management`): Trip = multiple hotels on route; Visit = work at one hotel.
Start Trip only when exiting warehouse (button disabled elsewhere). Trips available same morning;
warehouse refresh recommended, not mandatory. Visiting order fully flexible; skip → Pending (not completed).
Start Visit only inside 2-meter hotel perimeter (outside = fully blocked; inside = auto-available, no refresh).
Regular Orders in use (Daily/Weekly/Monthly, multiple per Visit mostly possible); Ad Hoc not in use.
Hotel closed/inaccessible/no network → contact team. End Trip only after ALL hotels completed;
pending hotel blocks it. Pending pickup does NOT block Trip completion (handling after completion unspecified).
Reassignment mostly not allowed.
**Pickup / Heavy Soil** (`Pushpak — Pickup and Heavy Soil`): Pickup = used linen collected; enter actual
quantity (need not match delivery, e.g. delivered 500 → pickup 480; Remarks optional). Heavy Soil =
stain/dirt needing special treatment (curry, blood, dried/set-in, grease, heavy dirt/grime). Damaged =
physical/structural (torn, ripped, hole, ripped in half, severe deterioration); fading alone is NOT Damaged.
One unit with both → log under both separately → 2 photos (1 + 1). Photos: 1 flagged unit = 1 photo
(5 damaged = 5 photos); mandatory; captured at hotel before leaving perimeter; no post-exit upload; app
blocks progression; retake via X button; format auto. `Heavy Soil/Damaged qty ≤ pickup qty`. Segregation by
driver at hotel. Hotel dispute → contact team. Remarks optional.

## 3. Open questions → must answer “Not specified in the provided documents.”
- OTP validity duration; OTP resend allowed; OTP resend limit; offline/no-network DC behavior;
  admin override for DC authentication; how the hotel receives the Digital DC copy.
  (Source: Digital DC and OTP, § Open Questions, p9.)
- Complete list of delivery linen types (only “mostly same/common types” stated). (Delivery Items, §17, p10.)
- Return Fresh scope confirmation; warehouse-shortage handling; mixed-shortage exact workflow
  (packed 480 / hotel wants 450 / expected 500, reason Other). (Delivery Items, §17, p10.)
- Exact behavior for every multiple-Regular-Order scenario; pending-pickup handling after Trip completion;
  Trip reassignment exceptions; internal Trip assignment method. (Trip Management, pp5–7.)

## 4. Data (Phase 1 output)
- `data/chunks.jsonl`: 174 chunks, one per heading/subheading via `src/ingest.py` (PyMuPDF, font-size
  headings, page numbers kept; text-layout tables normalised to Markdown). Delivery 42 / DC-OTP 41 /
  Pickup 42 / Trip 49. Schema: `{chunk_id, doc, module, section, page, page_end, text, tags, type, last_updated}`.
- `eval/golden_qa.jsonl`: 42 questions covering all modules + edge cases + refusals; each cites doc/section/page.
