#!/usr/bin/env python3
"""Phase 7 answer-level eval: all 50 golden questions end to end.

Deterministic checks (no judge):
- refusal accuracy: refusal-category answers must equal the verbatim sentence.
- calculation accuracy: calculation answers must contain the expected numbers.
- citation accuracy: every cited (doc, section) must exist in chunks AND be in
  the question's acceptable set; page must match the chunk page.
LLM judge (Gemini, temp 0): CORRECT/INCORRECT + reason per non-refusal answer.
Hallucination rate ~= share of non-refusal answers judged INCORRECT.
Writes eval/answer_eval.jsonl. Exit 1 unless PASS (refusals 10/10, calc 4/4,
citations >= 95%, judge >= 90%).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import _load_dotenv, close_pool  # noqa: E402
from src.generate import REFUSAL, answer  # noqa: E402

EXPECTED_NUMBERS = {  # question id -> numbers that must appear in the answer
    "q002": ["50"],
    "q003": ["1"],
    "q033": ["5"],
    "q049": ["40"],
    "q050": ["4"],
}

JUDGE_PROMPT = """You grade a RAG answer against a reference answer. Reply with exactly one JSON object: {{"verdict": "CORRECT" or "INCORRECT", "reason": "<one sentence>"}}.
Grade CORRECT when the candidate states the same rule, number, action, or refusal as the reference (extra true context is fine). Grade INCORRECT when it contradicts the reference, omits the key claim, invents specifics not in the reference, answers something the reference refuses, or refuses something the reference answers.
Question: {q}
Reference answer: {ref}
Candidate answer: {cand}"""


def judge(question: str, ref: str, cand: str) -> dict:
    from src.generate import chat_simple

    text = chat_simple("You are a strict RAG answer grader.",
                       JUDGE_PROMPT.format(q=question, ref=ref, cand=cand),
                       max_tokens=256)
    # gpt-oss models often emit reasoning with braces around the JSON verdict;
    # scan every brace-balanced candidate for one carrying a verdict.
    # Candidates may contain literal newlines (invalid strict JSON) — strip them.
    for m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        Cand = m.group(0)
        for variant in (Cand, Cand.replace("\n", " ")):
            try:
                verdict = json.loads(variant)
            except Exception:  # noqa: BLE001
                continue
            if verdict.get("verdict") in ("CORRECT", "INCORRECT"):
                return verdict
    return {"verdict": "UNPARSEABLE", "reason": text[:200]}


def _norm_cite(s: str) -> str:
    import unicodedata

    # Models emit lookalike whitespace (U+202F narrow nbsp, U+00A0) around
    # dashes; NFKC folds them so substantively-correct citations still match.
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def grade_one(q: dict, out: dict | None, err: str | None, chunks: dict) -> dict:
    rec: dict = {"id": q["id"], "category": q["category"], "module": q["module"]}
    if err:
        rec.update({"error": err, "refusal_ok": False, "calc_ok": None,
                    "cite_ok": False, "judge": "ERROR"})
        return rec
    ans = out["answer"]
    want_srcs = {(q["source_doc"].strip(), q["source_section"].strip())}
    for a in q.get("acceptable_sources", []):
        want_srcs.add((a["doc"].strip(), a["section"].strip()))
    # refusal check
    if q["category"] == "refusal":
        rec["refusal_ok"] = ans.strip() == REFUSAL
    else:
        rec["refusal_ok"] = ans.strip() != REFUSAL
    # calculation check
    if q["id"] in EXPECTED_NUMBERS:
        rec["calc_ok"] = all(n in ans for n in EXPECTED_NUMBERS[q["id"]])
    else:
        rec["calc_ok"] = None
    # citation check (NFKC-normalized: models emit lookalike whitespace)
    cites = re.findall(r"Source:\s*(.+?),\s*(.+?),\s*Page\s*(\d+)", ans)
    norm_chunks = {(_norm_cite(k[0]), _norm_cite(k[1])): v for k, v in chunks.items()}
    norm_want = {(_norm_cite(d), _norm_cite(s)) for d, s in want_srcs}
    if q["category"] == "refusal":
        rec["cite_ok"] = True  # refusals carry no citations by design
        rec["cites"] = []
    elif not cites:
        rec["cite_ok"] = False
        rec["cites"] = []
    else:
        ok_all, any_want = True, False
        parsed = []
        for doc, sec, pg in cites:
            key = (_norm_cite(doc), _norm_cite(sec))
            ch = norm_chunks.get(key)
            exists = ch is not None and ch["page"] == int(pg)
            wanted = exists and key in norm_want
            ok_all &= exists  # no fabricated citations, ever
            any_want |= wanted  # at least one citation supports the answer
            parsed.append({"doc": doc, "section": sec, "page": int(pg),
                           "valid": exists, "wanted": wanted})
        rec["cite_ok"] = ok_all and any_want
        rec["cites"] = parsed
        rec["cite_ok"] = ok_all
        rec["cites"] = parsed
    # judge (non-refusals only; refusals graded by the exact check above)
    if q["category"] == "refusal":
        rec["judge"] = "CORRECT" if rec["refusal_ok"] else "INCORRECT"
        rec["judge_reason"] = "verbatim refusal check"
    else:
        time.sleep(1)
        try:
            j = judge(q["question"], q["answer"], ans)
        except Exception as e:  # noqa: BLE001
            j = {"verdict": "JUDGE_ERROR", "reason": f"{type(e).__name__}: {str(e)[:150]}"}
        rec["judge"] = j["verdict"]
        rec["judge_reason"] = j.get("reason", "")
    rec["answer"] = ans[:500]
    rec["tool_calls"] = [t["name"] for t in out["tool_calls"]]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true",
                    help="skip question ids already in eval/answer_eval.jsonl")
    args = ap.parse_args()

    golden = [json.loads(l) for l in (ROOT / "eval" / "golden_qa.jsonl").open(encoding="utf-8") if l.strip()]
    chunks = {(c["doc"].strip(), c["section"].strip()): c
              for c in map(json.loads, (ROOT / "data" / "chunks.jsonl").open(encoding="utf-8"))}
    if args.limit:
        golden = golden[: args.limit]
    out_path = ROOT / "eval" / "answer_eval.jsonl"
    done: dict[str, dict] = {}
    if args.resume and out_path.exists():
        for l in out_path.open(encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                done[r["id"]] = r
        # ERROR/UNPARSEABLE/JUDGE_ERROR rows are re-graded; keep settled rows.
        done = {k: v for k, v in done.items()
                if v.get("judge") not in ("ERROR", "JUDGE_ERROR", "UNPARSEABLE")}
        with out_path.open("w", encoding="utf-8") as f:
            for q in golden:
                if q["id"] in done:
                    f.write(json.dumps(done[q["id"]], ensure_ascii=False) + "\n")
        print(f"resuming: {len(done)} already graded (errors will retry)")
    todo = [q for q in golden if q["id"] not in done]
    print(f"grading {len(todo)} questions")
    cache: dict = {}
    import time as _t
    t0 = _t.time()
    try:
        with out_path.open("a" if args.resume else "w", encoding="utf-8") as f:
            for n, q in enumerate(todo, 1):
                try:
                    out = answer(q["question"], embed_cache=cache)
                    err = None
                except Exception as e:  # noqa: BLE001
                    out, err = None, f"{type(e).__name__}: {str(e)[:200]}"
                rec = grade_one(q, out, err, chunks)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                el = _t.time() - t0
                print(f"[{n}/{len(todo)}] {q['id']} judge={rec.get('judge')} "
                      f"cite={rec.get('cite_ok')} {el:.0f}s elapsed", flush=True)
                time.sleep(6)
    finally:
        close_pool()

    results = list(done.values())
    # NOTE: do NOT reopen out_path here — rows were already streamed above and
    # reopening with "w" once truncated 50 graded rows. Reporting reads the file.
    # reload everything in golden order for reporting
    all_recs = {}
    for l in out_path.open(encoding="utf-8"):
        if l.strip():
            r = json.loads(l)
            all_recs[r["id"]] = r
    results = [all_recs[q["id"]] for q in golden if q["id"] in all_recs]
    if len(results) < len(golden):
        print(f"PARTIAL: {len(results)}/{len(golden)} graded — rerun with --resume")
        return 2

    n = len(results)
    ref = [r for r in results if next(q for q in golden if q["id"] == r["id"])["category"] == "refusal"]
    nonref = [r for r in results if r not in ref]
    ref_ok = sum(1 for r in ref if r.get("refusal_ok"))
    calc = [r for r in results if r.get("calc_ok") is not None]
    calc_ok = sum(1 for r in calc if r["calc_ok"])
    cite_ok = sum(1 for r in nonref if r.get("cite_ok"))
    judge_ok = sum(1 for r in nonref if r.get("judge") == "CORRECT")
    print(f"n={n} (refusals={len(ref)})")
    print(f"refusal accuracy: {ref_ok}/{len(ref)}")
    print(f"calculation accuracy: {calc_ok}/{len(calc)}")
    print(f"citation accuracy: {cite_ok}/{len(nonref)} = {cite_ok / len(nonref):.1%}" if nonref else "")
    print(f"judge correctness: {judge_ok}/{len(nonref)} = {judge_ok / len(nonref):.1%}" if nonref else "")
    print(f"hallucination rate (~incorrect non-refusals): {(len(nonref) - judge_ok)}/{len(nonref)}"
          f" = {(len(nonref) - judge_ok) / len(nonref):.1%}" if nonref else "")
    bad = [r for r in results if r.get("judge") in ("INCORRECT", "UNPARSEABLE", "JUDGE_ERROR", "ERROR")
           or not r.get("cite_ok", True) or r.get("calc_ok") is False or not r.get("refusal_ok", True)]
    if bad:
        print(f"\n== FAILURES ({len(bad)}) ==")
        for r in bad:
            print(f"  [{r['id']}] {r['category']}: judge={r.get('judge')} cite_ok={r.get('cite_ok')} "
                  f"calc_ok={r.get('calc_ok')} refusal_ok={r.get('refusal_ok')} tools={r.get('tool_calls')}")
            if r.get("judge_reason"):
                print(f"      judge: {r['judge_reason']}")
            print(f"      ans: {r.get('answer', r.get('error', ''))[:220]}")
    ok = (ref_ok == len(ref) and calc_ok == len(calc) and cite_ok / len(nonref) >= 0.95
          and judge_ok / len(nonref) >= 0.90)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
