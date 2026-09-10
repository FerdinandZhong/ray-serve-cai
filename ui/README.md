# Model Chat UI

A tiny, dependency-free web UI for chatting with a model deployed on the Ray
Serve endpoint (OpenAI-compatible `/v1/chat/completions`).

## Files

- `chat.html` — the entire UI (HTML + CSS + JS in one file). Streaming, model
  picker, system prompt, temperature / max-tokens / top-p, per-turn metrics
  (TTFT, tok/s), and a collapsible reasoning panel for `<think>` / `reasoning_content`.
- `serve.py` — a zero-dependency (stdlib only) dev server that serves the page
  **and** proxies API calls to the cluster, sidestepping CORS.

## Quick start

```bash
cd ui
python3 serve.py            # serves on http://localhost:8080
# then open http://localhost:8080/ in a browser
```

Point at a different cluster / port:

```bash
python3 serve.py --target https://other-host --port 9000
# or:  TARGET=https://other-host PORT=9000 python3 serve.py
```

In the browser: keep **Base URL** as the proxy origin (auto-filled), set the
**route prefix** (e.g. `qwen3-35b`), click **↻ load** to fetch the model list,
then chat. Settings persist in `localStorage`.

## Why the proxy?

The deployed vLLM app does **not** send CORS headers, so a browser loading
`chat.html` directly from `file://` (or any other origin) is blocked from
calling the endpoint. `serve.py` makes the browser talk only to
`localhost` (same origin) and forwards `/<prefix>/v1/*` to the cluster —
no CORS, no server-side redeploy. It streams responses (chunked transfer),
so SSE token-by-token output works.

If CORS is ever enabled on the vLLM app (add `CORSMiddleware` to the ingress
app), you can skip the proxy: open `chat.html` directly and set **Base URL**
to the cluster URL.

## Default endpoint

Defaults target the current deployment:

- Base URL (direct / `--target`): `https://ray-cluster-head.ml-e8a34ebc-1ec.qzhong-1.a465-9q4k.cloudera.site`
- Prefix: `qwen3-35b`
