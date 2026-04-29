"""
YOLO Object Detection Engine for Ray Serve.

Uses Ray Serve's @serve.batch decorator to dynamically batch concurrent HTTP
requests into single GPU inference calls, amortising per-call overhead and
maximising GPU throughput for YOLO models.

Architecture
------------
  HTTP request                    Ray Serve actor
  ──────────────                  ──────────────────────────────────────────
  POST /v1/detect     ──►  FastAPI  ──►  _detect_batch(single_bytes)
  POST /v1/detect (N) ──►  FastAPI  ──►  │                         │
  POST /v1/detect     ──►  FastAPI  ──►  │  @serve.batch collects  │
                                          │  up to max_batch_size   │
                                          │  or until timeout_s     │
                                          └──►  _run_inference([img0, img1, …])
                                                 └──►  return [result0, result1, …]

Swagger UI is available at <route_prefix>/docs (e.g. /yolo/docs).

Endpoints
---------
  POST  /v1/detect  — detect objects in an uploaded image (multipart file)
  GET   /health     — liveness probe
  GET   /info       — model metadata

Request (multipart/form-data)
-----------------------------
  file: image file (JPEG, PNG, etc.)

Response (JSON) — matches DetectionResult schema
-------------------------------------------------
  {
    "items": [
      {
        "name":       "Knife",
        "confidence": 0.93,
        "location":   "upper-left",
        "bbox":       [0.15, 0.22, 0.10, 0.18]   // [cx, cy, w, h] normalised
      }
    ],
    "total_count":        1,
    "has_concealed_items": false
  }
"""

import io
import logging
from typing import Any, Dict, List

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from ray import serve
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — spatial location & occlusion
# ---------------------------------------------------------------------------

def _bbox_to_location(bbox_normalised: List[float]) -> str:
    """
    Convert normalised bounding box to a human-readable location string.

    Args:
        bbox_normalised: [x_center, y_center, width, height] in [0, 1].

    Returns:
        Location like "upper-left", "center", "lower-right", etc.
    """
    cx, cy = bbox_normalised[0], bbox_normalised[1]

    if cx < 0.33:
        h = "left"
    elif cx > 0.67:
        h = "right"
    else:
        h = "center"

    if cy < 0.33:
        v = "upper"
    elif cy > 0.67:
        v = "lower"
    else:
        v = "center"

    if v == "center" and h == "center":
        return "center"
    if v == "center":
        return h
    if h == "center":
        return v
    return f"{v}-{h}"


def _check_occlusion(boxes: List[List[float]], threshold: float = 0.3) -> bool:
    """
    Return True if any pair of normalised boxes overlap above *threshold* IoU.

    Args:
        boxes: List of [x_center, y_center, width, height] normalised.
        threshold: IoU above which overlap counts as concealment.
    """
    n = len(boxes)
    if n < 2:
        return False

    for i in range(n):
        cx1, cy1, w1, h1 = boxes[i]
        ax1, ay1 = cx1 - w1 / 2, cy1 - h1 / 2
        ax2, ay2 = cx1 + w1 / 2, cy1 + h1 / 2
        a_area = w1 * h1

        for j in range(i + 1, n):
            cx2, cy2, w2, h2 = boxes[j]
            bx1, by1 = cx2 - w2 / 2, cy2 - h2 / 2
            bx2, by2 = cx2 + w2 / 2, cy2 + h2 / 2

            ix1 = max(ax1, bx1)
            iy1 = max(ay1, by1)
            ix2 = min(ax2, bx2)
            iy2 = min(ay2, by2)

            if ix2 <= ix1 or iy2 <= iy1:
                continue

            inter = (ix2 - ix1) * (iy2 - iy1)
            union = a_area + w2 * h2 - inter
            if union > 0 and inter / union > threshold:
                return True

    return False


# ---------------------------------------------------------------------------
# ASGI middleware — strips route_prefix from scope["path"] before routing.
# Plain class (not FastAPI subclass) to avoid Ray serialization issues.
# ---------------------------------------------------------------------------

class _RoutePathMiddleware:
    """Strip ASGI root_path prefix from scope path before FastAPI routing."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            root_path: str = scope.get("root_path", "")
            path: str = scope.get("path", "")
            if root_path and path.startswith(root_path):
                remainder = path[len(root_path):]
                if remainder == "" or remainder.startswith("/"):
                    scope = dict(scope)
                    scope["path"] = remainder or "/"
        await self.app(scope, receive, send)


_yolo_app = FastAPI(
    title="YOLO Object Detection API",
    description=(
        "Object detection API powered by YOLO and Ray Serve.\n\n"
        "Supports `/v1/detect` (multipart file upload), `/health`, and `/info`."
    ),
    version="1.0.0",
    root_path_in_servers=True,
    openapi_tags=[
        {"name": "Detection", "description": "Object detection endpoints"},
        {"name": "Health",    "description": "Liveness probe"},
        {"name": "Info",      "description": "Model metadata"},
    ],
)
_yolo_app.add_middleware(_RoutePathMiddleware)


# ---------------------------------------------------------------------------
# Base class — model loading, inference, FastAPI route handlers
# ---------------------------------------------------------------------------

class _YOLOBase:
    """
    Shared YOLO engine logic — model loading, inference, endpoints.

    Not decorated with @serve.deployment.  Subclasses add the deployment
    decorator and a @serve.batch decorated _detect_batch method with
    deployment-specific batch parameters.
    """

    def __init__(self, engine_config: Dict[str, Any]) -> None:
        from ultralytics import YOLO

        model_path = engine_config["model_path"]
        self._conf       = engine_config.get("conf_threshold", 0.25)
        self._iou        = engine_config.get("iou_threshold",  0.45)
        self._device     = engine_config.get("device", "cuda:0")
        self._model_path = model_path

        logger.info("Loading YOLO model from %r on %s", model_path, self._device)
        self._model = YOLO(model_path)
        self._model.to(self._device)
        logger.info("YOLO model loaded — classes: %s", self._model.names)

        # ── Ray Prometheus metrics ───────────────────────────────────────
        # Uses ray.util.metrics — auto-exported on Ray's metrics port (9090)
        # alongside system metrics.  No prometheus_client dependency needed.
        from ray.util.metrics import Counter, Histogram
        self._m_requests = Counter(
            "yolo_requests_total",
            description="Total detection requests",
            tag_keys=("status",),
        )
        self._m_inference = Histogram(
            "yolo_inference_seconds",
            description="Inference latency per image (seconds)",
            boundaries=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )
        self._m_batch_size = Histogram(
            "yolo_batch_size",
            description="Images per batch",
            boundaries=[1, 2, 4, 8, 16, 32],
        )
        self._m_detections = Histogram(
            "yolo_detections_per_image",
            description="Detected items per image",
            boundaries=[0.5, 1, 2, 5, 10, 20, 50],
        )
        logger.info("YOLO Ray metrics initialized")

    # ── Overridden by the @serve.batch decorated method in the subclass ──

    async def _detect_batch(self, image_bytes: bytes) -> Dict[str, Any]:
        """Subclasses replace this with a @serve.batch decorated version."""
        raise NotImplementedError

    # ── Synchronous inference (called from inside the batched method) ────

    def _run_inference(self, images: list) -> List[Dict[str, Any]]:
        """
        Run YOLO inference on a batch of PIL Images.

        Returns a list of DetectionResult dicts, one per input image.
        """
        import time

        self._m_batch_size.observe(len(images))

        t0 = time.perf_counter()
        results = self._model(
            images,
            conf=self._conf,
            iou=self._iou,
            device=self._device,
            verbose=False,
        )
        elapsed = time.perf_counter() - t0

        built = [self._build_result(r) for r in results]

        per_image = elapsed / max(len(images), 1)
        for b in built:
            self._m_inference.observe(per_image)
            self._m_detections.observe(b["total_count"])

        return built

    def _build_result(self, result) -> Dict[str, Any]:
        """Convert a single ultralytics Result to a DetectionResult dict."""
        items: List[Dict[str, Any]] = []
        bboxes: List[List[float]] = []

        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls.item())
                conf = round(float(box.conf.item()), 4)
                bbox_norm = [round(v, 4) for v in box.xywhn[0].tolist()]

                items.append({
                    "name":       result.names[class_id],
                    "confidence": conf,
                    "location":   _bbox_to_location(bbox_norm),
                    "bbox":       bbox_norm,
                })
                bboxes.append(bbox_norm)

        return {
            "items":               items,
            "total_count":         len(items),
            "has_concealed_items": _check_occlusion(bboxes),
        }

    # ── FastAPI endpoints ────────────────────────────────────────────────

    @_yolo_app.post("/v1/detect", tags=["Detection"],
                    summary="Detect objects in an uploaded image",
                    response_model=None)
    async def detect(self, file: UploadFile = File(...)):
        img_bytes = await file.read()
        try:
            result = await self._detect_batch(img_bytes)
            self._m_requests.inc(tags={"status": "ok"})
            return JSONResponse(result)
        except Exception:
            self._m_requests.inc(tags={"status": "error"})
            raise

    @_yolo_app.get("/health", tags=["Health"],
                   summary="Liveness probe")
    async def health_check(self):
        return {
            "status": "healthy",
            "model":  self._model_path,
        }

    @_yolo_app.get("/info", tags=["Info"],
                   summary="Model metadata")
    async def model_info(self):
        names = getattr(self._model, "names", {})
        return {
            "model":          self._model_path,
            "device":         self._device,
            "conf_threshold": self._conf,
            "iou_threshold":  self._iou,
            "classes":        names,
        }


# ---------------------------------------------------------------------------
# Deployment factory
# ---------------------------------------------------------------------------

def make_yolo_deployment(
    max_batch_size: int = 16,
    batch_wait_timeout_s: float = 0.05,
) -> type:
    """
    Create a @serve.deployment YOLO class with configurable batch parameters.

    The @serve.batch decorator is applied inside this function so the closure
    captures max_batch_size and batch_wait_timeout_s at class-creation time.
    """

    @serve.deployment
    @serve.ingress(_yolo_app)
    class YOLOEngine(_YOLOBase):
        """Ray Serve YOLO deployment with dynamic batching and Swagger UI."""

        @serve.batch(
            max_batch_size=max_batch_size,
            batch_wait_timeout_s=batch_wait_timeout_s,
        )
        async def _detect_batch(
            self, image_bytes_list: List[bytes]
        ) -> List[Dict[str, Any]]:
            """
            Batched inference entry point — called by Ray Serve.

            Ray Serve collects individual image_bytes from concurrent
            detect() invocations and delivers them here as image_bytes_list.
            Returns a list of DetectionResult dicts, one per input image.
            """
            from PIL import Image

            images = [
                Image.open(io.BytesIO(b)).convert("RGB")
                for b in image_bytes_list
            ]
            return self._run_inference(images)

    return YOLOEngine


# Default deployment class — used by the registry as engine_class reference.
YOLOEngine = make_yolo_deployment(max_batch_size=16, batch_wait_timeout_s=0.05)
