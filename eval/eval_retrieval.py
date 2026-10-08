#!/usr/bin/env python3
"""Phase 3c retrieval eval (G2 gate).

50 golden questions -> retrieve top-5 -> hit = any (doc, section) in
{primary + acceptable_sources}. Reports recall@5 overall / per category /
per module, rank-of-first-hit, refusals n/10, and a miss log with the full
top-5 + parsed tsquery per miss. Query embeddings cached in-run (quota +
determinism). Exit 1 unless G2 passes (recall >= 95%, refusals 10/10).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import close_pool  # noqa: E402
from src.retrieve import retrieve  # noqa: E402


def sources_of(q: dict) -> set[tuple[str, str]]:
    srcs = {(q["source_doc"].strip(), q["source_section"].strip())}
    for a in q.get("acceptable_sources", []):
        srcs.add((a["doc"].strip(), a["section"].strip()))
    return srcs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true", help="rescore fused candidates with Jev steered noul")
    args = ap.parse_args()

    golden = [json.loads(l) for l in (ROOT / "eval" / "golden_qa.jsonl").open(encoding="utf-8") if l.strip()]
    cache: dict = {}
    rows, misses = [], []
    print(f"mode: {'RRF+Jev' if args.rerank else 'RRF only'}")
    for q in golden:
        try:
            out = retrieve(q["question"], top_k=5, embed_cache=cache, rerank=args.rerank)
        except Exception as e:  # noqa: BLE001
            misses.append((q, f"RETRIEVE ERROR: {type(e).__name__}: {e}", [], ""))
            continue
        want = sources_of(q)
        rank = None
        for i, r in enumerate(out["results"], start=1):
            if (r["doc"].strip(), r["section"].strip()) in want:
                rank = i
                break
        rows.append((q, rank, out))
        if rank is None:
            misses.append((q, None, out["results"], out["debug"]["parsed_tsquery"]))
        time.sleep(1)  # quota-friendly pacing

    total, hits = len(rows), sum(1 for _, r, _ in rows if r is not None)
    recall = hits / total if total else 0.0
    print(f"recall@5: {hits}/{total} = {recall:.1%}")
    for dim in ("category", "module"):
        groups: dict[str, list] = {}
        for q, r, _ in rows:
            groups.setdefault(q[dim], []).append(r)
        for name in sorted(groups):
            rs = groups[name]
            h = sum(1 for r in rs if r is not None)
            print(f"  {dim} {name}: {h}/{len(rs)} = {h / len(rs):.1%}")
    ranked = sorted(r for _, r, _ in rows if r is not None)
    dist = Counter(ranked)
    print("rank-of-first-hit:", dict(sorted(dist.items())))
    refusals = [(q, r) for q, r, _ in rows if q["category"] == "refusal"]
    rh = sum(1 for _, r in refusals if r is not None)
    print(f"refusals: {rh}/{len(refusals)}")

    if misses:
        print(f"\n== MISS LOG ({len(misses)}) ==")
        for q, err, top5, tsq in misses:
            print(f"\n[{q['id']}] {q['question']}  ({q['module']} / {q['category']})")
            print(f"  want: {q['source_doc']} / {q['source_section']}")
            for a in q.get("acceptable_sources", []):
                print(f"    alt: {a['doc']} / {a['section']}")
            if err:
                print(f"  {err}")
                continue
            print(f"  tsquery: {tsq}")
            for i, r in enumerate(top5, start=1):
                print(f"  {i}. [{r['doc'].replace('Pushpak — ', '')} p{r['page']}] "
                      f"{r['section'][:75]} (vec={r['vec_rank']} kw={r['kw_rank']})")

    g2 = recall >= 0.95 and rh == len(refusals) and len(refusals) > 0
    print(f"\nG2 recall>=95%: {'YES' if recall >= 0.95 else 'NO'}  refusals 10/10: {'YES' if rh == len(refusals) else 'NO'}")
    print("RESULT:", "PASS" if g2 else "FAIL")
    close_pool()
    return 0 if g2 else 1


if __name__ == "__main__":
    sys.exit(main())
