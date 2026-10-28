import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.agents.layout_output import (
    print_partition_summary,
    write_partitioned_output,
)
from src.agents.perception_ocr import OCRPerceptionSkill
from src.agents.control_type_classifier import classify_elements


def export_partitioned_output(input_path, output_dir, result):
    output, output_path = write_partitioned_output(
        input_path=input_path,
        output_dir=output_dir,
        result=result,
    )
    print_partition_summary(output)
    print(f"\n✅ 正式感知输出：{output_path}")
    return output_path


def run_visual_memory(args, parser):
    if args.no_yolo:
        parser.error("--visual-memory 需要 YOLO 图标候选，不能与 --no-yolo 同时使用。")

    if args.memory:
        parser.error("--visual-memory 当前不能与 --memory 同时使用。")

    if args.qwen:
        parser.error("--visual-memory 当前不能与 --qwen 同时使用。")

    tool = ROOT / "tools" / "query_visual_memory.py"
    if not tool.is_file():
        parser.error(f"找不到视觉记忆工具：{tool}")

    debug_dir = args.output_dir / "_debug"
    command = [
        sys.executable,
        str(tool),
        "--input", str(args.input),
        "--visual-memory-path", str(args.visual_memory_path),
        "--output-dir", str(debug_dir),
        "--top-k", str(args.visual_top_k),
        "--batch-size", str(args.visual_batch_size),
    ]

    print("⏳ 启用视觉记忆调试模式 ...")
    return_code = subprocess.run(command, check=False).returncode
    if return_code != 0:
        return return_code

    detailed_path = debug_dir / f"{args.input.stem}_ocr_elements_visual_memory.json"
    result = json.loads(detailed_path.read_text(encoding="utf-8"))

    result["control_typing_context"] = classify_elements(
        args.input,
        result["elements"],
    )

    export_partitioned_output(args.input, args.output_dir, result)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "test.png")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "quality_output")

    parser.add_argument("--memory", action="store_true")
    parser.add_argument(
        "--memory-path",
        type=Path,
        default=ROOT / "ui_memory" / "prototypes_v2.json",
    )
    parser.add_argument("--qwen", action="store_true")
    parser.add_argument("--no-yolo", action="store_true")

    parser.add_argument("--visual-memory", action="store_true")
    parser.add_argument(
        "--visual-memory-path",
        type=Path,
        default=ROOT / "ui_memory" / "visual_memory_v2_224",
    )
    parser.add_argument("--visual-top-k", type=int, default=3)
    parser.add_argument("--visual-batch-size", type=int, default=8)

    args = parser.parse_args()
    args.input = args.input.resolve()
    args.output_dir = args.output_dir.resolve()

    if args.visual_memory:
        return run_visual_memory(args, parser)

    # 详细检测结果仅保存于 _debug，不属于正式感知输出。
    debug_dir = args.output_dir / "_debug"

    skill = OCRPerceptionSkill(
        ollama_model="qwen3-vl:32b",
        use_yolo=not args.no_yolo,
        use_qwen=args.qwen,
        use_memory=args.memory,
        ui_memory_path=args.memory_path,
        ocr_conf=0.5,
        yolo_conf=0.35,
    )
    result = skill.run(
        args.input,
        output_dir=debug_dir,
        use_memory=args.memory,
    )

    export_partitioned_output(args.input, args.output_dir, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
