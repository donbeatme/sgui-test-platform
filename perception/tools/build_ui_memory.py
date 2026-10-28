from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mean(values, digits=5):
    return round(sum(values) / len(values), digits) if values else None


def family_id(candidate_type, text):
    value = f"{candidate_type}\0{text}".encode("utf-8")
    return "family_" + hashlib.sha1(value).hexdigest()[:12]


def signature_summary(records):
    fields = (
        "aspect_ratio",
        "relative_area",
        "luminance_mean",
        "luminance_std",
        "edge_density",
    )
    result = {}
    for field in fields:
        values = [
            r["visual_signature"].get(field)
            for r in records
            if r["visual_signature"].get(field) is not None
        ]
        result[field] = mean(values)

    bgr = [
        r["visual_signature"].get("mean_bgr")
        for r in records
        if len(r["visual_signature"].get("mean_bgr", [])) == 3
    ]
    if bgr:
        result["mean_bgr"] = [
            mean([item[index] for item in bgr], 2) for index in range(3)
        ]
    return result


def layout_summary(records):
    centers = []
    for r in records:
        x1, y1, x2, y2 = r.get("relative_bbox", [0, 0, 0, 0])
        centers.append(((x1 + x2) / 2, (y1 + y2) / 2))
    return {
        "mean_center": [
            mean([item[0] for item in centers]),
            mean([item[1] for item in centers]),
        ]
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--observations",
        type=Path,
        default=ROOT / "ui_memory" / "observations_v2",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "ui_memory" / "prototypes_v2.json",
    )
    args = parser.parse_args()

    files = sorted(args.observations.glob("*.json"))
    if not files:
        raise SystemExit(f"No observation JSON files in: {args.observations}")

    groups = defaultdict(list)
    screen_index = []
    type_counts = Counter()
    unresolved_without_ocr = 0

    for file_path in files:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        observation_id = data.get("observation_id", file_path.stem)
        screen_texts = set()
        screen_counts = Counter()

        for element in data.get("elements", []):
            candidate_type = str(element.get("element_type", "unknown"))
            type_counts[candidate_type] += 1
            screen_counts[candidate_type] += 1

            evidence = element.get("evidence") or {}
            ocr_evidence = evidence.get("ocr", {})
            has_ocr = (
                ocr_evidence.get("present", False)
                if isinstance(ocr_evidence, dict)
                else bool(ocr_evidence)
            )
            text = str(element.get("text_normalized") or "").strip()

            if not has_ocr or not text:
                unresolved_without_ocr += 1
                continue

            screen_texts.add(text)
            groups[(candidate_type, text)].append({
                "observation_id": observation_id,
                "source_image": data.get("source_image"),
                "corpus_path": data.get("corpus_path"),
                "element_id": element.get("element_id"),
                "description": element.get("description", ""),
                "bbox": element.get("bbox"),
                "relative_bbox": element.get("relative_bbox"),
                "detection_confidence": element.get("detection_confidence"),
                "visual_signature": element.get("visual_signature") or {},
            })

        screen_index.append({
            "observation_id": observation_id,
            "source_image": data.get("source_image"),
            "corpus_path": data.get("corpus_path"),
            "element_count": data.get("elements_count", 0),
            "candidate_type_counts": dict(screen_counts),
            "ocr_texts": sorted(screen_texts),
        })

    families = []
    for (candidate_type, text), records in groups.items():
        screen_ids = sorted({r["observation_id"] for r in records})
        raw_variants = Counter(r["description"] for r in records)

        representatives = []
        seen_screens = set()
        for record in records:
            screen_id = record["observation_id"]
            if screen_id in seen_screens:
                continue
            representatives.append(record)
            seen_screens.add(screen_id)
            if len(representatives) == 5:
                break

        families.append({
            "family_id": family_id(candidate_type, text),
            "candidate_type": candidate_type,
            "text_normalized": text,
            "occurrence_count": len(records),
            "screen_count": len(screen_ids),
            "screen_coverage": round(len(screen_ids) / len(files), 4),
            "raw_text_variants": dict(raw_variants),
            "visual_signature_mean": signature_summary(records),
            "layout_mean": layout_summary(records),
            "representatives": representatives,
        })

    families.sort(
        key=lambda item: (
            -item["screen_count"],
            -item["occurrence_count"],
            item["candidate_type"],
            item["text_normalized"],
        )
    )

    result = {
        "schema_version": "ui_memory_text_index_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matching_policy": {
            "grouping": "exact normalized OCR text plus generic candidate type",
            "uses_business_rules": False,
            "infers_role": False,
            "infers_state": False,
            "infers_control_subtype": False,
        },
        "observation_count": len(files),
        "element_count": sum(type_counts.values()),
        "element_type_distribution": dict(type_counts),
        "text_family_count": len(families),
        "unresolved_without_ocr_count": unresolved_without_ocr,
        "text_families": families,
        "screen_index": screen_index,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"observations: {result['observation_count']}")
    print(f"elements: {result['element_count']}")
    print(f"text families: {result['text_family_count']}")
    print(f"without OCR evidence: {result['unresolved_without_ocr_count']}")
    print(f"output: {args.output}")
    print("\nTop recurring families:")
    for item in families[:30]:
        print(
            f"{item['screen_count']:2d} screens | "
            f"{item['occurrence_count']:3d} occurrences | "
            f"{item['candidate_type']:7s} | "
            f"{item['text_normalized']}"
        )


if __name__ == "__main__":
    main()
