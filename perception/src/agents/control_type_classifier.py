from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont


ALLOWED_TYPES = {
    "text",
    "button",
    "input",
    "select",
    "tab",
    "checkbox",
    "radio",
    "switch",
    "image",
}


def _fallback_type(element):
    """
    Qwen 不可用时的保守回退。
    不包含任何具体业务词规则。
    """
    coarse_type = str(element.get("element_type") or "").lower()

    if coarse_type == "text":
        return "text"

    if coarse_type in {"control", "icon"}:
        return "button"

    return "text"


def _load_font(size=18):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]

    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)

    return ImageFont.load_default()


def _build_annotated_image(image_path, elements):
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _load_font()

    colors = {
        "text": "#22c55e",
        "control": "#ef4444",
        "icon": "#2563eb",
    }

    for index, element in enumerate(elements):
        element_id = int(element.get("element_id", index))
        bbox = element.get("bbox") or [0, 0, 0, 0]

        x1, y1, x2, y2 = [int(round(float(value))) for value in bbox]
        coarse_type = str(element.get("element_type") or "text")
        color = colors.get(coarse_type, "#f59e0b")

        draw.rectangle(
            [x1, y1, x2, y2],
            outline=color,
            width=2,
        )

        label = str(element_id)
        text_bbox = draw.textbbox((x1, y1), label, font=font)
        label_width = text_bbox[2] - text_bbox[0] + 6
        label_height = text_bbox[3] - text_bbox[1] + 4

        label_y = max(0, y1 - label_height)

        draw.rectangle(
            [x1, label_y, x1 + label_width, label_y + label_height],
            fill=color,
        )
        draw.text(
            (x1 + 3, label_y + 1),
            label,
            fill="white",
            font=font,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_json(content):
    content = str(content or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    array_start = content.find("[")
    array_end = content.rfind("]")

    if array_start >= 0 and array_end > array_start:
        return json.loads(content[array_start:array_end + 1])

    object_start = content.find("{")
    object_end = content.rfind("}")

    if object_start >= 0 and object_end > object_start:
        return json.loads(content[object_start:object_end + 1])

    raise ValueError("Qwen 返回内容中没有可解析的 JSON。")


def classify_elements(
    image_path,
    elements,
    model=None,
    ollama_url=None,
    timeout=240,
):
    """
    使用整张截图和元素编号识别真实控件类型。

    只修改 element["control_type"]，不会改变：
    - OCR 文本
    - bbox
    - 元素数量
    - 分区结果
    """
    model = model or os.environ.get(
        "GUI_CONTROL_MODEL",
        "qwen3-vl:32b",
    )
    ollama_url = (
        ollama_url
        or os.environ.get("OLLAMA_URL", "http://localhost:11434")
    ).rstrip("/")

    for element in elements:
        element["control_type"] = _fallback_type(element)

    if os.environ.get("GUI_CONTROL_TYPING", "1") == "0":
        print("ℹ️ 已关闭视觉控件分类，使用保守类型。")
        return {
            "method": "fallback",
            "model": None,
            "classified_count": 0,
        }

    metadata = []

    for index, element in enumerate(elements):
        element_id = int(element.get("element_id", index))
        text = str(element.get("description") or "").strip()
        text = " ".join(text.split())

        metadata.append({
            "id": element_id,
            "coarse_type": element.get("element_type"),
            "text": text if text != "unknown_icon" else "",
            "bbox": [
                round(float(value), 1)
                for value in element.get("bbox", [])
            ],
        })

    prompt = f"""
你正在识别一张 GUI 截图中的控件。

截图已经使用数字标注每个元素。请根据元素的视觉样式、所在位置和交互含义，
为每个元素选择一个类型。

只能使用以下类型：

text：不可操作的普通文字、标题、提示、状态文字
button：按钮、链接、导航入口、图标按钮、折叠按钮、翻页按钮
input：文本框、日期框、搜索框、多行文本框
select：带下拉选择功能的输入控件
tab：页面标签或功能标签
checkbox：可以独立勾选的选项
radio：一组互斥单选项
switch：开关控件
image：不可点击的 Logo、插画或装饰图片

判断要求：
1. 不要根据航空、旅客等具体业务知识建立规则。
2. 没有文字但可以点击的图标应识别为 button。
3. 只有确认是不可操作视觉内容时才使用 image。
4. 输入框中的占位文字仍属于 input。
5. 状态文字和状态徽章统一归入 text。
6. 必须返回所有元素。
7. 只返回 JSON，不要解释。

返回格式：

[
  {{"id": 0, "type": "text"}},
  {{"id": 1, "type": "button"}}
]

元素信息：

{json.dumps(metadata, ensure_ascii=False)}
""".strip()

    try:
        annotated_image = _build_annotated_image(
            image_path,
            elements,
        )

        response = requests.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [annotated_image],
                    }
                ],
                "options": {
                    "temperature": 0,
                },
            },
            timeout=timeout,
        )
        response.raise_for_status()

        content = (
            response.json()
            .get("message", {})
            .get("content", "")
        )
        parsed = _extract_json(content)

        if isinstance(parsed, dict):
            parsed = (
                parsed.get("elements")
                or parsed.get("results")
                or parsed.get("items")
                or []
            )

        predictions = {}

        for item in parsed:
            if not isinstance(item, dict):
                continue

            raw_id = item.get("id", item.get("element_id"))
            predicted_type = str(item.get("type") or "").lower()

            if raw_id is None or predicted_type not in ALLOWED_TYPES:
                continue

            predictions[int(raw_id)] = predicted_type

        classified_count = 0

        for index, element in enumerate(elements):
            element_id = int(element.get("element_id", index))

            if element_id in predictions:
                element["control_type"] = predictions[element_id]
                classified_count += 1

        print(
            f"✅ Qwen 控件类型识别完成："
            f"{classified_count}/{len(elements)} 个元素"
        )

        return {
            "method": "qwen_visual_control_typing",
            "model": model,
            "classified_count": classified_count,
            "element_count": len(elements),
        }

    except Exception as error:
        print(f"⚠️ Qwen 控件分类失败，使用保守类型：{error}")

        return {
            "method": "fallback_after_error",
            "model": model,
            "classified_count": 0,
            "error": str(error),
        }
