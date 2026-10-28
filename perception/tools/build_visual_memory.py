from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import timm
import torch
import torch.nn.functional as F
from PIL import Image
from timm.data import create_transform, resolve_model_data_config

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL_NAME = "vit_large_patch14_reg4_dinov2.lvd142m"


def has_evidence(element, key):
    value = (element.get("evidence") or {}).get(key, {})
    return value.get("present", False) if isinstance(value, dict) else bool(value)


def main():
    parser = argparse.ArgumentParser(
        description="Build a rule-free DINOv2 visual embedding index for OCR-less icon candidates."
    )
    parser.add_argument(
        "--observations",
        type=Path,
        default=ROOT / "ui_memory" / "observations_v2",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "ui_memory" / "visual_memory_v1",
    )
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args()

    files = sorted(args.observations.glob("*.json"))
    if not files:
        raise SystemExit(f"No observations found in: {args.observations}")

    if args.device == "auto":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)

    if device.type == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was requested but torch.cuda.is_available() is False")

    print("device:", device)
    print("model:", MODEL_NAME)
    print("loading DINOv2 ...")

    model = timm.create_model(
        MODEL_NAME,
        pretrained=True,
        num_classes=0,
    ).eval().to(device)

    config = resolve_model_data_config(model)
    transform = create_transform(**config, is_training=False)
    print("input_size:", config["input_size"])

    pending_tensors = []
    pending_records = []
    embeddings = []
    index_records = []
    image_cache = {}
    skipped = 0

    def flush():
        if not pending_tensors:
            return

        batch = torch.stack(pending_tensors).to(device)
        with torch.inference_mode():
            features = model(batch)

        if isinstance(features, (tuple, list)):
            features = features[0]
        if features.ndim > 2:
            features = features.mean(dim=1)

        features = F.normalize(features.float(), dim=1).cpu().numpy().astype(np.float32)
        embeddings.append(features)
        index_records.extend(pending_records)

        print(f"encoded: {len(index_records)}", flush=True)
        pending_tensors.clear()
        pending_records.clear()

    for observation_path in files:
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        image_path = observation.get("source_image")
        if not image_path:
            continue

        if image_path not in image_cache:
            image_cache[image_path] = cv2.imread(image_path)
        image = image_cache[image_path]
        if image is None:
            print(f"skip unreadable image: {image_path}")
            continue

        height, width = image.shape[:2]
        for element in observation.get("elements", []):
            if has_evidence(element, "ocr") or not has_evidence(element, "icon_detector"):
                continue

            x1, y1, x2, y2 = [int(round(value)) for value in element["bbox"]]
            x1, x2 = max(0, min(x1, width)), max(0, min(x2, width))
            y1, y2 = max(0, min(y1, height)), max(0, min(y2, height))
            crop = image[y1:y2, x1:x2]

            if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
                skipped += 1
                continue

            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            pending_tensors.append(transform(Image.fromarray(rgb)))
            pending_records.append({
                "embedding_index": len(index_records) + len(pending_records),
                "observation_id": observation.get("observation_id"),
                "corpus_path": observation.get("corpus_path"),
                "source_image": image_path,
                "element_id": element.get("element_id"),
                "bbox": element.get("bbox"),
                "relative_bbox": element.get("relative_bbox"),
                "icon_score": element.get("icon_score"),
                "visual_signature": element.get("visual_signature") or {},
            })

            if len(pending_tensors) >= args.batch_size:
                flush()

    flush()

    if not index_records:
        raise SystemExit("No OCR-less icon candidates were encoded.")

    matrix = np.concatenate(embeddings, axis=0)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    np.save(args.output_dir / "embeddings.npy", matrix)
    payload = {
        "schema_version": "visual_embedding_index_v1",
        "method": "dinov2_visual_embedding",
        "model_name": MODEL_NAME,
        "device_used": str(device),
        "input_size": list(config["input_size"]),
        "embedding_count": int(matrix.shape[0]),
        "embedding_dim": int(matrix.shape[1]),
        "skipped_crop_count": skipped,
        "normalization": "l2",
        "uses_business_rules": False,
        "records": index_records,
    }
    (args.output_dir / "visual_index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n✅ visual memory built")
    print("embeddings:", args.output_dir / "embeddings.npy")
    print("index:", args.output_dir / "visual_index.json")
    print("shape:", tuple(matrix.shape))
    print("skipped:", skipped)


if __name__ == "__main__":
    main()
