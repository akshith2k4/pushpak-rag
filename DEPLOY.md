# Deploying Pushpak Q&A (Render free tier)

Takes ~10 minutes, no credit card. The app, DB (Neon), cache (Upstash),
embeddings (Gemini), generation (Groq) and rerank (Jev) all stay on free tiers.

## 1. Push the code

```bash
git add -A && git commit -m "Deploy Pushpak Q&A" && git push origin main
```

`.env` is gitignored (verified) — secrets never touch the repo. Confirm with
`git status` that `.env` is not listed before pushing.

## 2. Create the Render service

1. Go to https://dashboard.render.com → **New +** → **Web Service**.
2. Connect the `pushpak-rag` GitHub repo.
3. Render auto-detects `render.yaml` (Blueprint). Non-secret env vars come from it.
4. Fill the `sync: false` secrets in the **Environment** tab from your local `.env`:
   - `DATABASE_URL`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `JEV_API_KEY`,
     `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`
5. **Deploy.** First build takes ~3–5 min.

## 3. Verify live

- `https://<your-service>.onrender.com/health` → `{"api":"ok","db":"ok",...}`
- Open `https://<your-service>.onrender.com/` — the chat UI. Ask:
  “Expected 500, delivered 450. Return Fresh?” → 50, cited.
- Share that URL with users. No login needed.

## 4. Free-tier realities

- The service **sleeps after ~15 min idle**; first request thereafter takes
  30–60s (Render wake + Neon cold start). This is normal on free tiers.
- Quotas that can bite: Gemini generate/day (we use Groq for generation, so
  mostly irrelevant), Groq 20–30 RPM, Jev per-call credits on rerank.
  The chat has `rerank`/`cache` toggles — turning rerank off removes Jev spend.
- Logs: Render dashboard → Logs tab. Metrics: `/health` endpoint.

## 5. Later (not now)

- Custom domain: Render dashboard → Settings → Custom Domains.
- Keep-awake pinger, rate limiting, and auth are deliberately out of scope for
  this first deploy — add when real users arrive.
