from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

DEFAULT_MEMORY_PATH = Path(__file__).resolve().parents[2] / "ui_memory" / "prototypes.json"


class UIMemoryIndex:
    """只提供检索证据；绝不修改感知层的元素类型、状态或业务语义。"""

    def __init__(self, payload):
        self.payload = payload
        self.observation_count = int(payload.get("observation_count", 0))
        self.families = payload.get("text_families", [])
        self.screens = payload.get("screen_index", [])
        self.by_key = {
            (str(item.get("candidate_type", "")), str(item.get("text_normalized", ""))): item
            for item in self.families
        }

        self.document_frequency = Counter()
        for screen in self.screens:
            self.document_frequency.update(set(screen.get("ocr_texts", [])))
        self.screen_count = max(1, len(self.screens))

    @classmethod
    def load(cls, path=DEFAULT_MEMORY_PATH):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def _idf(self, text):
        frequency = self.document_frequency.get(text, 0)
        return math.log((self.screen_count + 1) / (frequency + 1)) + 1.0

    def retrieve_screens(self, text_keys, top_k=3):
        query = {str(value).strip() for value in text_keys if str(value).strip()}
        if not query:
            return []

        matches = []
        for screen in self.screens:
            known = set(screen.get("ocr_texts", []))
            shared = query & known
            if not shared:
                continue
            union = query | known
            similarity = sum(self._idf(text) for text in shared) / sum(
                self._idf(text) for text in union
            )
            matches.append({
                "observation_id": screen.get("observation_id"),
                "corpus_path": screen.get("corpus_path"),
                "similarity": round(similarity, 4),
                "shared_text_count": len(shared),
            })

        return sorted(
            matches,
            key=lambda item: (-item["similarity"], -item["shared_text_count"]),
        )[:top_k]

    def enrich(self, result):
        query_texts = set()
        matched_elements = 0

        for element in result.get("elements", []):
            ocr_evidence = (element.get("evidence") or {}).get("ocr", {})
            has_ocr = (
                ocr_evidence.get("present", False)
                if isinstance(ocr_evidence, dict)
                else bool(ocr_evidence)
            )
            text = str(element.get("text_normalized") or "").strip()

            if has_ocr and text:
                query_texts.add(text)
            if not has_ocr or not text:
                continue

            family = self.by_key.get((
                str(element.get("element_type", "unknown")),
                text,
            ))
            if family is None:
                continue

            matched_elements += 1
            element["prototype_id"] = family["family_id"]
            element["prototype_match"] = {
                "method": "exact_normalized_ocr_text_and_candidate_type",
                "exact_key_match": True,
                "identity_confidence": None,
            }
            element["prototype_support"] = {
                "screen_count": family["screen_count"],
                "occurrence_count": family["occurrence_count"],
                "screen_coverage": family["screen_coverage"],
            }

        result["memory_context"] = {
            "memory_schema": self.payload.get("schema_version"),
            "observation_count": self.observation_count,
            "matched_element_count": matched_elements,
            "uses_business_rules": False,
            "changes_perception_output": False,
            "screen_retrieval_method": "idf_weighted_jaccard",
        }
        result["screen_retrieval"] = self.retrieve_screens(query_texts)
        return result
