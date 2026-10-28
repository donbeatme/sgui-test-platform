from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import timm
import torch
import torch.nn.functional as F
from PIL import Image
from timm.data import create_transform, resolve_model_data_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.perception_ocr import OCRPerceptionSkill


def has_evidence(element, key):
    value = (element.get("evidence") or {}).get(key, {})
    return value.get("present", False) if isinstance(value, dict) else bool(value)


def visual_candidate(element):
    return (
        not has_evidence(element, "ocr")
        and has_evidence(element, "icon_detector")
    )


def crop_element(image, bbox):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [int(round(value)) for value in bbox]
    x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
    y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))

    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    return Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))


def encode_images(model, transform, images, batch_size):
    vectors = []

    with torch.inference_mode():
        for start in range(0, len(images), batch_size):
            batch = torch.stack([
                transform(image)
                for image in images[start:start + batch_size]
            ])

            features = model(batch)
            if isinstance(features, (tuple, list)):
                features = features[0]
            if features.ndim > 2:
                features = features.mean(dim=1)

            vectors.append(F.normalize(features, dim=1).cpu().numpy())

    return np.concatenate(vectors, axis=0).astype(np.float32)


def main():
    parser = argparse.ArgumentParser(
        description="为通用 GUI 观察结果增加视觉近邻证据；不修改识别结论。"
    )
    parser.add_argument("--input", required=True, help="待识别截图路径")
    parser.add_argument(
        "--visual-memory-path",
        default="ui_memory/visual_memory_v2_224",
        help="视觉记忆目录",
    )
    parser.add_argument(
        "--output-dir",
        default="quality_output_visual_memory",
        help="输出目录",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    memory_dir = Path(args.visual_memory_path).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"找不到输入截图：{input_path}")

    vectors_path = memory_dir / "embeddings.npy"
    index_path = memory_dir / "visual_index.json"
    if not vectors_path.is_file() or not index_path.is_file():
        raise FileNotFoundError(f"视觉记忆不完整：{memory_dir}")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    memory_vectors = np.load(vectors_path).astype(np.float32)
    records = index["records"]

    if len(records) != len(memory_vectors):
        raise RuntimeError("视觉索引记录数与向量数不一致。")

    memory_vectors /= np.maximum(
        np.linalg.norm(memory_vectors, axis=1, keepdims=True),
        1e-12,
    )

    print("⏳ 运行通用 OCR + YOLO 观察器 ...")
    observer = OCRPerceptionSkill(
        use_yolo=True,
        use_qwen=False,
        use_memory=False,
    )
    result = observer.parse_observation(str(input_path))

    image = cv2.imread(str(input_path))
    if image is None:
        raise RuntimeError(f"无法读取图片：{input_path}")

    element_ids = []
    crops = []

    for element_id, element in enumerate(result["elements"]):
        if not visual_candidate(element):
            continue

        crop = crop_element(image, element["bbox"])
        if crop is not None:
            element_ids.append(element_id)
            crops.append(crop)

    context = {
        "schema": "ui_visual_memory_query_v1",
        "memory_schema": index.get("schema"),
        "model_name": index.get("model_name"),
        "image_size": index.get("image_size"),
        "memory_candidate_count": len(records),
        "query_visual_candidate_count": len(crops),
        "top_k": args.top_k,
        "uses_business_rules": False,
        "changes_element_type": False,
        "changes_state": False,
        "changes_name": False,
        "changes_action": False,
    }

    if crops:
        model_name = index["model_name"]
        image_size = int(index["image_size"])

        print(f"⏳ 加载视觉模型：{model_name} ({image_size}×{image_size})")
        started = time.time()

        model = timm.create_model(
            model_name,
            pretrained=True,
            num_classes=0,
            img_size=image_size,
        ).eval()

        config = resolve_model_data_config(model)
        config["input_size"] = (3, image_size, image_size)
        transform = create_transform(**config, is_training=False)

        # 预热，不计入查询时间
        with torch.inference_mode():
            model(transform(crops[0]).unsqueeze(0))

        query_started = time.time()
        query_vectors = encode_images(
            model=model,
            transform=transform,
            images=crops,
            batch_size=args.batch_size,
        )
        similarities = query_vectors @ memory_vectors.T

        for row_id, element_id in enumerate(element_ids):
            scores = similarities[row_id]
            top_ids = np.argsort(-scores)[:args.top_k]

            neighbors = []
            for rank, memory_id in enumerate(top_ids, 1):
                record = records[int(memory_id)]
                neighbors.append({
                    "rank": rank,
                    "similarity": round(float(scores[memory_id]), 6),
                    "visual_id": record["visual_id"],
                    "corpus_path": record["corpus_path"],
                    "source_observation": record["source_observation"],
                    "element_index": record["element_index"],
                    "bbox": record["bbox"],
                    "candidate_type": record.get("candidate_type"),
                })

            result["elements"][element_id]["visual_neighbors"] = neighbors
            result["elements"][element_id]["visual_neighbor_margin"] = (
                round(float(scores[top_ids[0]] - scores[top_ids[1]]), 6)
                if len(top_ids) >= 2
                else None
            )

        context["model_load_seconds"] = round(query_started - started, 3)
        context["query_encode_seconds"] = round(time.time() - query_started, 3)

    result["visual_memory_context"] = context

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{input_path.stem}_ocr_elements_visual_memory.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n✅ 视觉记忆证据已写入")
    print("元素数:", result["elements_count"])
    print("纯视觉查询数:", len(crops))
    print("输出:", output_path)
    print("视觉查询耗时:", context.get("query_encode_seconds", 0), "秒")


if __name__ == "__main__":
    main()
