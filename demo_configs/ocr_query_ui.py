#!/usr/bin/env python3
"""
Simple Gradio UI for querying a Ray Serve hosted vision model.

Usage:
    pip install gradio requests pillow
    python demo_configs/ocr_query_ui.py

The UI lets you:
  - Set the cluster host URL and Ray app route_prefix
  - Optionally upload an image (or skip for text-only queries)
  - Enter a prompt and send to /v1/chat/completions
"""

import base64
import io
import json
import os

import gradio as gr
import requests
from PIL import Image


DEFAULT_HOST = os.environ.get(
    "CLUSTER_HOST",
    "https://ray-cluster-head.ml-1841266f-15a.qzhong-1.a465-9q4k.cloudera.site",
)
DEFAULT_ROUTE_PREFIX = os.environ.get("ROUTE_PREFIX", "/paddleocr")
DEFAULT_MODEL = os.environ.get("MODEL_PATH", "/home/cdsw/models/PaddleOCR-VL-1.5")
DEFAULT_PROMPT = "What text can you read in this image? List all detected text."


def image_to_data_url(pil_image: Image.Image) -> str:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def build_endpoint_url(host: str, route_prefix: str) -> str:
    host = host.rstrip("/")
    route_prefix = "/" + route_prefix.strip("/")
    return f"{host}{route_prefix}/v1/chat/completions"


def send_query(
    host: str,
    route_prefix: str,
    model: str,
    prompt: str,
    image,
    max_tokens: int,
    temperature: float,
    verify_ssl: bool,
) -> tuple[str, str]:
    """Send request to the model endpoint, return (response_text, raw_json)."""
    url = build_endpoint_url(host, route_prefix)

    content: list[dict] = []

    if image is not None:
        data_url = image_to_data_url(image)
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    content.append({"type": "text", "text": prompt})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": int(max_tokens),
        "temperature": float(temperature),
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=120, verify=verify_ssl)
        resp.raise_for_status()
        data = resp.json()
        raw = json.dumps(data, indent=2)
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "(no content in response)")
        )
        return text, raw
    except requests.exceptions.SSLError as e:
        msg = f"SSL error: {e}\n\nTry disabling SSL verification."
        return msg, msg
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            pass
        msg = f"HTTP {e.response.status_code}: {e}\n\n{body}"
        return msg, msg
    except requests.exceptions.RequestException as e:
        msg = f"Request failed: {e}"
        return msg, msg
    except Exception as e:
        msg = f"Error: {e}"
        return msg, msg


def update_endpoint_url(host: str, route_prefix: str) -> str:
    return build_endpoint_url(host, route_prefix)


with gr.Blocks(title="Ray Serve Vision Query") as demo:
    gr.Markdown("## Ray Serve Vision Model Query")
    gr.Markdown(
        "Connect to a Ray Serve hosted vision model and send OpenAI-compatible "
        "chat completion requests.  Image upload is **optional** — leave blank for text-only queries."
    )

    with gr.Row():
        with gr.Column(scale=3):
            host_input = gr.Textbox(
                label="Cluster Host URL",
                value=DEFAULT_HOST,
                placeholder="https://ray-cluster-head.example.cloudera.site",
            )
        with gr.Column(scale=1):
            route_prefix_input = gr.Textbox(
                label="Ray App route_prefix",
                value=DEFAULT_ROUTE_PREFIX,
                placeholder="/paddleocr",
            )

    endpoint_display = gr.Textbox(
        label="Endpoint URL (auto-computed)",
        value=build_endpoint_url(DEFAULT_HOST, DEFAULT_ROUTE_PREFIX),
        interactive=False,
    )

    model_input = gr.Textbox(
        label="Model Path",
        value=DEFAULT_MODEL,
        placeholder="/home/cdsw/models/PaddleOCR-VL-1.5",
    )

    with gr.Row():
        with gr.Column():
            image_input = gr.Image(
                label="Upload Image (optional)",
                type="pil",
            )
        with gr.Column():
            prompt_input = gr.Textbox(
                label="Prompt",
                value=DEFAULT_PROMPT,
                lines=5,
            )
            with gr.Row():
                max_tokens_input = gr.Slider(
                    label="Max Tokens", minimum=64, maximum=2048, value=512, step=64
                )
                temperature_input = gr.Slider(
                    label="Temperature", minimum=0.0, maximum=1.0, value=0.1, step=0.05
                )
            verify_ssl_input = gr.Checkbox(label="Verify SSL", value=True)

    submit_btn = gr.Button("Send Query", variant="primary")

    with gr.Row():
        with gr.Column():
            response_text = gr.Textbox(
                label="Model Response", lines=12, interactive=False
            )
        with gr.Column():
            raw_json = gr.Code(
                label="Raw JSON Response", language="json", lines=12
            )

    # Update endpoint URL preview as user types
    for inp in [host_input, route_prefix_input]:
        inp.change(
            fn=update_endpoint_url,
            inputs=[host_input, route_prefix_input],
            outputs=endpoint_display,
        )

    submit_btn.click(
        fn=send_query,
        inputs=[
            host_input,
            route_prefix_input,
            model_input,
            prompt_input,
            image_input,
            max_tokens_input,
            temperature_input,
            verify_ssl_input,
        ],
        outputs=[response_text, raw_json],
    )

if __name__ == "__main__":
    port = int(os.environ.get("GRADIO_PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, share=False)
