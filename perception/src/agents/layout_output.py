from __future__ import annotations

import base64
import io
import json
from collections import Counter
from pathlib import Path

from PIL import Image


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


def _normalized_bbox(bbox, width, height):
    x1, y1, x2, y2 = bbox

    return [
        round(x1 / width, 6),
        round(y1 / height, 6),
        round(x2 / width, 6),
        round(y2 / height, 6),
    ]


def _center(bbox, width, height):
    x1, y1, x2, y2 = bbox

    return [
        round(((x1 + x2) / 2) / width, 6),
        round(((y1 + y2) / 2) / height, 6),
    ]


def _crop_image(source_image, bbox):
    width, height = source_image.size
    x1, y1, x2, y2 = [int(round(value)) for value in bbox]

    x1 = max(0, min(x1, width))
    x2 = max(0, min(x2, width))
    y1 = max(0, min(y1, height))
    y2 = max(0, min(y2, height))

    if x2 <= x1 or y2 <= y1:
        return None

    crop = source_image.crop((x1, y1, x2, y2))
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")

    return {
        "mime_type": "image/png",
        "width": crop.width,
        "height": crop.height,
        "base64": base64.b64encode(
            buffer.getvalue()
        ).decode("ascii"),
    }


def _formal_type(element):
    control_type = str(
        element.get("control_type")
        or ""
    ).lower()

    if control_type in ALLOWED_TYPES:
        return control_type

    coarse_type = str(
        element.get("element_type")
        or ""
    ).lower()

    if coarse_type == "text":
        return "text"

    if coarse_type in {"control", "icon"}:
        return "button"

    return "text"


def _visible_text(element):
    text = str(
        element.get("description")
        or element.get("text")
        or ""
    ).strip()

    if text.lower() in {
        "",
        "unknown_icon",
        "unknown",
        "none",
        "null",
    }:
        return ""

    return text


def _make_element(element, index, source_image):
    bbox = [
        round(float(value), 2)
        for value in element.get("bbox", [0, 0, 0, 0])
    ]

    width, height = source_image.size
    text = _visible_text(element)
    control_type = _formal_type(element)

    machine = {
        "id": int(element.get("element_id", index)),
        "bbox": bbox,
        "normalized_bbox": _normalized_bbox(
            bbox,
            width,
            height,
        ),
        "normalized_center": _center(
            bbox,
            width,
            height,
        ),
    }

    # 无可读文字时，将真实元素截图放在 machine 最后。
    if not text:
        image_payload = _crop_image(source_image, bbox)

        if image_payload is not None:
            machine["image"] = image_payload

        return {
            "type": control_type,
            "machine": machine,
        }

    return {
        "text": text,
        "type": control_type,
        "machine": machine,
    }


def _region_path(bbox, width, height):
    x1, y1, x2, y2 = bbox

    center_x = ((x1 + x2) / 2) / width
    center_y = ((y1 + y2) / 2) / height

    if center_x <= 0.04:
        return ("left_global_navigation",)

    if center_y < 0.12:
        return ("top_navigation_toolbar",)

    if center_y < 0.22:
        return ("tab_area",)

    if center_y >= 0.90 and center_x < 0.36:
        return ("bottom_action_bar",)

    if center_x < 0.36:
        return (
            "main_workspace",
            "pnr_summary_panel",
        )

    if center_y >= 0.22:
        return (
            "main_workspace",
            "operation_panel",
        )

    return ("other",)


def _element_sort_key(item):
    machine = item["machine"]
    bbox = machine["bbox"]

    return (
        bbox[1],
        bbox[0],
        machine["id"],
    )


def build_partitioned_output(input_path, result):
    input_path = Path(input_path)
    source_image = Image.open(input_path).convert("RGB")
    width, height = source_image.size

    regions = {
        "left_global_navigation": {
            "elements": [],
        },
        "top_navigation_toolbar": {
            "elements": [],
        },
        "tab_area": {
            "elements": [],
        },
        "main_workspace": {
            "pnr_summary_panel": {
                "elements": [],
            },
            "operation_panel": {
                "elements": [],
            },
        },
        "bottom_action_bar": {
            "elements": [],
        },
    }

    other_elements = []

    for index, element in enumerate(result.get("elements", [])):
        item = _make_element(
            element,
            index,
            source_image,
        )

        path = _region_path(
            item["machine"]["bbox"],
            width,
            height,
        )

        if path == ("other",):
            other_elements.append(item)
        elif len(path) == 1:
            regions[path[0]]["elements"].append(item)
        else:
            regions[path[0]][path[1]]["elements"].append(item)

    regions["left_global_navigation"]["elements"].sort(
        key=_element_sort_key
    )
    regions["top_navigation_toolbar"]["elements"].sort(
        key=_element_sort_key
    )
    regions["tab_area"]["elements"].sort(
        key=_element_sort_key
    )
    regions["main_workspace"]["pnr_summary_panel"]["elements"].sort(
        key=_element_sort_key
    )
    regions["main_workspace"]["operation_panel"]["elements"].sort(
        key=_element_sort_key
    )
    regions["bottom_action_bar"]["elements"].sort(
        key=_element_sort_key
    )
    other_elements.sort(key=_element_sort_key)

    output = {
        "image": {
            "file_name": input_path.name,
            "width": width,
            "height": height,
        },
        **regions,
    }

    if other_elements:
        output["other"] = {
            "elements": other_elements,
        }

    return output


def write_partitioned_output(input_path, output_dir, result):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output = build_partitioned_output(
        input_path,
        result,
    )

    output_path = (
        output_dir
        / f"{Path(input_path).stem}_elements.json"
    )

    output_path.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return output, output_path


def _iter_elements(node):
    if isinstance(node, dict):
        elements = node.get("elements")

        if isinstance(elements, list):
            yield from elements

        for key, value in node.items():
            if key not in {"elements", "image"}:
                yield from _iter_elements(value)


def print_partition_summary(output):
    elements = list(_iter_elements(output))
    distribution = Counter(
        item["type"]
        for item in elements
    )

    print("\n✅ 正式分区输出完成")
    print("元素数:", len(elements))
    print("控件类型:", dict(distribution))
