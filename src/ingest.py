"""Pushpak RAG — Phase 1 ingestion: PDF -> section chunks -> chunks.jsonl.

- Parses the 4 Pushpak PDFs with PyMuPDF, keeping page numbers.
- Chunks by heading (font-size based), NOT fixed token size: one chunk per
  heading/subheading. FAQ questions (size 13) each become their own chunk.
- Tables in these docs are text-layout tables (no bordered tables detectable
  via find_tables), so table content is preserved as text and normalised to
  Markdown where recognised (Return Fresh by Product, Trip vs Visit).
- Invisible Unicode chars (U+200B ZWSP etc. embedded by the PDFs) are stripped
  from headings and body so section strings, BM25 terms, and cache keys compare
  exactly.
- Output: data/chunks.jsonl with metadata {chunk_id, stable_id, doc, module,
  section, page, page_end, text, tags, type, retrievable, last_updated}.
  chunk_id is positional (c000…); stable_id is a sha1 content hash for caching.
  type == "metadata" chunks are marked retrievable: false.

Usage:
    python3 src/ingest.py
    python3 src/ingest.py --check   # print summary only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "chunks.jsonl"

DOCS = [
    ("Pushpak — Delivery Items.pdf", "Pushpak — Delivery Items", "Delivery Items"),
    ("Pushpak — Digital DC and OTP.pdf", "Pushpak — Digital DC and OTP", "Digital DC and OTP"),
    ("Pushpak — Pickup and Heavy Soil.pdf", "Pushpak — Pickup and Heavy Soil", "Pickup and Heavy Soil"),
    ("Pushpak — Trip Management.pdf", "Pushpak — Trip Management", "Trip Management"),
]

LAST_UPDATED = "2026-10-07"  # stated in all 4 docs

HEADING_SIZE_THRESHOLD = 13.0

# --- P0 fix: strip invisible chars everywhere -------------------------------
# The PDFs embed U+200B (ZWSP) after list numbers, values, colons, and "?".
# str.strip() and regex \s do NOT remove it (category Cf), so it must be
# stripped explicitly or section strings / BM25 terms / cache keys mismatch.
INVISIBLE = "\u200b\u200c\u200d\u200e\u200f\u00ad\ufeff"
_INV = str.maketrans("", "", INVISIBLE)


def strip_invisible(s: str) -> str:
    return s.translate(_INV)


def stable_hash(doc: str, section: str, page: int, text: str) -> str:
    h = hashlib.sha1(f"{doc}\n{section}\n{page}\n{text}".encode("utf-8")).hexdigest()
    return h[:12]


def _is_title(doc_title: str, page_no: int, text: str, size: float, seen_title: bool) -> bool:
    return page_no == 1 and not seen_title and text.strip() == doc_title.strip()


def extract_headed_blocks(pdf_path: Path, doc_title: str):
    """Return list of (heading, body_lines, start_page, end_page) in order."""
    doc = pymupdf.open(pdf_path)
    # Linear stream of (page_no, size, text) per visual line
    stream: list[tuple[int, float, str]] = []
    for pi in range(len(doc)):
        page_no = pi + 1
        d = doc[pi].get_text("dict")
        for b in d["blocks"]:
            for line in b.get("lines", []):
                txt = "".join(s["text"] for s in line["spans"]).strip()
                if not txt:
                    continue
                txt = strip_invisible(txt)
                if not txt:
                    continue
                size = round(line["spans"][0]["size"], 1)
                stream.append((page_no, size, txt))
    doc.close()

    # Group into heading-led blocks. First title line is skipped.
    # Only consecutive headings of the SAME font size with no body between
    # are merged (multi-line headings like "9. Return Fresh and Shortage
    # Reason Are" + "Different", both size 23). A size-23 section followed
    # by a size-17/13 subsection stays separate.
    blocks: list[dict] = []
    current = None
    seen_title = False

    prev_was_heading = False
    prev_heading_size: float | None = None
    for page_no, size, txt in stream:
        if _is_title(doc_title, page_no, txt, size, seen_title):
            seen_title = True
            prev_was_heading = False
            prev_heading_size = None
            continue
        is_heading = size >= HEADING_SIZE_THRESHOLD
        if is_heading:
            if prev_was_heading and current is not None and not current["lines"] and prev_heading_size == size:
                # multi-line heading fragment -> merge
                current["heading"] = re.sub(r"\s+", " ", (current["heading"] + " " + txt)).strip()
                current["end_page"] = page_no
            else:
                if current is not None and (current["lines"] or current["heading"]):
                    blocks.append(current)
                current = {"heading": re.sub(r"\s+", " ", txt).strip(), "lines": [], "start_page": page_no, "end_page": page_no,
                           "heading_size": size}
            prev_was_heading = True
            prev_heading_size = size
        else:
            # body line
            if current is None:
                # text before first heading (should not happen after title skip)
                current = {"heading": "(intro)", "lines": [], "start_page": page_no, "end_page": page_no,
                           "heading_size": 11.0}
            current["lines"].append(txt)
            current["end_page"] = page_no
            prev_was_heading = False
            prev_heading_size = None

    if current is not None:
        blocks.append(current)
    # drop empty intro blocks
    blocks = [b for b in blocks if b["heading"] != "(intro)" or b["lines"]]
    return blocks


def normalise_text(lines: list[str]) -> str:
    lines = [strip_invisible(l) for l in lines]
    text = "\n".join(lines)
    text = text.replace("●\u200b", "●").replace("●", "- ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalise known text-layout tables to Markdown for downstream use.
    if "Bedsheet" in text and "Pillow Cover" in text and "Return Fresh" in text:
        text += (
            "\n\n[Table: Return Fresh by Product — Markdown]\n"
            "| Item | Expected | Delivered | Return Fresh |\n"
            "|---|---|---|---|\n"
            "| Bedsheet | 500 | 450 | 50 |\n"
            "| Pillow Cover | 300 | 280 | 20 |"
        )
    if "Contains multiple hotels" in text and "Represents one hotel" in text:
        text += (
            "\n\n[Table: Trip vs Visit — Markdown]\n"
            "| Trip | Visit |\n"
            "|---|---|\n"
            "| Contains multiple hotels | Represents one hotel |\n"
            "| Covers the complete route | Covers work at one hotel |\n"
            "| Started when exiting the warehouse | Started at the hotel |\n"
            "| Contains multiple visits | Contains one hotel's delivery/pickup work |\n"
            "| Ends after all hotels are completed | Completed after the hotel's required process is completed |"
        )
    return text.strip()


TAG_RULES = [
    # One tag per keyword group: a tag fires only on its own keywords, so
    # "linen"/"hotel"/"warehouse"/"perimeter" no longer drag in unrelated tags.
    (["return fresh"], ["return fresh"]),
    (["delivery"], ["deliver", "delivery", "expected"]),
    (["shortage"], ["shortage"]),
    (["shortage reason"], ["shortage reason"]),
    (["otp"], ["otp"]),
    (["authentication"], ["authenticat"]),
    (["dc"], [" dc", "dc ", "dc?", "challan", "generate dc"]),
    (["digital dc"], ["digital dc"]),
    (["heavy soil"], ["heavy soil"]),
    (["stain"], ["stain", "curry", "blood", "grease", "soil", "grime", "dirt"]),
    (["damaged"], ["damaged", "torn", "ripped", "hole", "tear", "deteriorat", "transit"]),
    (["photo"], ["photo", "picture", "camera"]),
    (["pickup"], ["pickup", "collect"]),
    (["trip"], ["trip"]),
    (["visit"], ["visit"]),
    (["hotel"], ["hotel"]),
    (["warehouse"], ["warehouse"]),
    (["start trip"], ["start trip"]),
    (["start visit"], ["start visit"]),
    (["perimeter"], ["perimeter", "2-meter", "2 meter"]),
    (["end trip"], ["end trip", "end the trip", "trip completion", "trip is considered complete"]),
    (["remarks"], ["remark"]),
    (["regular order"], ["regular order"]),
    (["ad hoc"], ["ad hoc"]),
    (["linen"], ["linen"]),
]


def infer_tags(heading: str, text: str) -> list[str]:
    hay = strip_invisible(heading + " " + text).lower()
    tags: list[str] = []
    for tag_names, keywords in TAG_RULES:
        if any(k in hay for k in keywords):
            tags.extend(tag_names)
    # dedupe, keep order
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:10]


FAQ_PREFIX_RE = re.compile(r"^\d+\.\s*faq\s*[—–-]\s*", re.IGNORECASE)
QUESTION_WORDS = ("what ", "how ", "can ", "do ", "does ", "is ", "are ",
                  "why ", "who ", "when ", "where ", "which ", "should ")


def infer_type(heading: str, text: str) -> str:
    h = strip_invisible(heading).lower()
    q = FAQ_PREFIX_RE.sub("", h)          # "16. FAQ — Can ...?" -> "can ...?"
    tl = strip_invisible(text).lower()
    if ("open question" in h or "____" in text or "to be provided" in tl
            or "not further specified" in tl or "not currently specified" in tl):
        return "open_question"
    if h == "faq" or "common questions" in h:
        return "faq"
    if q.startswith(QUESTION_WORDS):
        return "faq"
    if "example" in h:
        return "example"
    if any(k in h for k in ["definition", "key terms", "what is", " means"]):
        return "definition"
    if any(k in h for k in ["important rules", "critical rules", "validation", "cannot", "must", "required",
                             "prevent", "block", "rules"]):
        return "rule"
    if any(k in h for k in ["flow", "steps", "process", "generation", "authentication", "visit",
                             "completion", "procedure"]):
        return "process"
    if "metadata" in h:
        return "metadata"
    return "info"


def split_delivery_faq(blocks: list[dict]) -> list[dict]:
    """Split Delivery Items '16. FAQ' body (Q: ... / A lines, size 11) into per-question blocks
    so FAQ granularity matches the other 3 docs (where questions are size-13 headings)."""
    out: list[dict] = []
    for b in blocks:
        if b["heading"] == "16. FAQ" and any(l.startswith("Q:") for l in b["lines"]):
            cur_q = None
            cur_lines: list[str] = []
            for line in b["lines"]:
                if line.startswith("Q:"):
                    if cur_q is not None:
                        out.append({"heading": f"16. FAQ — {cur_q}", "lines": cur_lines,
                                    "start_page": b["start_page"], "end_page": b["end_page"],
                                    "heading_size": 13.0})
                    cur_q = strip_invisible(line[2:]).strip()
                    cur_lines = []
                else:
                    cur_lines.append(line)
            if cur_q is not None:
                out.append({"heading": f"16. FAQ — {cur_q}", "lines": cur_lines,
                            "start_page": b["start_page"], "end_page": b["end_page"],
                            "heading_size": 13.0})
        else:
            out.append(b)
    return out


def build_chunks() -> list[dict]:
    all_chunks: list[dict] = []
    for fname, doc_title, module in DOCS:
        pdf_path = DATA_DIR / fname
        blocks = extract_headed_blocks(pdf_path, doc_title)
        blocks = split_delivery_faq(blocks)
        # For Delivery Items: prefix non-numbered subsections with their parent
        # numbered section so citations stay unambiguous after empty parents
        # ("5.", "13.", "15.", "16.", "17.") are dropped.
        # e.g. "Before Save and Continue" -> "13. Return Fresh Editing Rules — Before Save and Continue".
        last_numbered = None
        for b in blocks:
            if re.match(r"^\d+\.", b["heading"]):
                last_numbered = b["heading"]
            elif doc_title.endswith("Delivery Items") and last_numbered and not b["heading"].startswith(last_numbered):
                b["heading"] = f"{last_numbered} — {b['heading']}"
        for b in blocks:
            text = normalise_text(b["lines"])
            if not text:
                continue
            ctype = infer_type(b["heading"], text)
            all_chunks.append(
                {
                    "doc": doc_title,
                    "module": module,
                    "section": b["heading"],
                    "page": b["start_page"],
                    "page_end": b["end_page"],
                    "text": text,
                    "tags": infer_tags(b["heading"], text),
                    "type": ctype,
                    "retrievable": ctype != "metadata",
                    "last_updated": LAST_UPDATED,
                }
            )
    # assign positional ids (stable across runs given same input order) plus
    # content-hash stable_ids for caching/retrieval references.
    for i, c in enumerate(all_chunks):
        c["chunk_id"] = f"c{i:03d}"
        c["stable_id"] = stable_hash(c["doc"], c["section"], c["page"], c["text"])
    # order keys for readability
    ordered = []
    for c in all_chunks:
        ordered.append(
            {
                "chunk_id": c["chunk_id"],
                "stable_id": c["stable_id"],
                "doc": c["doc"],
                "module": c["module"],
                "section": c["section"],
                "page": c["page"],
                "page_end": c["page_end"],
                "text": c["text"],
                "tags": c["tags"],
                "type": c["type"],
                "retrievable": c["retrievable"],
                "last_updated": c["last_updated"],
            }
        )
    return ordered


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="print summary without writing")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    chunks = build_chunks()
    print(f"docs=4 chunks={len(chunks)}")
    by_doc: dict[str, int] = {}
    for c in chunks:
        by_doc[c["doc"]] = by_doc.get(c["doc"], 0) + 1
    for k, v in by_doc.items():
        print(f"  {k}: {v} chunks")
    if args.check:
        for c in chunks[:5]:
            print(f"  {c['chunk_id']} [{c['doc']} p{c['page']}] {c['section'][:70]} ({c['type']})")
        return
    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
