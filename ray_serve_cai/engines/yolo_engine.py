"""
YOLO Object Detection Engine for Ray Serve.

Uses Ray Serve's @serve.batch decorator to dynamically batch concurrent HTTP
requests into single GPU inference calls, amortising per-call overhead and
maximising GPU throughput for YOLO models.

Architecture
------------
  HTTP request                    Ray Serve actor
  ──────────────                  ──────────────────────────────────────────
  POST /detect        ──►  __call__  ──►  _detect_batch(single_bytes)
  POST /detect (x N)  ──►  __call__  ──►  │                         │
  POST /detect        ──►  __call__  ──►  │  @serve.batch collects  │
                                           │  up to max_batch_size   │
                                           │  or until timeout_s     │
                                           └──►  _run_inference([img0, img1, …])
                                                  └──►  return [dets0, dets1, …]

The @serve.batch wrapper is applied inside make_yolo_deployment() so that
max_batch_size and batch_wait_timeout_s can be configured per deployment at
run time — each call to make_yolo_deployment() closes over different values.

Endpoints
---------
  POST  /          — detect objects in one image (same as /detect)
  POST  /detect    — detect objects in one image
  GET   /health    — liveness probe
  GET   /info      — model metadata

Request body (JSON)
-------------------
  {"image":     "<base64-encoded image bytes>"}
  {"image_url": "https://..."}              (fetches at request time)

Response body (JSON)
--------------------
  {
    "detections": [
      {
        "class_id":   0,
        "class_name": "person",
        "confidence": 0.9312,
        "bbox_xyxy":  [100.5, 200.3, 300.7, 400.1]   // [x1, y1, x2, y2] pixels
      },
      ...
    ],
    "num_detections": 1,
    "model":          "/home/cdsw/models/yolov8n.pt",
    "image_size":     [640, 480]                       // [width, height]
  }
"""

import base64
import io
import logging
from typing import Any, Dict, List

from ray import serve
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class _YOLOBase:
    """
    Shared YOLO engine logic — model loading, inference, HTTP routing.

    Not decorated with @serve.deployment.  Subclasses add the deployment
    decorator and a @serve.batch decorated _detect_batch method with
    deployment-specific batch parameters.
    """

    def __init__(self, engine_config: Dict[str, Any]) -> None:
        from ultralytics import YOLO

        model_path = engine_config["model_path"]
        self._conf        = engine_config.get("conf_threshold", 0.25)
        self._iou         = engine_config.get("iou_threshold",  0.45)
        self._device      = engine_config.get("device", "cuda:0")
        self._model_path  = model_path

        logger.info(f"Loading YOLO model from {model_path!r} on {self._device}")
        self._model = YOLO(model_path)
        self._model.to(self._device)
        logger.info("YOLO model loaded")

    # ── Overridden by the @serve.batch decorated method in the subclass ──────

    async def _detect_batch(self, image_bytes: bytes) -> List[Dict]:
        """Subclasses replace this with a @serve.batch decorated version."""
        raise NotImplementedError

    # ── Synchronous inference (called from inside the batched method) ─────────

    def _run_inference(self, images: list) -> List[List[Dict]]:
        """
        Run YOLO inference on a batch of PIL Images.

        Called synchronously from _detect_batch.  Blocking the event loop
        during GPU inference is acceptable because each Ray Serve replica
        runs in its own process — there is no other async work competing
        in the same actor during inference.

        Returns a list of detection lists, one per input image.
        """
        results = self._model(
            images,
            conf=self._conf,
            iou=self._iou,
            device=self._device,
            verbose=False,
        )
        return [self._format_boxes(r) for r in results]

    def _format_boxes(self, result) -> List[Dict]:
        """Convert a single ultralytics Result to a list of detection dicts."""
        detections = []
        if result.boxes is None:
            return detections
        for box in result.boxes:
            detections.append({
                "class_id":   int(box.cls.item()),
                "class_name": result.names[int(box.cls.item())],
                "confidence": round(float(box.conf.item()), 4),
                "bbox_xyxy":  [round(v, 2) for v in box.xyxy[0].tolist()],
            })
        return detections

    # ── HTTP handlers ─────────────────────────────────────────────────────────

    async def _handle_detect(self, request: Request) -> JSONResponse:
        body = await request.json()
        img_bytes = _decode_image(body)

        # Single call; @serve.batch collects concurrent calls into a batch.
        detections = await self._detect_batch(img_bytes)

        # Extract image size from body if provided, else omit
        img_size = body.get("image_size")

        response = {
            "detections":     detections,
            "num_detections": len(detections),
            "model":          self._model_path,
        }
        if img_size:
            response["image_size"] = img_size

        return JSONResponse(response)

    async def _handle_health(self, request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "model": self._model_path})

    async def _handle_info(self, request: Request) -> JSONResponse:
        names = getattr(self._model, "names", {})
        return JSONResponse({
            "model":          self._model_path,
            "device":         self._device,
            "conf_threshold": self._conf,
            "iou_threshold":  self._iou,
            "classes":        names,
        })

    async def __call__(self, request: Request):
        path = request.url.path

        if request.method == "POST":
            # POST / and POST /detect both trigger detection
            return await self._handle_detect(request)

        # GET sub-paths
        if path.endswith("/health"):
            return await self._handle_health(request)
        if path.endswith("/info"):
            return await self._handle_info(request)

        return JSONResponse(
            {"error": f"Not found: {request.method} {path}"},
            status_code=404,
        )


# ── Deployment factory ────────────────────────────────────────────────────────

def make_yolo_deployment(
    max_batch_size: int = 16,
    batch_wait_timeout_s: float = 0.05,
) -> type:
    """
    Create a @serve.deployment YOLO class with configurable batch parameters.

    The @serve.batch decorator is applied inside this function so the closure
    captures max_batch_size and batch_wait_timeout_s at class-creation time.
    Calling make_yolo_deployment() twice with different values produces two
    independent deployment classes with independent batch queues.

    Args:
        max_batch_size:
            Maximum number of images Ray Serve will accumulate before calling
            _run_inference.  Higher values improve GPU utilisation at the cost
            of increased per-request latency tail.  Typical T4 sweet spot: 8–32.
        batch_wait_timeout_s:
            How long (seconds) Ray Serve waits to fill the batch before
            dispatching a partial batch.  Lower values reduce latency; higher
            values improve throughput under bursty load.

    Returns:
        A @serve.deployment decorated class ready for .options(...).bind(config).
    """

    @serve.deployment
    class YOLOEngine(_YOLOBase):
        """
        Ray Serve YOLO deployment.

        Inherits all logic from _YOLOBase; adds the @serve.batch decorated
        _detect_batch method with the batch parameters captured in the
        make_yolo_deployment() closure.
        """

        @serve.batch(
            max_batch_size=max_batch_size,
            batch_wait_timeout_s=batch_wait_timeout_s,
        )
        async def _detect_batch(self, image_bytes_list: List[bytes]) -> List[List[Dict]]:
            """
            Batched inference entry point — called by Ray Serve.

            Ray Serve collects individual image_bytes arguments from concurrent
            __call__ invocations and delivers them here as image_bytes_list.
            The return value is a list of equal length, one detection list per
            input image.

            Args:
                image_bytes_list: List of raw image bytes (JPEG/PNG/etc.)

            Returns:
                List of detection result lists, one per image.
            """
            from PIL import Image

            images = [
                Image.open(io.BytesIO(b)).convert("RGB")
                for b in image_bytes_list
            ]
            return self._run_inference(images)

    return YOLOEngine


# Default deployment class — used by the registry as the engine_class reference.
# The deployment factory calls make_yolo_deployment() with config-driven params
# so the actual deployed class may have different batch settings.
YOLOEngine = make_yolo_deployment(max_batch_size=16, batch_wait_timeout_s=0.05)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_image(body: Dict[str, Any]) -> bytes:
    """
    Decode raw image bytes from a request body.

    Supports:
      - base64 field:  {"image": "<base64>"}
      - URL field:     {"image_url": "https://..."}
    """
    if "image" in body:
        return base64.b64decode(body["image"])
    if "image_url" in body:
        import urllib.request
        with urllib.request.urlopen(body["image_url"], timeout=10) as resp:
            return resp.read()
    raise ValueError(
        "Request body must contain 'image' (base64-encoded bytes) "
        "or 'image_url' (HTTP/HTTPS URL)."
    )
