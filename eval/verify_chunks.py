#!/usr/bin/env python3
"""Validate data/chunks.jsonl against eval/golden_qa.jsonl.
Hard: dup ids, missing fields, invisible chars, bad pages, doc counts,
      golden citations that don't resolve (doc+section) or page mismatch.
Soft: short/long chunks, refusals not on open_question chunks,
      open_question chunks not covered by any refusal question.
Exit 1 on hard failures."""
from __future__ import annotations
import json, statistics, sys, unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {"Pushpak — Delivery Items": 42, "Pushpak — Digital DC and OTP": 41,
            "Pushpak — Pickup and Heavy Soil": 42, "Pushpak — Trip Management": 49}
REQUIRED = {"chunk_id", "stable_id", "doc", "module", "section", "page", "page_end",
            "text", "tags", "type", "retrievable", "last_updated"}
hard, soft = [], []


def load(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def invisible(s: str) -> set[str]:
    return {ch for ch in s if unicodedata.category(ch) in {"Cc", "Cf"} and ch not in "\n\t"}


def main() -> int:
    chunks = load(ROOT / "data" / "chunks.jsonl")
    golden = load(ROOT / "eval" / "golden_qa.jsonl") if (ROOT / "eval" / "golden_qa.jsonl").exists() else []

    ids = [c.get("chunk_id") for c in chunks]
    if len(ids) != len(set(ids)):
        hard.append("duplicate chunk_ids")
    sids = [c.get("stable_id") for c in chunks]
    if len(sids) != len(set(sids)):
        hard.append("duplicate stable_ids")
    counts: dict[str, int] = {}
    for c in chunks:
        cid = c.get("chunk_id", "?")
        if missing := REQUIRED - set(c):
            hard.append(f"{cid}: missing {sorted(missing)}")
        for f in ("section", "text"):
            if bad := invisible(str(c.get(f, ""))):
                hard.append(f"{cid}: invisible chars {sorted(map(repr, bad))} in {f}")
        p, pe = c.get("page"), c.get("page_end")
        if not (isinstance(p, int) and isinstance(pe, int) and 1 <= p <= pe):
            hard.append(f"{cid}: bad page range {p!r}-{pe!r}")
        if len(c.get("text", "")) < 40:
            soft.append(f"{cid}: short ({len(c['text'])} ch): {c['section'][:60]}")
        if len(c.get("text", "")) > 2500:
            soft.append(f"{cid}: long ({len(c['text'])} ch)")
        counts[c["doc"]] = counts.get(c["doc"], 0) + 1
    for doc, n in EXPECTED.items():
        if counts.get(doc) != n:
            hard.append(f"count: {doc} = {counts.get(doc)}, expected {n}")
    sizes = [len(c["text"]) for c in chunks]
    print(f"chunks={len(chunks)} chars min/med/max = {min(sizes)}/{int(statistics.median(sizes))}/{max(sizes)}")

    by_key = {(c["doc"].strip(), c["section"].strip()): c for c in chunks}

    def resolve(doc: str, section: str):
        return by_key.get((doc.strip(), section.strip()))

    for q in golden:
        ch = resolve(q["source_doc"], q["source_section"])
        via = "primary"
        if ch is None:
            for alt in q.get("acceptable_sources", []):
                ch = resolve(alt["doc"], alt["section"])
                if ch is not None:
                    via = f"acceptable ({alt['doc']} / {alt['section']})"
                    break
        if ch is None:
            hard.append(f"{q['id']}: citation not found: {q['source_doc']} / '{q['source_section']}'")
            continue
        expect_page = None
        if via == "primary":
            expect_page = q["source_page"]
        else:
            for alt in q.get("acceptable_sources", []):
                if (alt["doc"].strip(), alt["section"].strip()) == (ch["doc"].strip(), ch["section"].strip()):
                    expect_page = alt.get("page", ch["page"])
                    break
        if expect_page is not None and expect_page != ch["page"]:
            hard.append(f"{q['id']}: page mismatch golden={expect_page} chunk={ch['page']} ({ch['chunk_id']})")
        if q.get("category") == "refusal" and ch["type"] != "open_question":
            soft.append(f"{q['id']}: refusal cites type={ch['type']} ({ch['chunk_id']})")

    ref_keys = {(q["source_doc"].strip(), q["source_section"].strip())
                for q in golden if q.get("category") == "refusal"}
    cited_keys = {(q["source_doc"].strip(), q["source_section"].strip()) for q in golden}
    for alt in [a for q in golden for a in q.get("acceptable_sources", [])]:
        cited_keys.add((alt["doc"].strip(), alt["section"].strip()))
    print(f"golden={len(golden)} refusals={len(ref_keys)} open_question chunks="
          f"{sum(c['type'] == 'open_question' for c in chunks)}")
    for c in chunks:
        if c["type"] == "open_question" and (c["doc"].strip(), c["section"].strip()) not in cited_keys:
            soft.append(f"open_question not covered: {c['chunk_id']} {c['doc']} / {c['section']}")

    for title, items in (("HARD FAILURES", hard), ("WARNINGS", soft)):
        if items:
            print(f"\n== {title} ({len(items)}) ==")
            [print("  -", it) for it in items]
    if hard:
        print("\nRESULT: FAIL"); return 1
    print(f"\nRESULT: PASS" + (f" ({len(soft)} warnings)" if soft else "")); return 0


if __name__ == "__main__":
    sys.exit(main())
