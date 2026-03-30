#!/usr/bin/env python3
"""
YOLO Detection UI — CAI Application.

A single-file FastAPI application that serves a browser-based UI for
uploading images and visualising YOLO detection results from a Ray Serve
deployment.

Routes:
    GET  /          — Single-page detection UI
    POST /api/detect — Proxy: forwards uploaded image to the YOLO Ray Serve
                        deployment and returns the DetectionResult JSON

Environment Variables:
    CDSW_APP_PORT   CAI-injected port (default: 8100)
    YOLO_API_URL    Full base URL of the YOLO Ray Serve deployment
                    (default: http://localhost:8000/yolo)

Usage (local):
    python demo_configs/yolo_detection_ui.py

Usage (CAI Application):
    Set script to demo_configs/yolo_detection_ui.py — the app reads
    CDSW_APP_PORT automatically.
"""

import io
import os
import subprocess
import sys

import requests as http_requests
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

# ── Config ───────────────────────────────────────────────────────────────────

YOLO_API_URL = os.getenv(
    "YOLO_API_URL",
    "http://localhost:8000/yolo",
).rstrip("/")

APP_PORT = int(os.getenv("CDSW_APP_PORT", "8100"))

# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="YOLO Detection UI", docs_url=None, redoc_url=None)

# ── HTML page (inline) ──────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>YOLO Object Detection</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --border: #2e3147;
    --accent: #4f8ef7;
    --text: #e2e8f0;
    --muted: #8892a4;
    --danger: #ef4444;
    --success: #22c55e;
    --warn: #f59e0b;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', system-ui, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  header {
    padding: 18px 32px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--surface);
  }
  header svg { flex-shrink: 0; }
  header h1 { font-size: 1.15rem; font-weight: 600; }
  header span { font-size: 0.8rem; color: var(--muted); margin-left: auto; }

  main {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 340px;
    gap: 0;
    overflow: hidden;
  }

  /* ── Canvas panel ── */
  .canvas-panel {
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    overflow: auto;
  }

  #drop-zone {
    border: 2px dashed var(--border);
    border-radius: 12px;
    min-height: 220px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    cursor: pointer;
    transition: border-color .2s, background .2s;
    color: var(--muted);
    font-size: 0.9rem;
    position: relative;
  }
  #drop-zone.drag-over { border-color: var(--accent); background: rgba(79,142,247,.06); }
  #drop-zone.has-image { border-color: transparent; padding: 0; overflow: hidden; }
  #file-input { display: none; }

  #canvas-wrapper {
    position: relative;
    display: inline-block;
    width: 100%;
  }
  canvas { display: block; width: 100%; height: auto; border-radius: 10px; }

  .upload-icon { opacity: .5; }

  /* ── Loading overlay ── */
  #loading-overlay {
    display: none;
    position: absolute;
    inset: 0;
    background: rgba(15,17,23,.75);
    border-radius: 10px;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    font-size: .9rem;
    color: var(--text);
  }
  #loading-overlay.visible { display: flex; }

  .spinner {
    width: 40px; height: 40px;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Detect button ── */
  #detect-btn {
    align-self: flex-start;
    padding: 10px 24px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: .9rem;
    font-weight: 600;
    cursor: pointer;
    transition: opacity .15s, transform .1s;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  #detect-btn:disabled { opacity: .45; cursor: not-allowed; }
  #detect-btn:not(:disabled):hover { opacity: .88; }
  #detect-btn:not(:disabled):active { transform: scale(.97); }

  /* ── Results panel ── */
  .results-panel {
    background: var(--surface);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .results-panel h2 {
    padding: 18px 20px 14px;
    font-size: .85rem;
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }

  #results-content {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .empty-state {
    color: var(--muted);
    font-size: .85rem;
    text-align: center;
    margin-top: 32px;
  }

  /* summary bar */
  .summary-bar {
    background: var(--bg);
    border-radius: 8px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .summary-row {
    display: flex;
    justify-content: space-between;
    font-size: .82rem;
  }
  .summary-row .label { color: var(--muted); }
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: .75rem;
    font-weight: 600;
  }
  .badge.green { background: rgba(34,197,94,.15); color: var(--success); }
  .badge.red   { background: rgba(239,68,68,.15);  color: var(--danger); }
  .badge.blue  { background: rgba(79,142,247,.15); color: var(--accent); }

  /* detection item card */
  .det-card {
    background: var(--bg);
    border-radius: 8px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 5px;
    border-left: 3px solid var(--accent);
  }
  .det-header { display: flex; align-items: center; gap: 8px; }
  .det-swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
  .det-name { font-size: .88rem; font-weight: 600; }
  .det-conf { margin-left: auto; font-size: .8rem; color: var(--muted); }
  .det-location { font-size: .75rem; color: var(--muted); }

  /* error */
  .error-box {
    background: rgba(239,68,68,.08);
    border: 1px solid rgba(239,68,68,.25);
    border-radius: 8px;
    padding: 12px;
    font-size: .82rem;
    color: var(--danger);
  }

  @media (max-width: 768px) {
    main { grid-template-columns: 1fr; }
    .results-panel { border-left: none; border-top: 1px solid var(--border); }
  }
</style>
</head>
<body>

<header>
  <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
       stroke="var(--accent)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="3" width="18" height="18" rx="2"/>
    <circle cx="9" cy="9" r="2"/><circle cx="15" cy="15" r="2"/>
    <path d="M9 15h6M15 9H9"/>
  </svg>
  <h1>YOLO Object Detection</h1>
  <span id="api-status">API: <b id="api-url-label"></b></span>
</header>

<main>
  <section class="canvas-panel">

    <div id="drop-zone" tabindex="0" role="button"
         aria-label="Upload image for detection">
      <svg class="upload-icon" width="48" height="48" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
      </svg>
      <p>Drag &amp; drop an image here</p>
      <p style="font-size:.78rem">or click to browse</p>
      <input type="file" id="file-input" accept="image/*"/>

      <div id="canvas-wrapper" style="display:none; width:100%;">
        <canvas id="detection-canvas"></canvas>
        <div id="loading-overlay">
          <div class="spinner"></div>
          <span>Running detection...</span>
        </div>
      </div>
    </div>

    <button id="detect-btn" disabled>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
      Detect Objects
    </button>

  </section>

  <aside class="results-panel">
    <h2>Detection Results</h2>
    <div id="results-content">
      <p class="empty-state">Upload an image and click<br>"Detect Objects" to start.</p>
    </div>
  </aside>
</main>

<script>
// ── Class colours ────────────────────────────────────────────────────────────
const CLASS_COLORS = {};
const PALETTE = [
  '#4f8ef7','#f97316','#22c55e','#a855f7','#ef4444',
  '#14b8a6','#eab308','#ec4899','#06b6d4','#f43f5e',
];
function classColor(name) {
  if (!CLASS_COLORS[name]) {
    const idx = Object.keys(CLASS_COLORS).length % PALETTE.length;
    CLASS_COLORS[name] = PALETTE[idx];
  }
  return CLASS_COLORS[name];
}

// ── State ────────────────────────────────────────────────────────────────────
let currentFile = null;
let currentImage = null;

// ── DOM refs ─────────────────────────────────────────────────────────────────
const dropZone       = document.getElementById('drop-zone');
const fileInput      = document.getElementById('file-input');
const detectBtn      = document.getElementById('detect-btn');
const canvas         = document.getElementById('detection-canvas');
const ctx            = canvas.getContext('2d');
const canvasWrapper  = document.getElementById('canvas-wrapper');
const loadingOverlay = document.getElementById('loading-overlay');
const resultsContent = document.getElementById('results-content');

document.getElementById('api-url-label').textContent =
  window.location.host + '/api/detect';

// ── Upload handling ──────────────────────────────────────────────────────────
dropZone.addEventListener('click', e => {
  if (e.target === canvas || canvasWrapper.contains(e.target)) return;
  fileInput.click();
});
dropZone.addEventListener('keydown', e => { if (e.key === 'Enter') fileInput.click(); });

dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) loadFile(file);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) loadFile(fileInput.files[0]);
});

function loadFile(file) {
  if (!file.type.startsWith('image/')) { alert('Please upload an image file.'); return; }
  currentFile = file;
  const reader = new FileReader();
  reader.onload = e => {
    const img = new Image();
    img.onload = () => {
      currentImage = img;
      showImage(img);
      detectBtn.disabled = false;
      resultsContent.innerHTML =
        '<p class="empty-state">Click "Detect Objects"<br>to run inference.</p>';
    };
    img.src = e.target.result;
  };
  reader.readAsDataURL(file);
}

function showImage(img) {
  canvas.width  = img.naturalWidth;
  canvas.height = img.naturalHeight;
  ctx.drawImage(img, 0, 0);
  Array.from(dropZone.children).forEach(el => {
    if (el !== canvasWrapper) el.style.display = 'none';
  });
  canvasWrapper.style.display = 'block';
  dropZone.classList.add('has-image');
}

// ── Detection ────────────────────────────────────────────────────────────────
detectBtn.addEventListener('click', runDetection);

async function runDetection() {
  if (!currentFile) return;
  detectBtn.disabled = true;
  loadingOverlay.classList.add('visible');
  resultsContent.innerHTML = '';
  ctx.drawImage(currentImage, 0, 0);

  try {
    const form = new FormData();
    form.append('file', currentFile, currentFile.name);

    const resp = await fetch('/api/detect', { method: 'POST', body: form });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    const result = await resp.json();
    drawBoxes(result.items);
    renderResults(result);
  } catch (err) {
    renderError(err.message);
  } finally {
    loadingOverlay.classList.remove('visible');
    detectBtn.disabled = false;
  }
}

// ── Canvas drawing ───────────────────────────────────────────────────────────
function drawBoxes(items) {
  if (!items || !items.length) return;
  const W = canvas.width, H = canvas.height;
  const scale = Math.max(1, Math.round(W / 600));

  items.forEach(item => {
    if (!item.bbox || item.bbox.length < 4) return;

    // bbox is normalised [cx, cy, w, h]
    const [cx_n, cy_n, w_n, h_n] = item.bbox;
    const x1 = (cx_n - w_n / 2) * W;
    const y1 = (cy_n - h_n / 2) * H;
    const bw = w_n * W;
    const bh = h_n * H;
    const color = classColor(item.name);

    // box
    ctx.strokeStyle = color;
    ctx.lineWidth   = scale * 2;
    ctx.strokeRect(x1, y1, bw, bh);

    // label
    const fontSize = scale * 13;
    ctx.font = `bold ${fontSize}px Inter, system-ui, sans-serif`;
    const label = `${item.name} ${(item.confidence * 100).toFixed(0)}%`;
    const textW = ctx.measureText(label).width;
    const padX = scale * 5, padY = scale * 4;
    const labelH = fontSize + padY * 2;
    const labelY = y1 > labelH ? y1 - labelH : y1 + bh;

    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(x1 - scale, labelY, textW + padX * 2 + scale, labelH, 3);
    ctx.fill();

    ctx.fillStyle = '#fff';
    ctx.fillText(label, x1 + padX - scale, labelY + labelH - padY - 1);
  });
}

// ── Results sidebar ──────────────────────────────────────────────────────────
function renderError(msg) {
  resultsContent.innerHTML =
    `<div class="error-box"><b>Detection failed</b><br>${escHtml(msg)}</div>`;
}

function renderResults(result) {
  const items = result.items || [];
  if (!items.length) {
    resultsContent.innerHTML = '<p class="empty-state">No objects detected.</p>';
    return;
  }

  const counts = {};
  items.forEach(i => { counts[i.name] = (counts[i.name] || 0) + 1; });

  let html = `
  <div class="summary-bar">
    <div class="summary-row">
      <span class="label">Total detections</span>
      <span class="badge blue">${result.total_count}</span>
    </div>
    <div class="summary-row">
      <span class="label">Concealment flag</span>
      ${result.has_concealed_items
        ? '<span class="badge red">WARNING</span>'
        : '<span class="badge green">Clear</span>'}
    </div>
  </div>`;

  // per-class summary chips
  html += '<div style="display:flex;flex-wrap:wrap;gap:6px;padding:4px 0;">';
  Object.entries(counts).forEach(([name, count]) => {
    const color = classColor(name);
    html += `<span style="background:${color}22;color:${color};
      border-radius:999px;padding:3px 10px;font-size:.75rem;font-weight:600;">
      ${escHtml(name)} &times;${count}</span>`;
  });
  html += '</div>';

  // individual detections
  items.forEach(item => {
    const color = classColor(item.name);
    const confPct = (item.confidence * 100).toFixed(1);
    html += `
    <div class="det-card" style="border-left-color:${color}">
      <div class="det-header">
        <div class="det-swatch" style="background:${color}"></div>
        <span class="det-name">${escHtml(item.name)}</span>
        <span class="det-conf">${confPct}%</span>
      </div>
      <div class="det-location">Location: ${escHtml(item.location || '\u2014')}</div>
    </div>`;
  });

  resultsContent.innerHTML = html;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>
"""

# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=HTML)


@app.post("/api/detect")
async def detect(file: UploadFile = File(...)):
    """Proxy the uploaded image to the YOLO Ray Serve deployment."""
    image_bytes = await file.read()

    try:
        resp = http_requests.post(
            f"{YOLO_API_URL}/v1/detect",
            files={"file": (file.filename, io.BytesIO(image_bytes), file.content_type)},
            timeout=60,
        )
        resp.raise_for_status()
    except http_requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot reach YOLO API at {YOLO_API_URL}. Check YOLO_API_URL env var.",
        )
    except http_requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="YOLO API timed out (>60 s).")
    except http_requests.exceptions.HTTPError:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"YOLO API error {resp.status_code}: {resp.text[:300]}",
        )

    return JSONResponse(content=resp.json())


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("YOLO Detection UI")
    print("=" * 60)
    print(f"  Port:     {APP_PORT}")
    print(f"  YOLO API: {YOLO_API_URL}")
    print(f"  UI:       http://127.0.0.1:{APP_PORT}/")
    print()

    if os.getenv("_UI_SERVER_MODE"):
        uvicorn.run(app, host="127.0.0.1", port=APP_PORT)
    else:
        # Running in the CAI/Jupyter parent process which already owns an
        # event loop.  Spawn a fresh subprocess to host uvicorn.
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        except NameError:
            project_root = os.getcwd()
        ui_script = os.path.join(project_root, "demo_configs", "yolo_detection_ui.py")
        cmd = [sys.executable, ui_script]
        env = {**os.environ, "_UI_SERVER_MODE": "1"}
        print(f"Spawning UI server subprocess: {' '.join(cmd)}")
        print()
        try:
            process = subprocess.Popen(
                cmd,
                stdout=sys.stdout,
                stderr=sys.stderr,
                cwd=project_root,
                env=env,
            )
            exit_code = process.wait()
            if exit_code != 0:
                print(f"\nERROR: UI server exited with code {exit_code}")
                sys.exit(exit_code)
        except KeyboardInterrupt:
            print("\n\nShutting down UI server...")
            if "process" in locals():
                process.terminate()
                process.wait()
            sys.exit(0)
        except Exception as exc:
            print(f"\nERROR: Failed to start UI server: {exc}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
