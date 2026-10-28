import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.agents.perception_e2e import E2EPerceptionSkill

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legacy perception experiment")
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    screenshot = str(args.input.resolve())
    
    skill = E2EPerceptionSkill(
        ollama_model="qwen3-vl:32b"
    )
    
    result, report = skill.run(screenshot)
    
    print("\n" + "="*70)
    print("🎯 识别到的所有可交互元素：")
    print("="*70)
    for e in result["elements"]:
        print(f"[{e['element_id']:2d}] {e['element_type']:14s} | {e['description']}")
        print(f"      坐标: {e['bbox']}  质量: {e['quality_level']}")
