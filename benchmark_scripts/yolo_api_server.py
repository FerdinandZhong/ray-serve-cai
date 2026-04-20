#!/usr/bin/env python3
"""
YOLO-based X-ray Detection API Server with OpenAI-compatible endpoints.

Supports both native Ultralytics YOLO (.pt) and ONNX Runtime inference.
Returns structured JSON matching the existing output_schema.json format.
"""

import argparse
import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import io

from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn
from PIL import Image
import numpy as np


# Try importing Ultralytics YOLO
try:
    from ultralytics import YOLO
    HAS_ULTRALYTICS = True
except ImportError:
    HAS_ULTRALYTICS = False
    # Don't print warning on import - will warn only if Ultralytics backend is requested

# Try importing ONNX Runtime (optional, only needed for .onnx models)
try:
    import onnxruntime as ort
    HAS_ONNX = True
except ImportError:
    HAS_ONNX = False
    # Don't print warning on import - will warn only if ONNX backend is requested


class ItemDetection(BaseModel):
    """Single detected item with metadata."""
    name: str = Field(..., description="Item category name")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence score")
    location: str = Field(..., description="Spatial location descriptor")
    bbox: Optional[List[float]] = Field(None, description="Bounding box [x_center, y_center, width, height] normalized")


class DetectionResult(BaseModel):
    """Complete detection result matching output_schema.json."""
    items: List[ItemDetection] = Field(default_factory=list, description="List of detected items")
    total_count: int = Field(..., ge=0, description="Total number of items detected")
    has_concealed_items: bool = Field(..., description="Whether items are partially hidden")


class ChatMessage(BaseModel):
    """OpenAI chat message format."""
    role: str = "assistant"
    content: str


class ChatChoice(BaseModel):
    """OpenAI chat completion choice."""
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""
    id: str = "chatcmpl-yolo-detection"
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatChoice]


def bbox_to_location(bbox_normalized: List[float]) -> str:
    """
    Convert normalized bounding box to location string.
    
    Args:
        bbox_normalized: [x_center, y_center, width, height] normalized to [0, 1]
    
    Returns:
        Location string like 'upper-left', 'center', 'lower-right', etc.
    """
    x_center, y_center, _, _ = bbox_normalized
    
    # Define grid thresholds
    LEFT_THRESHOLD = 0.33
    RIGHT_THRESHOLD = 0.67
    UPPER_THRESHOLD = 0.33
    LOWER_THRESHOLD = 0.67
    
    # Determine horizontal position
    if x_center < LEFT_THRESHOLD:
        h_pos = "left"
    elif x_center > RIGHT_THRESHOLD:
        h_pos = "right"
    else:
        h_pos = "center"
    
    # Determine vertical position
    if y_center < UPPER_THRESHOLD:
        v_pos = "upper"
    elif y_center > LOWER_THRESHOLD:
        v_pos = "lower"
    else:
        v_pos = "center"
    
    # Combine positions
    if v_pos == "center" and h_pos == "center":
        return "center"
    elif v_pos == "center":
        return h_pos
    elif h_pos == "center":
        return v_pos
    else:
        return f"{v_pos}-{h_pos}"


def check_occlusion(boxes: List[List[float]], iou_threshold: float = 0.3) -> bool:
    """
    Check if any boxes are overlapping (potential occlusion/concealment).
    
    Args:
        boxes: List of bounding boxes [x_center, y_center, width, height]
        iou_threshold: IOU threshold for considering boxes as overlapping
    
    Returns:
        True if significant overlap detected
    """
    if len(boxes) < 2:
        return False
    
    def box_iou(box1, box2):
        """Calculate IOU between two boxes."""
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        
        # Convert to corner coordinates
        x1_min, y1_min = x1 - w1/2, y1 - h1/2
        x1_max, y1_max = x1 + w1/2, y1 + h1/2
        x2_min, y2_min = x2 - w2/2, y2 - h2/2
        x2_max, y2_max = x2 + w2/2, y2 + h2/2
        
        # Calculate intersection
        inter_xmin = max(x1_min, x2_min)
        inter_ymin = max(y1_min, y2_min)
        inter_xmax = min(x1_max, x2_max)
        inter_ymax = min(y1_max, y2_max)
        
        if inter_xmax < inter_xmin or inter_ymax < inter_ymin:
            return 0.0
        
        inter_area = (inter_xmax - inter_xmin) * (inter_ymax - inter_ymin)
        box1_area = w1 * h1
        box2_area = w2 * h2
        union_area = box1_area + box2_area - inter_area
        
        return inter_area / union_area if union_area > 0 else 0.0
    
    # Check all pairs
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if box_iou(boxes[i], boxes[j]) > iou_threshold:
                return True
    
    return False


class YOLODetectionEngine:
    """Detection engine supporting both Ultralytics and ONNX backends."""
    
    def __init__(
        self,
        model_path: str,
        backend: str = 'ultralytics',
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        device: str = '0'
    ):
        """
        Initialize detection engine.
        
        Args:
            model_path: Path to model (.pt for Ultralytics, .onnx for ONNX)
            backend: 'ultralytics' or 'onnx'
            conf_threshold: Confidence threshold for detections
            iou_threshold: IOU threshold for NMS
            device: Device for inference ('0', 'cpu', etc.)
        """
        self.model_path = model_path
        self.backend = backend.lower()
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.device = device
        
        if self.backend == 'ultralytics':
            if not HAS_ULTRALYTICS:
                raise ImportError("Ultralytics not installed. Install with: pip install ultralytics")
            self.model = YOLO(model_path)
            self.class_names = self.model.names
            print(f"Loaded Ultralytics YOLO model from {model_path}")
            print(f"Classes: {len(self.class_names)}")
        
        elif self.backend == 'onnx':
            if not HAS_ONNX:
                raise ImportError(
                    "ONNX backend requested but ONNXRuntime not installed.\n"
                    "Install with: pip install onnxruntime-gpu (for GPU) or pip install onnxruntime (for CPU)"
                )
            
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider'] if device != 'cpu' else ['CPUExecutionProvider']
            self.session = ort.InferenceSession(model_path, providers=providers)
            
            # Get input/output info
            self.input_name = self.session.get_inputs()[0].name
            self.input_shape = self.session.get_inputs()[0].shape
            
            print(f"Loaded ONNX model from {model_path}")
            print(f"Input: {self.input_name} {self.input_shape}")
            
            # Load class names from metadata or use default
            # TODO: Store class names in ONNX metadata during export
            self.class_names = self._load_class_names()
        
        else:
            raise ValueError(f"Unknown backend: {backend}. Use 'ultralytics' or 'onnx'")
    
    def _load_class_names(self) -> Dict[int, str]:
        """Load class names for ONNX model (from metadata or default)."""
        # Try to load from same directory as model
        names_path = Path(self.model_path).parent / 'class_names.json'
        if names_path.exists():
            with open(names_path) as f:
                return json.load(f)
        
        # Default fallback (STCray categories)
        return {
            0: '3D Gun', 1: '3D printed gun', 2: 'Battery', 3: 'Blade',
            4: 'Bullet', 5: 'Cutter', 6: 'Explosive', 7: 'Gun',
            8: 'Hammer', 9: 'Handcuffs', 10: 'Injection', 11: 'Knife',
            12: 'Lighter', 13: 'Multilabel Threat', 14: 'Nail Cutter',
            15: 'Non Threat', 16: 'Other Sharp Item', 17: 'Pliers',
            18: 'Powerbank', 19: 'Scissors', 20: 'Screwdriver',
            21: 'Shaving Razor', 22: 'Syringe', 23: 'Wrench'
        }
    
    def predict(self, image: Image.Image) -> DetectionResult:
        """
        Run detection on image.
        
        Args:
            image: PIL Image
        
        Returns:
            DetectionResult with items, count, and concealment flag
        """
        if self.backend == 'ultralytics':
            return self._predict_ultralytics(image)
        elif self.backend == 'onnx':
            return self._predict_onnx(image)
    
    def _predict_ultralytics(self, image: Image.Image) -> DetectionResult:
        """Predict using Ultralytics YOLO."""
        # Run inference
        results = self.model.predict(
            image,
            conf=self.conf_threshold,
            iou=self.iou_threshold,
            verbose=False
        )
        
        # Extract detections
        items = []
        bboxes = []
        
        if len(results) > 0 and results[0].boxes is not None:
            for box in results[0].boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])
                bbox_normalized = box.xywhn[0].tolist()  # [x_center, y_center, w, h] normalized
                
                items.append(ItemDetection(
                    name=self.class_names[class_id],
                    confidence=confidence,
                    location=bbox_to_location(bbox_normalized),
                    bbox=bbox_normalized
                ))
                bboxes.append(bbox_normalized)
        
        # Check for occlusion/concealment
        has_concealed = check_occlusion(bboxes)
        
        return DetectionResult(
            items=items,
            total_count=len(items),
            has_concealed_items=has_concealed
        )
    
    def _predict_onnx(self, image: Image.Image) -> DetectionResult:
        """Predict using ONNX Runtime."""
        # TODO: Implement ONNX inference with proper preprocessing and postprocessing
        # This requires understanding the ONNX model's output format
        raise NotImplementedError("ONNX inference not yet implemented. Use 'ultralytics' backend.")


# Initialize FastAPI app
app = FastAPI(
    title="YOLO X-ray Detection API",
    description="OpenAI-compatible API for X-ray baggage threat detection using YOLO",
    version="1.0.0"
)

# Global detection engine
detection_engine: Optional[YOLODetectionEngine] = None


@app.on_event("startup")
async def startup_event():
    """Initialize detection engine on startup."""
    global detection_engine
    
    model_path = os.getenv('MODEL_PATH', 'runs/detect/xray_detection/weights/best.pt')
    backend = os.getenv('BACKEND', 'ultralytics')
    conf_threshold = float(os.getenv('CONF_THRESHOLD', '0.25'))
    iou_threshold = float(os.getenv('IOU_THRESHOLD', '0.45'))
    device = os.getenv('DEVICE', '0')
    
    print(f"Initializing detection engine...")
    print(f"  Model: {model_path}")
    print(f"  Backend: {backend}")
    print(f"  Confidence threshold: {conf_threshold}")
    print(f"  IOU threshold: {iou_threshold}")
    print(f"  Device: {device}")
    
    detection_engine = YOLODetectionEngine(
        model_path=model_path,
        backend=backend,
        conf_threshold=conf_threshold,
        iou_threshold=iou_threshold,
        device=device
    )
    
    print("✓ Detection engine ready")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model_loaded": detection_engine is not None,
        "backend": detection_engine.backend if detection_engine else None
    }


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: Request):
    """
    OpenAI-compatible chat completions endpoint for agentic workflows.
    
    Expects multipart/form-data with 'file' field containing X-ray image.
    Returns detection results in OpenAI chat completion format.
    """
    if detection_engine is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    # Parse multipart form data
    form = await request.form()
    
    if 'file' not in form:
        raise HTTPException(status_code=400, detail="No file provided. Send image as 'file' field.")
    
    file = form['file']
    
    try:
        # Read and open image
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes))
        
        # Ensure RGB
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Run detection
        result = detection_engine.predict(image)
        
        # Convert to JSON string for OpenAI format
        result_json = result.model_dump_json()
        
        # Create OpenAI-compatible response
        import time
        response = ChatCompletionResponse(
            id=f"chatcmpl-yolo-{int(time.time())}",
            object="chat.completion",
            created=int(time.time()),
            model=detection_engine.model_path,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=result_json
                    ),
                    finish_reason="stop"
                )
            ]
        )
        
        return response
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detection failed: {str(e)}")


@app.post("/v1/detect", response_model=DetectionResult)
async def detect(file: UploadFile = File(...)):
    """
    Direct detection endpoint (non-OpenAI format).
    
    Returns DetectionResult directly.
    """
    if detection_engine is None:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    try:
        # Read and open image
        image_bytes = await file.read()
        image = Image.open(io.BytesIO(image_bytes))
        
        # Ensure RGB
        if image.mode != 'RGB':
            image = image.convert('RGB')
        
        # Run detection
        result = detection_engine.predict(image)
        
        return result
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detection failed: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="YOLO X-ray Detection API Server"
    )
    parser.add_argument(
        '--model',
        type=str,
        default='runs/detect/xray_detection/weights/best.pt',
        help='Path to model (.pt or .onnx)'
    )
    parser.add_argument(
        '--backend',
        type=str,
        default='ultralytics',
        choices=['ultralytics', 'onnx'],
        help='Inference backend'
    )
    parser.add_argument(
        '--conf-threshold',
        type=float,
        default=0.25,
        help='Confidence threshold (default: 0.25)'
    )
    parser.add_argument(
        '--iou-threshold',
        type=float,
        default=0.45,
        help='IOU threshold for NMS (default: 0.45)'
    )
    parser.add_argument(
        '--device',
        type=str,
        default='0',
        help='Device (0, cpu, etc.)'
    )
    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8000,
        help='Port to bind (default: 8000)'
    )
    
    args = parser.parse_args()
    
    # Set environment variables for startup event
    os.environ['MODEL_PATH'] = args.model
    os.environ['BACKEND'] = args.backend
    os.environ['CONF_THRESHOLD'] = str(args.conf_threshold)
    os.environ['IOU_THRESHOLD'] = str(args.iou_threshold)
    os.environ['DEVICE'] = args.device
    
    # Run server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info"
    )


if __name__ == '__main__':
    main()
