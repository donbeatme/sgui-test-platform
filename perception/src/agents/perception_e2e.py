import json
import time
import base64
import re
import requests
from pathlib import Path
from io import BytesIO
import cv2
import numpy as np
from PIL import Image


class E2EPerceptionSkill:
    """
    端到端纯视觉感知智能体
    直接使用 Qwen3-VL 整页识别所有可交互元素，无需 YOLO 检测
    输出格式与 YOLOPerceptionSkill 完全兼容，可直接对接生成智能体
    """

    def __init__(
        self,
        ollama_model: str = "qwen3-vl:32b",
        ollama_base_url: str = "http://localhost:11434",
        use_gpu: bool = True
    ):
        self.ollama_model = ollama_model
        self.ollama_base_url = ollama_base_url
        
        # 测试连接
        try:
            resp = requests.get(f"{ollama_base_url}/api/tags", timeout=3)
            models = [m["name"] for m in resp.json().get("models", [])]
            if ollama_model not in models:
                print(f"⚠️  Ollama 中未找到 {ollama_model}，可用模型：{models}")
            else:
                print(f"✅ Ollama 连接正常，使用端到端模型：{ollama_model}")
        except Exception as e:
            print(f"⚠️  Ollama 连接失败：{e}")

        # 整页识别提示词：要求输出严格 JSON
        self.detection_prompt = """你是专业的GUI界面测试分析专家。请分析这张软件界面截图，识别出**所有可交互的UI元素**。

需要识别的元素类型包括：
- button（按钮：可点击的按钮，如保存、删除、查询、新增）
- input（输入框：可输入文字的文本框、数字框）
- dropdown（下拉框：可展开选择的下拉菜单）
- checkbox（复选框：可勾选的方框）
- radio（单选框：圆形单选按钮）
- tab（标签页：页面切换标签）
- icon（图标：小型功能图标按钮）
- link（链接：可点击的文字链接）
- menu（菜单项：导航菜单、右键菜单）
- table_action（表格操作列：表格中的操作按钮）

对每个元素，输出以下信息：
1. type：元素类型，只能是上面列出的英文类型
2. description：中文功能描述，4-10个字，如"保存按钮""用户名输入框""国家地区下拉框"
3. bbox：元素边界框像素坐标 [x1, y1, x2, y2]，必须紧贴元素边缘，坐标基于图片原始像素尺寸

要求：
- 只识别可交互元素，不要识别纯文字、图片、背景、分隔线
- 坐标尽量准确，紧贴元素边缘
- 不要遗漏任何可交互元素
- 不要输出思考过程、解释、markdown代码块标记
- 必须严格输出以下JSON格式，不要输出任何其他内容：

{"elements": [{"type": "button", "description": "查询按钮", "bbox": [100, 200, 180, 240]}, ...]}"""

    def _image_to_base64(self, img_path: str) -> tuple:
        """读取图片转base64，返回(base64, 宽, 高)"""
        img = Image.open(img_path).convert("RGB")
        w, h = img.size
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8"), w, h

    def _extract_json(self, text: str) -> dict:
        """从模型输出中提取JSON，兼容代码块包裹、前后多余文字"""
        # 去除思考过程（如果content里混了思考）
        text = text.strip()
        # 尝试提取 ```json ... ``` 代码块
        json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1).strip()
        # 尝试直接找 { ... }
        brace_match = re.search(r'\{.*\}', text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0).strip()
        return json.loads(text)

    def _call_vlm(self, img_b64: str) -> str:
        """调用 Ollama Qwen3-VL"""
        payload = {
            "model": self.ollama_model,
            "messages": [
                {
                    "role": "user",
                    "content": self.detection_prompt,
                    "images": [img_b64]
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.05,  # 低温度，输出更稳定
                "num_predict": 4000,  # 整页元素多，给足token
                "num_ctx": 8192
            }
        }
        
        resp = requests.post(
            f"{self.ollama_base_url}/api/chat",
            json=payload,
            timeout=300
        )
        resp.raise_for_status()
        result = resp.json()
        
        content = result.get("message", {}).get("content", "").strip()
        # 如果content为空，从thinking中提取JSON部分
        if not content or "{" not in content:
            thinking = result.get("message", {}).get("thinking", "")
            # 从思考中找最后一个JSON块
            json_matches = re.findall(r'\{[^{}]*"elements"[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', thinking, re.DOTALL)
            if json_matches:
                content = json_matches[-1]
            elif "{" in thinking:
                # 找最后一个{到结尾
                last_brace = thinking.rfind("{")
                content = thinking[last_brace:]
        return content

    def parse(self, screenshot_path: str) -> dict:
        start_time = time.time()
        
        img_b64, orig_w, orig_h = self._image_to_base64(screenshot_path)
        
        print("⏳ Qwen3-VL 正在整页识别元素...")
        raw_output = self._call_vlm(img_b64)
        
        # 解析JSON
        try:
            parsed = self._extract_json(raw_output)
            raw_elements = parsed.get("elements", [])
            parse_status = "success"
            parse_error = None
        except Exception as e:
            print(f"⚠️  JSON解析失败: {e}")
            print(f"原始返回前500字: {raw_output[:500]}")
            raw_elements = []
            parse_status = "parse_failed"
            parse_error = str(e)
        
        # 标准化输出，兼容旧格式
        elements = []
        valid_types = {"button", "input", "dropdown", "checkbox", "radio", 
                       "tab", "icon", "link", "menu", "table_action"}
        
        for idx, elem in enumerate(raw_elements):
            try:
                bbox = elem.get("bbox", [])
                if len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = [float(v) for v in bbox]
                
                # 坐标边界修正
                x1 = max(0, min(x1, orig_w))
                y1 = max(0, min(y1, orig_h))
                x2 = max(0, min(x2, orig_w))
                y2 = max(0, min(y2, orig_h))
                
                if x2 <= x1 or y2 <= y1:
                    continue
                
                w, h = x2 - x1, y2 - y1
                area = w * h
                # 过滤异常小或异常大的元素
                if area < 20 or area > orig_w * orig_h * 0.8:
                    continue
                
                elem_type = elem.get("type", "unknown").lower().strip()
                if elem_type not in valid_types:
                    elem_type = "icon"  # 未知类型归为图标
                
                description = elem.get("description", "").strip()
                if not description:
                    description = "unknown_element"
                
                # VLM没有置信度，根据元素大小和类型给启发式质量等级
                # 大元素（按钮、输入框）质量高，小图标质量中等
                if elem_type in ("button", "input", "dropdown", "tab", "menu") and min(w, h) >= 15:
                    quality_level = "high"
                    confidence = 0.9
                elif elem_type in ("checkbox", "radio", "link", "table_action") and min(w, h) >= 10:
                    quality_level = "high"
                    confidence = 0.85
                elif min(w, h) >= 12:
                    quality_level = "medium"
                    confidence = 0.7
                else:
                    quality_level = "low"
                    confidence = 0.5
                
                elements.append({
                    "element_id": idx,
                    "element_type": elem_type,
                    "text": "",
                    "description": description,
                    "bbox": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                    "center": [round((x1+x2)/2, 2), round((y1+y2)/2, 2)],
                    "confidence": confidence,
                    "quality_level": quality_level,
                    "is_interactive": True
                })
            except Exception:
                continue
        
        # 按位置排序（从上到下，从左到右）
        elements.sort(key=lambda e: (e["bbox"][1] // 50, e["bbox"][0]))
        for i, elem in enumerate(elements):
            elem["element_id"] = i
        
        valid_descriptions = sum(1 for e in elements if e["description"] != "unknown_element")
        
        return {
            "image": Path(screenshot_path).name,
            "image_size": {"width": orig_w, "height": orig_h},
            "method": "end_to_end_qwen3vl",
            "model": f"Ollama {self.ollama_model}",
            "inference_time_seconds": round(time.time() - start_time, 3),
            "parse_status": parse_status,
            "parse_error": parse_error,
            "elements_count": len(elements),
            "valid_caption_count": valid_descriptions,
            "raw_output_length": len(raw_output),
            "elements": elements
        }

    def generate_quality_report(self, result: dict) -> dict:
        elements = result["elements"]
        high = sum(1 for e in elements if e["quality_level"] == "high")
        medium = sum(1 for e in elements if e["quality_level"] == "medium")
        low = sum(1 for e in elements if e["quality_level"] == "low")
        
        # 按类型统计
        type_counts = {}
        for e in elements:
            t = e["element_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        
        valid = result.get("valid_caption_count", 0)
        caption_score = round(valid / len(elements) * 100, 2) if elements else 0.0
        score = round((high*1.0 + medium*0.7 + low*0.4)/len(elements)*100, 2) if elements else 0.0
        
        conclusion = "优秀" if score >= 85 and caption_score >= 90 else "良好" if score >= 70 else "合格" if score >= 50 else "较差"
        
        return {
            "quality_score": score,
            "caption_coverage": caption_score,
            "distribution": {"high": high, "medium": medium, "low": low},
            "element_type_distribution": type_counts,
            "conclusion": f"{conclusion}：识别到{len(elements)}个可交互元素，描述覆盖率{caption_score}%",
            "suggestions": [
                "端到端VLM方案可识别按钮、输入框、下拉框等全类型元素",
                "建议人工抽查标注图，确认元素无遗漏、坐标准确",
                "后续可结合DOM解析做交叉验证，进一步提升准确率"
            ] if elements else ["识别失败，请检查模型输出或更换提示词"]
        }

    def visualize(self, result: dict, screenshot_path: str, output_path: str):
        """生成标注图"""
        img = cv2.imread(screenshot_path)
        
        # 不同类型用不同颜色
        type_colors = {
            "button": (0, 255, 0),      # 绿色
            "input": (255, 150, 0),     # 蓝色
            "dropdown": (0, 200, 255),  # 橙色
            "checkbox": (255, 0, 255),  # 紫色
            "radio": (255, 0, 255),
            "tab": (0, 165, 255),       # 橙色
            "icon": (128, 128, 128),    # 灰色
            "link": (255, 100, 100),
            "menu": (255, 200, 0),
            "table_action": (0, 255, 200)
        }
        
        for elem in result["elements"]:
            x1, y1, x2, y2 = [int(v) for v in elem["bbox"]]
            color = type_colors.get(elem["element_type"], (0, 255, 0))
            
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            # 标签：类型+描述
            label = f"{elem['element_type']}:{elem['description'][:10]}"
            # 标签背景
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            cv2.rectangle(img, (x1, y1-th-4), (x1+tw+2, y1), color, -1)
            cv2.putText(img, label, (x1+1, y1-3), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
        
        cv2.imwrite(output_path, img)

    def run(self, screenshot_path: str, output_dir: str = str(Path(__file__).resolve().parents[2] / "quality_output")):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        name = Path(screenshot_path).stem
        
        result = self.parse(screenshot_path)
        report = self.generate_quality_report(result)
        
        with open(output_dir / f"{name}_e2e_elements.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(output_dir / f"{name}_e2e_quality_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        self.visualize(result, screenshot_path, str(output_dir / f"{name}_e2e_annotated.png"))
        
        print(f"\n✅ 端到端识别完成，耗时 {result['inference_time_seconds']} 秒")
        print(f"🎯 识别可交互元素 {result['elements_count']} 个")
        print(f"📊 类型分布：{report['element_type_distribution']}")
        print(f"⭐ 质量评分 {report['quality_score']}，描述覆盖率 {report['caption_coverage']}%")
        print(f"📁 结果已保存到 {output_dir}")
        
        return result, report
