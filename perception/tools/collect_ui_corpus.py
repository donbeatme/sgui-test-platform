from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agents.perception_ocr import OCRPerceptionSkill

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:12]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect raw rule-free GUI observations from screenshots."
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "ui_memory" / "observations_v2",
    )
    parser.add_argument("--use-qwen", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    images = sorted(
        path for path in args.input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise SystemExit(f"No screenshots found in: {args.input_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    skill = OCRPerceptionSkill(
        ollama_model="qwen3-vl:32b",
        use_yolo=True,
        use_qwen=args.use_qwen,
        use_memory=False,
        ocr_conf=0.5,
        yolo_conf=0.35,
    )

    totals = Counter()
    written = 0
    for index, image_path in enumerate(images, start=1):
        digest = sha1_file(image_path)
        output_path = args.output_dir / f"{image_path.stem}_{digest}.json"

        if output_path.exists() and not args.overwrite:
            print(f"[{index}/{len(images)}] skip {image_path.name}")
            continue

        print(f"[{index}/{len(images)}] observe {image_path.name}")
        result = skill.parse_observation(str(image_path))
        result["record_version"] = "ui_observation_v2"
        result["observation_id"] = digest
        result["source_image"] = str(image_path.resolve())
        result["corpus_path"] = str(image_path.relative_to(args.input_dir))

        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        totals.update(item["element_type"] for item in result["elements"])
        written += 1

    print(f"\nwritten observations: {written}")
    print(f"element distribution: {dict(totals)}")
    print(f"output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
