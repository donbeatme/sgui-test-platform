import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.agents.perception_yolo import YOLOPerceptionSkill

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legacy perception experiment")
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    screenshot = str(args.input.resolve())
    
    skill = YOLOPerceptionSkill(
        device="cpu",
        conf_threshold=0.25,
        min_box_size=4,
        iou_threshold=0.45,
        caption_conf_threshold=0.30,  # 0.30覆盖更多元素，0.35更精准
        use_caption=True
    )
    
    result, report = skill.run(screenshot)
    
    print("\n" + "="*70)
    print("🎯 完整元素清单：")
    print("="*70)
    for e in result["elements"]:
        desc = e["description"]
        if desc not in ["unknown_icon", "low_confidence_icon"]:
            print(f"[{e['element_id']:2d}] 置信度:{e['confidence']:.2f} | {desc}")
            print(f"      坐标: {e['bbox']}")
        else:
            print(f"[{e['element_id']:2d}] 置信度:{e['confidence']:.2f} | {desc}")
