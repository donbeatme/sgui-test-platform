import json
import time
import base64
import requests
from pathlib import Path
from io import BytesIO
import torch
import numpy as np
import cv2
from PIL import Image, ImageEnhance, ImageFilter


class YOLOPerceptionSkill:
    """
    感知智能体 Skill（YOLO检测 + Ollama Qwen3-VL 描述）
    优化：适配思考型模型、大上下文裁剪、修复重复推理
    """

    def __init__(
        self,
        detect_model_path: str = str(Path(__file__).resolve().parents[2] / "OmniParser" / "weights" / "icon_detect" / "model.pt"),
        ollama_model: str = "qwen3-vl:32b",
        ollama_base_url: str = "http://localhost:11434",
        device: str = "cpu",
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        min_box_size: int = 4,
        max_aspect_ratio: float = 1.8,
        min_box_area: int = 16,
        caption_conf_threshold: float = 0.35,
        use_caption: bool = True
    ):
        self.device = device
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.min_box_size = min_box_size
        self.max_aspect_ratio = max_aspect_ratio
        self.min_box_area = min_box_area
        self.caption_conf_threshold = caption_conf_threshold
        self.use_caption = use_caption
        self.ollama_model = ollama_model
        self.ollama_base_url = ollama_base_url
        
        self.detect_model = torch.jit.load(detect_model_path, map_location=device)
        self.detect_model.eval()
        
        if use_caption:
            try:
                resp = requests.get(f"{ollama_base_url}/api/tags", timeout=3)
                models = [m["name"] for m in resp.json().get("models", [])]
                if ollama_model not in models:
                    print(f"⚠️  Ollama 中未找到 {ollama_model}，可用模型：{models}")
                else:
                    print(f"✅ Ollama 连接正常，使用模型：{ollama_model}")
            except Exception as e:
                print(f"⚠️  Ollama 连接失败：{e}")
        
        self.input_size = 640
        self.strides = [8, 16, 32]
        self.sigmoid = torch.nn.Sigmoid()
        # 提示词：明确要求直接输出，不要思考过程
        self.caption_prompt = """你是GUI图标识别专家。请直接输出这个图标的功能名称，3-6个中文字，不要解释，不要思考过程。
例如：保存按钮、删除图标、设置菜单、搜索按钮、关闭按钮、刷新图标。
如果无法识别，直接输出：未知图标"""

    def _letterbox(self, img, new_shape=640):
        shape = img.shape[:2]
        r = min(new_shape / shape[0], new_shape / shape[1])
        new_unpad = int(round(shape[1] * r)), int(round(shape[0] * r))
        dw, dh = new_shape - new_unpad[0], new_shape - new_unpad[1]
        dw /= 2
        dh /= 2
        if shape[::-1] != new_unpad:
            img = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        return img, r, (dw, dh)

    def _nms(self, boxes, scores, iou_threshold):
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            ovr = inter / (areas[i] + areas[order[1:]] - inter)
            inds = np.where(ovr <= iou_threshold)[0]
            order = order[inds + 1]
        return keep

    def _decode_detections(self, obj_tensor, bbox_tensor, stride):
        obj = self.sigmoid(obj_tensor[0, 0]).cpu().numpy()
        bbox = bbox_tensor[0].cpu().numpy()
        H, W = obj.shape
        detections = []
        for y in range(H):
            for x in range(W):
                conf = float(obj[y, x])
                if conf < self.conf_threshold:
                    continue
                dx, dy, w, h = bbox[:, y, x]
                cx = (x + dx) * stride
                cy = (y + dy) * stride
                x1, y1 = cx - w/2, cy - h/2
                x2, y2 = cx + w/2, cy + h/2
                detections.append([x1, y1, x2, y2, conf])
        return detections

    def _is_likely_functional_icon(self, w, h, area) -> bool:
        if area < self.min_box_area:
            return False
        aspect = max(w, h) / max(min(w, h), 1)
        if aspect > self.max_aspect_ratio:
            return False
        if aspect > 1.5 and w > h * 1.5:
            return False
        return True

    def _preprocess_icon(self, icon_img: Image.Image) -> Image.Image:
        """图标预处理：适度放大，不过度处理"""
        w, h = icon_img.size
        # 最小边放大到 64 像素，保持比例，不要强行拉到128导致失真
        if min(w, h) < 64:
            scale = 64 / min(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            icon_img = icon_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        # 轻微增强对比度
        enhancer = ImageEnhance.Contrast(icon_img)
        icon_img = enhancer.enhance(1.2)
        return icon_img

    def _image_to_base64(self, img: Image.Image) -> str:
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    def _generate_icon_caption(self, icon_image: Image.Image) -> str:
        """通过 Ollama chat 接口调用 Qwen3-VL，适配思考型模型"""
        try:
            icon_image = self._preprocess_icon(icon_image)
            img_b64 = self._image_to_base64(icon_image)
            
            # 使用 chat 接口，增加 num_predict 到 150，给思考留足够空间
            payload = {
                "model": self.ollama_model,
                "messages": [
                    {
                        "role": "user",
                        "content": self.caption_prompt,
                        "images": [img_b64]
                    }
                ],
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 150,  # 关键：给思考型模型足够的token
                    "num_ctx": 2048
                }
            }
            
            resp = requests.post(
                f"{self.ollama_base_url}/api/chat",
                json=payload,
                timeout=120
            )
            resp.raise_for_status()
            result = resp.json()
            
            # Qwen3-VL 思考型模型：优先取 message.content，为空则取 thinking 的最后部分
            caption = result.get("message", {}).get("content", "").strip()
            if not caption:
                thinking = result.get("message", {}).get("thinking", "")
                # 从思考内容中提取最后一行作为答案
                if thinking:
                    lines = [l.strip() for l in thinking.split("\n") if l.strip()]
                    if lines:
                        caption = lines[-1].strip()
            
            # 清理输出
            caption = caption.replace("\"", "").replace("'", "").replace("。", "").strip()
            # 去掉可能的前缀
            for prefix in ["答案：", "答：", "功能：", "这个图标是", "这是"]:
                if caption.startswith(prefix):
                    caption = caption[len(prefix):]
            
            if not caption or "未知" in caption or "无法" in caption or "不清楚" in caption:
                return "unknown_icon"
            return caption[:30]
            
        except Exception as e:
            print(f"  ⚠️  描述生成失败: {e}")
            return "unknown_icon"

    def parse(self, screenshot_path: str) -> dict:
        start_time = time.time()
        
        img0 = cv2.imread(screenshot_path)
        img0 = cv2.cvtColor(img0, cv2.COLOR_BGR2RGB)
        orig_h, orig_w = img0.shape[:2]
        pil_img = Image.fromarray(img0)
        
        img, ratio, (dw, dh) = self._letterbox(img0, self.input_size)
        img_tensor = torch.from_numpy(img).permute(2, 0, 1).float() / 255.0
        img_tensor = img_tensor.unsqueeze(0).to(self.device)
        
        with torch.no_grad():
            pred = self.detect_model(img_tensor)
        
        all_detections = []
        for i in range(3):
            detections = self._decode_detections(pred[i*2], pred[i*2+1], self.strides[i])
            all_detections.extend(detections)
        
        elements = []
        filtered_count = 0
        if all_detections:
            all_detections = np.array(all_detections)
            boxes, scores = all_detections[:, :4], all_detections[:, 4]
            keep = self._nms(boxes, scores, self.iou_threshold)
            boxes, scores = boxes[keep], scores[keep]
            
            for idx, (box, conf) in enumerate(zip(boxes, scores)):
                x1 = max(0, min((box[0]-dw)/ratio, orig_w))
                y1 = max(0, min((box[1]-dh)/ratio, orig_h))
                x2 = max(0, min((box[2]-dw)/ratio, orig_w))
                y2 = max(0, min((box[3]-dh)/ratio, orig_h))
                
                w, h = x2-x1, y2-y1
                area = w * h
                
                if w < self.min_box_size or h < self.min_box_size:
                    filtered_count += 1
                    continue
                if not self._is_likely_functional_icon(w, h, area):
                    filtered_count += 1
                    continue
                
                description = "low_confidence_icon"
                if self.use_caption and conf >= self.caption_conf_threshold:
                    # 关键：裁剪大上下文区域，不只裁图标本身
                    # 图标周围 40px 上下文，帮助模型理解
                    pad = 40
                    cx1 = max(0, int(x1 - pad))
                    cy1 = max(0, int(y1 - pad))
                    cx2 = min(orig_w, int(x2 + pad))
                    cy2 = min(orig_h, int(y2 + pad))
                    icon_img = pil_img.crop((cx1, cy1, cx2, cy2))
                    description = self._generate_icon_caption(icon_img)
                    print(f"  [{idx}] 置信度:{conf:.2f} → {description}")
                
                quality_level = "high" if conf >= 0.75 else "medium" if conf >= 0.5 else "low"
                elements.append({
                    "element_id": idx,
                    "element_type": "icon",
                    "text": "",
                    "description": description,
                    "bbox": [round(float(x1),2), round(float(y1),2), round(float(x2),2), round(float(y2),2)],
                    "center": [round(float((x1+x2)/2),2), round(float((y1+y2)/2),2)],
                    "confidence": round(float(conf),4),
                    "quality_level": quality_level,
                    "is_interactive": True
                })
        
        elements.sort(key=lambda x: x["confidence"], reverse=True)
        for i, elem in enumerate(elements):
            elem["element_id"] = i
        
        valid_captions = sum(1 for e in elements if e["description"] not in ["unknown_icon", "low_confidence_icon"])
        
        return {
            "image": Path(screenshot_path).name,
            "device": self.device,
            "model": f"YOLOv9-E icon_detect + Ollama {self.ollama_model}",
            "inference_time_seconds": round(time.time()-start_time, 3),
            "elements_count": len(elements),
            "filtered_count": filtered_count,
            "valid_caption_count": valid_captions,
            "elements": elements
        }

    def generate_quality_report(self, result: dict) -> dict:
        elements = result["elements"]
        high = sum(1 for e in elements if e["quality_level"] == "high")
        medium = sum(1 for e in elements if e["quality_level"] == "medium")
        low = sum(1 for e in elements if e["quality_level"] == "low")
        valid = sum(1 for e in elements if e["description"] not in ["unknown_icon", "low_confidence_icon"])
        score = round((high*1.0 + medium*0.7 + low*0.4)/len(elements)*100, 2) if elements else 0.0
        caption_score = round(valid / len(elements) * 100, 2) if elements else 0.0
        conclusion = "优秀" if score >= 85 and caption_score >= 60 else "良好" if score >= 70 else "合格" if score >= 50 else "较差"
        return {
            "quality_score": score,
            "caption_coverage": caption_score,
            "filtered_count": result.get("filtered_count", 0),
            "distribution": {"high": high, "medium": medium, "low": low},
            "conclusion": f"{conclusion}：检测质量{score}分，有效描述覆盖率{caption_score}%，过滤误检{result.get('filtered_count',0)}个",
            "suggestions": ["后续可接入DOM解析补充大元素信息"] if elements else []
        }

    def visualize_with_result(self, result: dict, screenshot_path: str, output_path: str):
        """使用已有 parse 结果生成标注图，避免重复推理"""
        img = cv2.imread(screenshot_path)
        for elem in result["elements"]:
            x1, y1, x2, y2 = [int(x) for x in elem["bbox"]]
            if elem["description"] in ["unknown_icon", "low_confidence_icon"]:
                color = (128, 128, 128)
            else:
                color = (0, 255, 0) if elem["quality_level"]=="high" else (0, 255, 255) if elem["quality_level"]=="medium" else (0, 0, 255)
            cv2.rectangle(img, (x1,y1), (x2,y2), color, 2)
            label = elem["description"][:10] if elem["description"] not in ["unknown_icon", "low_confidence_icon"] else "?"
            cv2.putText(img, label, (x1, y1-8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        cv2.imwrite(output_path, img)

    def run(self, screenshot_path: str, output_dir: str = str(Path(__file__).resolve().parents[2] / "quality_output")):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        name = Path(screenshot_path).stem
        result = self.parse(screenshot_path)
        report = self.generate_quality_report(result)
        with open(output_dir / f"{name}_elements.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(output_dir / f"{name}_quality_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        # 使用已有结果生成标注图，不重复推理
        self.visualize_with_result(result, screenshot_path, str(output_dir / f"{name}_annotated.png"))
        print(f"\n✅ 推理完成，耗时 {result['inference_time_seconds']} 秒")
        print(f"🎯 检测图标 {result['elements_count']} 个（过滤误检 {result['filtered_count']} 个）")
        print(f"✨ 有效描述 {result['valid_caption_count']} 个，覆盖率 {report['caption_coverage']}%")
        print(f"⭐ 质量评分 {report['quality_score']}")
        print(f"📁 结果已保存到 {output_dir}")
        return result, report
