"""Phase 5 answer generation: Gemini + rules-engine function calling.

- Model: GEN_MODEL env (default gemini-2.5-flash, verified on this key), temp 0.
- Context = retrieve() top-5 (RRF+Jev combo). Strict system prompt (SPEC §1):
  context-only answers, verbatim refusal, mechanism-refusal rule (q045),
  confusion-pair guards, function mandate for ALL math, citation format.
- Tool loop (max 3): functionCall -> src.rules.call_function -> functionResponse.
- Safety net: if the model omits citations, the retrieved sources are appended
  (marked) so no answer ever ships sourceless.
"""
from __future__ import annotations

import json
import os
import time

import requests

from src.db import _load_dotenv
from src.retrieve import retrieve
from src.rules import FUNCTION_DECLARATIONS, call_function

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
REFUSAL = "Not specified in the provided documents."

SYSTEM_PROMPT = """You are the Pushpak operations assistant. Answer ONLY from the provided context chunks.
- If the answer is not in the context, reply with EXACTLY this sentence and nothing else: Not specified in the provided documents.
- If the question asks HOW, by what means, or for a procedure/validity/limit (e.g. how the hotel receives the DC, resend steps, OTP validity, offline behavior) and the context states the fact but NOT the mechanism, reply with EXACTLY that sentence. A related fact is not a mechanism.
- Never confuse: Delivery (fresh linen to hotel) with Pickup (used linen from hotel); Return Fresh (a quantity) with Shortage Reason (the reason); Heavy Soil (stain/dirt) with Damaged (physical damage); fresh-linen transit damage with used-linen pickup damage; a pending hotel (BLOCKS End Trip) with a pending pickup (does NOT block End Trip).
- For ANY quantity or check (Return Fresh, photo counts, over-delivery, trip completion, edit windows) you MUST call the provided function and use its exact result. Never do arithmetic or logic in prose.
- End every answered (non-refusal) response with citation lines: Source: <document name>, <section>, Page <page>. Use the exact document/section/page headers from the context. Maximum 2 sources."""

SYSTEM_PROMPT_NO_TOOLS = SYSTEM_PROMPT.replace(
    "- For ANY quantity or check (Return Fresh, photo counts, over-delivery, trip completion, edit windows) you MUST call the provided function and use its exact result. Never do arithmetic or logic in prose.",
    "- No functions are available. Do any arithmetic carefully by hand from the context numbers and state each step.",
)

MAX_TOOL_ROUNDS = 3


def _gen_model() -> str:
    _load_dotenv()
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    if provider == "groq":
        return os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
    return os.environ.get("GEN_MODEL", "gemini-2.5-flash").strip()


def _provider() -> str:
    _load_dotenv()
    return os.environ.get("LLM_PROVIDER", "gemini").strip().lower()


def _groq_client():
    from openai import OpenAI

    _load_dotenv()
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set (env or .env)")
    base = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()
    return OpenAI(api_key=key, base_url=base)


def _openai_tools() -> list[dict]:
    return [
        {"type": "function", "function": {k: v for k, v in d.items()}}
        for d in FUNCTION_DECLARATIONS
    ]


def _generate_groq(messages: list[dict], model: str, tools: list[dict] | None = None) -> dict:
    """One Groq chat call, optionally with tools. Returns simplified {text, calls}."""
    client = _groq_client()
    last = None
    for attempt in range(5):
        try:
            kwargs: dict = dict(model=model, messages=messages, temperature=0.0, max_tokens=512)
            if tools is not None:
                kwargs["tools"] = tools
            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            calls = [
                {"name": tc.function.name, "args": json.loads(tc.function.arguments or "{}")}
                for tc in (msg.tool_calls or [])
            ]
            return {"text": msg.content or "", "calls": calls}
        except Exception as e:  # noqa: BLE001
            last = e
            s = str(e)
            if "429" in s or "500" in s or "503" in s:
                wait = min(10 * (2 ** attempt), 120)
                print(f"    groq {type(e).__name__}, retry {attempt + 1}/5 after {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise
    raise last


def _api_key() -> str:
    _load_dotenv()
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is not set (env or .env)")
    return key


def _context_block(results: list[dict]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[Chunk {i} | {r['doc']} | {r['section']} | Page {r['page']}]\n{r['text']}"
        )
    return "\n\n".join(parts)


def _generate(key: str, model: str, contents: list[dict], with_tools: bool = True) -> dict:
    last = None
    for attempt in range(5):
        payload: dict = {
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT if with_tools else SYSTEM_PROMPT_NO_TOOLS}]},
                "contents": contents,
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 512},
            }
        if with_tools:
            payload["tools"] = [{"function_declarations": FUNCTION_DECLARATIONS}]
        r = requests.post(
            f"{GEMINI_BASE}/models/{model}:generateContent",
            params={"key": key},
            json=payload,
            timeout=120,
        )
        if r.status_code == 200:
            return r.json()
        last = r
        if r.status_code in (429, 500, 503):
            wait = min(10 * (2 ** attempt), 120)
            print(f"    generate {r.status_code}, retry {attempt + 1}/5 after {wait}s", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
    last.raise_for_status()


def _parts(resp: dict) -> list[dict]:
    return resp["candidates"][0]["content"].get("parts", [])


def generate(question: str, retrieved: list[dict], tools_enabled: bool = True) -> dict:
    """Run the tool loop over pre-retrieved chunks. Returns answer + metadata."""
    if _provider() == "groq":
        return _generate_groq_loop(question, retrieved, tools_enabled)
    if not tools_enabled:
        key, model = _api_key(), _gen_model()
        text = "".join(
            p.get("text", "") for p in _parts(_generate(key, model, [
                {"role": "user", "parts": [
                    {"text": f"Context:\n{_context_block(retrieved)}\n\nQuestion: {question}"}]},
            ], with_tools=False))
            if "text" in p
        ).strip()
        return {"answer": _ensure_cited(_normalize_refusal(text), retrieved), "tool_calls": []}
    key, model = _api_key(), _gen_model()
    contents: list[dict] = [
        {
            "role": "user",
            "parts": [
                {"text": f"Context:\n{_context_block(retrieved)}\n\nQuestion: {question}"}
            ],
        }
    ]
    tool_calls: list[dict] = []
    for _ in range(MAX_TOOL_ROUNDS + 1):
        parts = _parts(_generate(key, model, contents))
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not calls:
            return {"answer": _ensure_cited(_normalize_refusal(text), retrieved), "tool_calls": tool_calls}
        responses = []
        for c in calls:
            name, args = c["name"], c.get("args", {})
            outcome = call_function(name, args)
            tool_calls.append({"name": name, "args": args, "outcome": outcome})
            responses.append({"functionResponse": {"name": name, "response": outcome}})
        # Echo the model's turn (text + CLEAN functionCall parts) before
        # functionResponse. Response parts carry a thoughtSignature field the
        # request schema rejects, so rebuild name/args only.
        model_parts = ([{"text": text}] if text else []) + [
            {"functionCall": {"name": c["name"], "args": c.get("args", {})}} for c in calls
        ]
        contents.append({"role": "model", "parts": model_parts})
        contents.append({"role": "function", "parts": responses})
    return {"answer": _ensure_cited(_normalize_refusal(text), retrieved), "tool_calls": tool_calls}


def _generate_groq_loop(question: str, retrieved: list[dict], tools_enabled: bool = True) -> dict:
    """Same tool loop over the Groq OpenAI-compatible endpoint."""
    model = _gen_model()
    prompt = SYSTEM_PROMPT if tools_enabled else SYSTEM_PROMPT_NO_TOOLS
    tools = _openai_tools() if tools_enabled else None
    messages: list[dict] = [
        {"role": "system", "content": prompt},
        {"role": "user",
         "content": f"Context:\n{_context_block(retrieved)}\n\nQuestion: {question}"},
    ]
    tool_calls: list[dict] = []
    text = ""
    for _ in range(MAX_TOOL_ROUNDS + 1):
        out = _generate_groq(messages, model, tools)
        text, calls = out["text"].strip(), out["calls"]
        if not calls or not tools_enabled:
            return {"answer": _ensure_cited(_normalize_refusal(text), retrieved), "tool_calls": tool_calls}
        messages.append({"role": "assistant", "content": text or None, "tool_calls": [
            {"id": f"call_{len(tool_calls) + i}", "type": "function",
             "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
            for i, c in enumerate(calls)
        ]})
        for i, c in enumerate(calls):
            outcome = call_function(c["name"], c["args"])
            tool_calls.append({"name": c["name"], "args": c["args"], "outcome": outcome})
            messages.append({"role": "tool", "tool_call_id": f"call_{len(tool_calls) - 1}",
                             "content": json.dumps(outcome)})
    return {"answer": _ensure_cited(_normalize_refusal(text), retrieved), "tool_calls": tool_calls}


def chat_simple(system: str, user: str, max_tokens: int = 256) -> str:
    """Single non-tool chat completion on the active provider (used by the judge)."""
    if _provider() == "groq":
        client = _groq_client()
        last = None
        for attempt in range(5):
            try:
                resp = client.chat.completions.create(
                    model=_gen_model(),
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0.0, max_tokens=max_tokens,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                last = e
                if "429" in str(e) or "500" in str(e) or "503" in str(e):
                    time.sleep(min(10 * (2 ** attempt), 120))
                    continue
                raise
        raise last
    import requests as _rq

    key = _api_key()
    last = None
    for attempt in range(5):
        r = _rq.post(
            f"{GEMINI_BASE}/models/{_gen_model()}:generateContent",
            params={"key": key},
            json={"system_instruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": [{"text": user}]}],
                   "generationConfig": {"temperature": 0.0, "maxOutputTokens": max_tokens}},
            timeout=120,
        )
        if r.status_code == 200:
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        last = r
        if r.status_code in (429, 500, 503):
            time.sleep(min(10 * (2 ** attempt), 120))
            continue
        r.raise_for_status()
    last.raise_for_status()


def _normalize_refusal(text: str) -> str:
    """Format-only guard: if the model decided to refuse (wrote the refusal
    sentence, optionally with a preamble like "The workflow is ...") but added
    preamble/citations, collapse to the exact contract sentence. Only fires
    when the refusal sentence is present — never invents a refusal."""
    import re as _re

    body = "\n".join(
        ln for ln in text.strip().splitlines() if not ln.strip().lower().startswith("source:")
    ).strip()
    if _re.match(
        r"^(the .{0,120}? is )?not specified in the provided documents\.?$",
        body,
        _re.IGNORECASE | _re.DOTALL,
    ):
        return REFUSAL
    return text


def _ensure_cited(text: str, retrieved: list[dict]) -> str:
    if "source" in text.lower() or text.strip() == REFUSAL:
        return text
    cites = "\n".join(
        f"Source: {r['doc']}, {r['section']}, Page {r['page']}" for r in retrieved[:2]
    )
    return f"{text}\n{cites}"


def answer(
    question: str, top_k: int = 5, rerank: bool = True, embed_cache: dict | None = None,
    use_cache: bool = True, tools_enabled: bool = True,
) -> dict:
    """Full pipeline: cache -> retrieve -> generate. Returns answer, sources, debug."""
    from src import cache as cache_mod
    from src.embed_config import EMBEDDING_DIMS, EMBEDDING_MODEL, TASK_QUERY
    from src.retrieve import embed_query

    debug_extra: dict = {"cache": "disabled"}
    qvec = None
    fp = None
    if use_cache:
        # Embed once (reused by retrieve via embed_cache) for the lookup.
        from src.db import get_pool as _get_pool

        try:
            import requests as _rq

            _load_dotenv()
            import os as _os

            gkey = _os.environ.get("GOOGLE_API_KEY", "").strip()
            r = _rq.post(
                f"https://generativelanguage.googleapis.com/v1beta/{EMBEDDING_MODEL}:embedContent",
                params={"key": gkey},
                json={"content": {"parts": [{"text": question}]}, "taskType": TASK_QUERY,
                      "outputDimensionality": EMBEDDING_DIMS},
                timeout=60,
            )
            r.raise_for_status()
            qvec = r.json()["embedding"]["values"]
            if embed_cache is not None:
                embed_cache[question] = qvec
            with _get_pool().connection() as conn:
                with conn.cursor() as cur:
                    from src.embed_config import assert_model as _assert

                    _assert(cur)
                    from src.cache import fingerprint as _fp

                    fp = _fp(cur)
            hit = cache_mod.lookup(question, qvec, fp)
            if hit is not None:
                return {
                    "answer": hit["answer"],
                    "sources": hit["sources"],
                    "tool_calls": hit.get("tool_calls", []),
                    "debug": {"cache": hit.get("_match", "hit")},
                }
            debug_extra = {"cache": "miss"}
        except Exception:  # noqa: BLE001
            debug_extra = {"cache": "lookup-error"}
    out = retrieve(question, top_k=top_k, embed_cache=embed_cache, rerank=rerank)
    gen = generate(question, out["results"], tools_enabled)
    if use_cache and qvec is not None and fp is not None:
        cache_mod.store(question, qvec, fp, {
            "answer": gen["answer"],
            "sources": [{"doc": r["doc"], "section": r["section"], "page": r["page"]}
                        for r in out["results"][:2]],
            "tool_calls": gen["tool_calls"],
            "stable_ids": [r["stable_id"] for r in out["results"]],
        })
    debug = out["debug"]
    debug.update(debug_extra)
    return {
        "answer": gen["answer"],
        "sources": [
            {"doc": r["doc"], "section": r["section"], "page": r["page"]}
            for r in out["results"][:2]
        ],
        "tool_calls": gen["tool_calls"],
        "debug": debug,
    }
