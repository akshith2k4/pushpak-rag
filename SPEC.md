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
  Pickup 42 / Trip 49. Schema: `{chunk_id, stable_id, doc, module, section, page, page_end, text, tags,
  type, retrievable, last_updated}`. `chunk_id` is positional; `stable_id` is a sha1 content hash —
  caching (Phase 6) must key on `stable_id`. `type == "metadata"` chunks have `retrievable: false`.
- `eval/golden_qa.jsonl`: 50 questions covering all modules + edge cases + refusals; each cites doc/section/page.
- `eval/verify_chunks.py`: automated Phase-1 gate (dup ids, required fields, invisible-char scan, page ranges,
  per-doc counts, golden citation resolution incl. `acceptable_sources`, refusal/open-question coverage).
  Current status: PASS, 0 hard failures (29 warnings: 27 short FAQ answer chunks + 2 uncovered open-question
  chunks c040, c138 — both documented gaps, see below).

## 5. Chunking-review patch notes
- **Invisible chars (P0):** PDFs embed U+200B (ZWSP) after list numbers, values, colons, `?`. Stripped in
  `extract_headed_blocks`, `normalise_text`, `split_delivery_faq` so section strings/BM25/cache keys compare exactly.
- **Citation policy:** several facts live in 2+ chunks (e.g. no-edit-after-OTP = Delivery §13 + DC
  "Editing After OTP Authentication" + DC "Important Rules" + DC FAQ). Golden items carry
  `acceptable_sources: [{doc, section, page}]`; the grader/verifier accepts the primary citation OR any
  acceptable source. 14 of 50 items have alternates.
- **Offline / DC-copy boundary (spec decision):** Trip doc "no network → contact the team" (q027) is a
  *driver-action* rule — answerable. DC doc "Offline/no-network behavior" (q046) asks about *app behavior* —
  refusal. Likewise "hotel receives a copy" (q018, yes) vs "how the hotel receives it" (q045, refusal).
  q045/q046 pair deliberately against q018/q027 as confusion tests.
- **Tags:** one tag per keyword group (no bundling); `hotel`/`warehouse`/`perimeter`/`linen` no longer drag in
  `trip`/`start trip`/`start visit`/`return fresh`. Tags are hints only, never module filters alone.
- **Retrieval notes (Phase 3):** exclude `retrievable == false`; embed `f"{doc} — {section}\n{text}"` so tiny
  FAQ chunks (<40 chars, e.g. "Yes.") carry context; cite section as-is. Hybrid keyword list:
  Return Fresh, Heavy Soil, OTP, Digital DC, Start Visit, End Trip, Shortage Reason.
  Keyword channel uses OR semantics (AND zeroes refusal chunks) with hyphens sanitized;
  parsed tsquery is logged per question. Query params cast `%s::vector`.
- **Rerank (locked):** fused top-15 rescored by Jev steered noul (`src/rerank.py`, V2 criteria:
  true = "contains the specific fact/rule/quantity, or explicitly marks it unspecified",
  false = "shares words/topics but neither"). Generic relevance scoring was measured to
  bury refusal sources (q045 #7→#13) and is banned here. Final order: normalized
  combo `0.5*rrf + 0.5*jev` (pure-Jev sort fixed q045 but broke q049's table chunk;
  the combo keeps both). No threshold discard (uncalibrated). Requires `JEV_API_KEY`.
- **G2 results (eval/eval_retrieval.py):** RRF-only 49/50 (q045 miss) → RRF+Jev combo
  **50/50 = 100%, refusals 10/10**, rank-of-first-hit {1:35, 2:9, 3:4, 4:1, 5:1} —
  strictly better than either signal alone (RRF-only rank-1: 32; pure-Jev rank-1: 28).

## 6. Phase 4/5 (rules engine + generation + /ask)
- `src/rules.py`: pure functions (`calculate_return_fresh`, `validate_delivery`,
  `validate_pickup`, `check_photo_requirements`, `can_end_trip`,
  `pending_pickup_blocks_trip() -> False`, `can_edit_dc`) + `FUNCTION_DECLARATIONS`
  + `call_function` dispatcher. `eval/test_rules.py`: 10/10 green.
- `src/generate.py`: `GEN_MODEL` (default gemini-2.5-flash, verified on key), temp 0,
  strict prompt (context-only, verbatim refusal, mechanism-refusal rule, confusion
  guards, function mandate, `Source:` citations), tool loop (max 3 rounds) with
  citation safety net. `POST /ask {question, rerank, top_k}` wired in `src/api.py`.
- Learnings: REST generate URL needs `models/` prefix; echoed functionCall parts
  must be rebuilt name/args-only (response `thoughtSignature` is rejected);
  retry-with-backoff on 429/503 in `_generate`.
- Smoke test 10/10 (calculations via tools, refusals exact incl. q045, citations
  present); `/ask` verified over HTTP (500/499 → tool result 1, correct + cited).

## 7. Phase 7 (answer-level eval, Groq gpt-oss→qwen — first full run)
- `eval/evaluate.py`: deterministic checks (verbatim refusal, expected numbers,
  NFKC-normalized citation match) + Gemini→Groq judge (CORRECT/INCORRECT).
  Gemini generate quota exhausted mid-build; generation moved to Groq
  (`LLM_PROVIDER`, `qwen/qwen3.8-27b`, OpenAI-compatible tools).
- Measured (50 q, from run.log verdict lines): judge **48/50** — q024 is a documented
  judge false-negative (answer correct per Trip/End-Trip chunks), q042 was a refusal
  *format* deviation (semantically correct). Refusals verbatim 9/10 → **10/10 after
  the `_normalize_refusal` format guard** (verified live on q042 re-query).
  Calculations judge-backed 5/5. Citations 15/40 under the old all-pre-listed rule —
  checker artifact (sampled failures = valid primary + real unlisted secondary, zero
  fabricated citations seen); rule fixed to exists-in-chunks + ≥1-acceptable.
- Harness fixes: file-truncation bug (reporting block reopened JSONL with "w",
  wiping 50 rows — metrics reconstructed from run.log), patient retries,
  incremental writes, `--resume` (retries ERROR/JUDGE_ERROR/UNPARSEABLE),
  brace-tolerant judge-JSON parse, `.format` brace-escape bug.
- **Known gaps (accepted):** c040 Delivery-shortage operational detail (partially answerable, no dedicated
  question) and c138 multiple-Regular-Order edge behavior have no golden coverage; internal Trip assignment
  method likewise untested.

## 8. Phase 6 (semantic cache, live on Upstash)
- `src/cache.py`: Upstash REST backend (exact sha1 key + 200-entry index scanned
  with local cosine). First token was read-restricted (SET→NOPERM, fail-closed);
  replacement token verified full read/write.
- Verified: cold miss 12.6s → store; exact repeat **2.4s identical**; unseen
  paraphrase **semantic hit 0.957 in 5.3s**; TTL ≈ 86400s; fingerprint mismatch
  → miss (re-ingest safe). Threshold calibrated 0.95 → **0.85** on a measured
  true paraphrase at 0.877. Wired into `answer()`/`/ask` (`use_cache` flag).
