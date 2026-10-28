from __future__ import annotations

import base64
import json
import os
import time
import unicodedata
from collections import Counter
from io import BytesIO
from pathlib import Path

import cv2
import requests
from PIL import Image


class OCRPerceptionSkill:
    """通用 GUI 截图观察器：OCR、图标检测、几何关联、可选记忆检索。"""

    def __init__(
        self,
        yolo_model_path=None,
        ollama_model="qwen3-vl:32b",
        ollama_url="http://localhost:11434",
        use_yolo=True,
        use_qwen=False,
        use_memory=False,
        ui_memory_path=None,
        ocr_conf=0.5,
        yolo_conf=0.35,
        merge_iou=0.15,
        max_caption=15,
    ):
        self.project_root = Path(__file__).resolve().parents[2]
        self.yolo_path = str(
            yolo_model_path
            or self.project_root / "OmniParser/weights/icon_detect/model.pt"
        )
        ollama_model = os.environ.get("GUI_ICON_MODEL", ollama_model)
        ollama_url = os.environ.get("OLLAMA_URL", ollama_url).rstrip("/")
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        self.ocr_conf = float(ocr_conf)
        self.yolo_conf = float(yolo_conf)
        self.merge_iou = float(merge_iou)
        self.max_caption = int(max_caption)
        self.use_memory = bool(use_memory)
        self.ui_memory_path = Path(ui_memory_path) if ui_memory_path else None
        self._ui_memory_initialized = False
        self._ui_memory = None

        print("⏳ 加载 RapidOCR ...")
        from rapidocr_onnxruntime import RapidOCR
        self.ocr_engine = RapidOCR()
        print("✅ OCR 就绪")

        self.yolo = None
        if use_yolo:
            try:
                from ultralytics import YOLO
                self.yolo = YOLO(self.yolo_path)
                print("✅ YOLO icon_detect 加载成功")
            except Exception as exc:
                print(f"⚠️ YOLO 不可用，本次仅使用 OCR：{str(exc)[:160]}")

        self.use_qwen = False
        if use_qwen:
            try:
                response = requests.get(f"{ollama_url}/api/tags", timeout=3)
                names = [item.get("name") for item in response.json().get("models", [])]
                self.use_qwen = ollama_model in names
                if self.use_qwen:
                    print(f"✅ Qwen 图标描述模型就绪：{ollama_model}")
                else:
                    print(f"⚠️ 未找到 {ollama_model}，关闭图标描述")
            except Exception:
                print("⚠️ Ollama 未运行，关闭图标描述")

    @staticmethod
    def _iou(a, b):
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _overlap_ratio(a, b):
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        smaller = min(
            (a[2] - a[0]) * (a[3] - a[1]),
            (b[2] - b[0]) * (b[3] - b[1]),
        )
        return inter / smaller if smaller > 0 else 0.0

    @staticmethod
    def _union(a, b):
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]

    @staticmethod
    def _text_key(value):
        return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())

    @staticmethod
    def _clip_bbox(bbox, width, height):
        x1, y1, x2, y2 = [float(value) for value in bbox]
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        x1, x2 = max(0.0, min(x1, width)), max(0.0, min(x2, width))
        y1, y2 = max(0.0, min(y1, height)), max(0.0, min(y2, height))
        return [round(value, 1) for value in (x1, y1, x2, y2)]

    @staticmethod
    def _visual_signature(image, bbox, image_w, image_h):
        x1, y1, x2, y2 = [int(round(value)) for value in bbox]
        x1, x2 = max(0, min(x1, image_w)), max(0, min(x2, image_w))
        y1, y2 = max(0, min(y1, image_h)), max(0, min(y2, image_h))
        width, height = max(1, x2 - x1), max(1, y2 - y1)
        signature = {
            "aspect_ratio": round(width / height, 4),
            "relative_area": round((width * height) / max(1, image_w * image_h), 6),
        }
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return signature
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 160)
        mean_bgr = crop.reshape(-1, 3).mean(axis=0)
        signature.update({
            "mean_bgr": [round(float(value), 2) for value in mean_bgr],
            "luminance_mean": round(float(gray.mean()), 2),
            "luminance_std": round(float(gray.std()), 2),
            "edge_density": round(float((edges > 0).mean()), 4),
        })
        return signature

    def detect_text(self, image_path):
        result, _ = self.ocr_engine(str(image_path))
        boxes = []
        for item in result or []:
            points, text, score = item
            if float(score) < self.ocr_conf:
                continue
            text = str(text).strip()
            if not text:
                continue
            xs, ys = [point[0] for point in points], [point[1] for point in points]
            boxes.append({
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "text": text,
                "score": float(score),
            })
        return boxes

    def detect_icons(self, image):
        if self.yolo is None:
            return []
        try:
            results = self.yolo.predict(image, conf=self.yolo_conf, verbose=False)
        except Exception as exc:
            print(f"⚠️ YOLO 推理失败，本次跳过图标检测：{str(exc)[:160]}")
            self.yolo = None
            return []
        boxes = []
        for item in results[0].boxes:
            x1, y1, x2, y2 = item.xyxy[0].tolist()
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            boxes.append({"bbox": [x1, y1, x2, y2], "score": float(item.conf[0])})
        return boxes

    def _caption_icon(self, image, bbox):
        height, width = image.shape[:2]
        x1, y1, x2, y2 = [int(value) for value in bbox]
        box_w, box_h = x2 - x1, y2 - y1
        center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
        half = max(box_w / 2 + max(box_w, box_h) * 0.9, box_h / 2 + max(box_w, box_h) * 0.9, 28)
        xa, ya = int(max(0, center_x - half)), int(max(0, center_y - half))
        xb, yb = int(min(width, center_x + half)), int(min(height, center_y + half))
        crop = image[ya:yb, xa:xb]
        if crop.size == 0:
            return ""

        picture = Image.fromarray(crop[..., ::-1]).resize((128, 128), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        picture.save(buffer, format="PNG")
        payload = {
            "model": self.ollama_model,
            "messages": [{
                "role": "user",
                "content": "这是软件界面图标。请只用简短、通用的功能名描述它；无法判断时只输出 UNKNOWN。不要根据任何业务领域猜测。",
                "images": [base64.b64encode(buffer.getvalue()).decode()],
            }],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 30},
        }
        try:
            response = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=60)
            label = str(response.json().get("message", {}).get("content") or "").strip()
            if not label or "unknown" in label.casefold() or any(word in label for word in ("无法", "看不清", "不确定")):
                return ""
            return label.replace("\n", "").strip("。,.， ")[:24]
        except Exception:
            return ""

    def _make_element(self, element_type, description, bbox, source, image, image_w, image_h,
                      ocr=None, icon=None, association_score=None, caption=None):
        bbox = self._clip_bbox(bbox, image_w, image_h)
        x1, y1, x2, y2 = bbox
        text = str(description or "").strip()
        if not text and element_type == "icon":
            text = "unknown_icon"

        ocr_bbox = self._clip_bbox(ocr["bbox"], image_w, image_h) if ocr else None
        icon_bbox = self._clip_bbox(icon["bbox"], image_w, image_h) if icon else None
        ocr_score = round(float(ocr["score"]), 4) if ocr else None
        icon_score = round(float(icon["score"]), 4) if icon else None

        return {
            "element_type": element_type,
            "description": text,
            "text_normalized": self._text_key(text),
            "bbox": bbox,
            "center": [round((x1 + x2) / 2, 1), round((y1 + y2) / 2, 1)],
            "relative_bbox": [
                round(x1 / max(1, image_w), 5),
                round(y1 / max(1, image_h), 5),
                round(x2 / max(1, image_w), 5),
                round(y2 / max(1, image_h), 5),
            ],
            "source": source,
            "detection_confidence": None if (ocr and icon) else (ocr_score if ocr else icon_score),
            "ocr_score": ocr_score,
            "icon_score": icon_score,
            "association_score": round(float(association_score), 4) if association_score is not None else None,
            "ocr_bbox": ocr_bbox,
            "icon_bbox": icon_bbox,
            "evidence": {
                "ocr": {"present": ocr is not None, "score": ocr_score, "bbox": ocr_bbox},
                "icon_detector": {"present": icon is not None, "score": icon_score, "bbox": icon_bbox},
                "association": {
                    "present": association_score is not None,
                    "score": round(float(association_score), 4) if association_score is not None else None,
                    "method": "max_iou_or_containment",
                },
                "vision_caption": {"present": bool(caption), "text": caption or None},
            },
            "visual_signature": self._visual_signature(image, bbox, image_w, image_h),
            "state": "unknown",
            "state_confidence": 0.0,
            "is_enabled": None,
            "is_checked": None,
            "is_required": None,
            "semantic_role": None,
            "action_type": None,
            "section_id": None,
            "prototype_id": None,
        }

    @staticmethod
    def _element_strength(element):
        values = [value for value in (element.get("ocr_score"), element.get("icon_score")) if value is not None]
        return max(values, default=0.0)

    def _fuse_generic(self, ocr_boxes, icon_boxes, image):
        image_h, image_w = image.shape[:2]
        pairs = []
        for icon_index, icon in enumerate(icon_boxes):
            for ocr_index, ocr in enumerate(ocr_boxes):
                score = max(self._iou(icon["bbox"], ocr["bbox"]), self._overlap_ratio(icon["bbox"], ocr["bbox"]))
                if score > 0:
                    pairs.append((score, icon_index, ocr_index))
        pairs.sort(reverse=True)

        used_icons, used_ocr, elements = set(), set(), []
        for score, icon_index, ocr_index in pairs:
            if score < self.merge_iou:
                break
            if icon_index in used_icons or ocr_index in used_ocr:
                continue
            icon, ocr = icon_boxes[icon_index], ocr_boxes[ocr_index]
            used_icons.add(icon_index)
            used_ocr.add(ocr_index)
            elements.append(self._make_element(
                "control", ocr["text"], self._union(icon["bbox"], ocr["bbox"]), "ocr+yolo",
                image, image_w, image_h, ocr=ocr, icon=icon, association_score=score,
            ))

        weak_by_icon = {}
        for score, icon_index, ocr_index in pairs:
            if score >= self.merge_iou or icon_index in weak_by_icon:
                continue
            weak_by_icon[icon_index] = (score, ocr_index)
        association_candidates = [{
            "icon_bbox": self._clip_bbox(icon_boxes[icon_index]["bbox"], image_w, image_h),
            "ocr_bbox": self._clip_bbox(ocr_boxes[ocr_index]["bbox"], image_w, image_h),
            "association_score": round(float(score), 4),
        } for icon_index, (score, ocr_index) in weak_by_icon.items()]

        captions_used = 0
        for icon_index, icon in enumerate(icon_boxes):
            if icon_index in used_icons:
                continue
            caption = ""
            if self.use_qwen and captions_used < self.max_caption:
                caption = self._caption_icon(image, icon["bbox"])
                captions_used += 1
            elements.append(self._make_element(
                "icon", caption or "unknown_icon", icon["bbox"], "qwen" if caption else "yolo",
                image, image_w, image_h, icon=icon, caption=caption,
            ))

        for ocr_index, ocr in enumerate(ocr_boxes):
            if ocr_index not in used_ocr:
                elements.append(self._make_element(
                    "text", ocr["text"], ocr["bbox"], "ocr",
                    image, image_w, image_h, ocr=ocr,
                ))

        kept = []
        for element in sorted(elements, key=lambda item: -self._element_strength(item)):
            if any(self._iou(element["bbox"], existing["bbox"]) > 0.85 for existing in kept):
                continue
            kept.append(element)
        kept.sort(key=lambda item: (item["bbox"][1] // 40, item["bbox"][0]))
        for element_id, element in enumerate(kept):
            element["element_id"] = element_id
        return kept, association_candidates

    def parse_observation(self, screenshot_path):
        started = time.time()
        image = cv2.imread(str(screenshot_path))
        if image is None:
            raise FileNotFoundError(f"无法读取截图：{screenshot_path}")
        image_h, image_w = image.shape[:2]

        print("⏳ OCR 文字检测 ...")
        ocr_boxes = self.detect_text(screenshot_path)
        print(f"   OCR 文字区域 {len(ocr_boxes)} 个")
        print("⏳ YOLO 图标检测 ...")
        icon_boxes = self.detect_icons(image)
        print(f"   YOLO 图标框 {len(icon_boxes)} 个")
        print("⏳ 通用几何关联 ...")
        elements, association_candidates = self._fuse_generic(ocr_boxes, icon_boxes, image)

        return {
            "schema_version": "gui_observation_v2",
            "method": "rapidocr_omniparser_generic_observer_v2",
            "image": Path(screenshot_path).name,
            "image_size": {"width": image_w, "height": image_h},
            "inference_time_seconds": round(time.time() - started, 2),
            "elements_count": len(elements),
            "type_distribution": dict(Counter(item["element_type"] for item in elements)),
            "source_distribution": dict(Counter(item["source"] for item in elements)),
            "association_candidates": association_candidates,
            "elements": elements,
        }

    def _load_memory(self):
        if self._ui_memory_initialized:
            return self._ui_memory
        self._ui_memory_initialized = True
        try:
            from src.agents.ui_memory import DEFAULT_MEMORY_PATH, UIMemoryIndex
            path = self.ui_memory_path or DEFAULT_MEMORY_PATH
            self._ui_memory = UIMemoryIndex.load(path)
            print(f"✅ UI memory 已加载：{self._ui_memory.observation_count} 个截图观察")
        except FileNotFoundError:
            print("⚠️ 未找到 UI memory，返回纯观察结果")
        except Exception as exc:
            print(f"⚠️ UI memory 加载失败，返回纯观察结果：{str(exc)[:160]}")
        return self._ui_memory

    def parse(self, screenshot_path, use_memory=None):
        result = self.parse_observation(screenshot_path)
        enabled = self.use_memory if use_memory is None else bool(use_memory)
        memory = self._load_memory() if enabled else None
        return memory.enrich(result) if memory is not None else result

    def visualize(self, result, image_path, output_path):
        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError(f"无法读取截图：{image_path}")
        colors = {
            "ocr": (0, 180, 0),
            "ocr+yolo": (0, 165, 255),
            "qwen": (255, 120, 0),
            "yolo": (130, 130, 130),
        }
        for element in result["elements"]:
            x1, y1, x2, y2 = [int(value) for value in element["bbox"]]
            color = colors.get(element["source"], (0, 200, 0))
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                image,
                f'{element["element_id"]}:{element["element_type"]}',
                (x1, max(12, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
            )
        cv2.imwrite(str(output_path), image)

    def run(self, screenshot_path, output_dir=None, use_memory=None):
        output = Path(output_dir) if output_dir else self.project_root / "quality_output"
        output.mkdir(parents=True, exist_ok=True)
        result = self.parse(screenshot_path, use_memory=use_memory)
        stem = Path(screenshot_path).stem
        (output / f"{stem}_ocr_elements.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.visualize(result, screenshot_path, output / f"{stem}_ocr_annotated.png")
        print(f"\n✅ 耗时 {result['inference_time_seconds']}s，共 {result['elements_count']} 个元素")
        print(f"📊 类型分布：{result['type_distribution']}")
        print(f"🔗 来源分布：{result['source_distribution']}")
        print(f"📁 结果：{output}")
        return result
