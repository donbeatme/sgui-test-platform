"""Resolve browser screenshots across the host/container shared directory."""
import os
from pathlib import Path, PureWindowsPath


def resolve_screenshot_path(file_path, root=None, host_root=None):
    root = Path(root or os.getenv("SGUI_SCREENSHOT_ROOT", "/tmp/playwright-output")).resolve()
    host_root = host_root or os.getenv("SGUI_SCREENSHOT_HOST_ROOT", "")
    value = str(file_path).strip()
    if not value or "\x00" in value:
        raise ValueError("截图路径为空或无效")
    windows = PureWindowsPath(value)
    if ".." in value.replace("\\", "/").split("/"):
        raise ValueError("截图路径不能包含上级目录")
    native_path = Path(value)
    if native_path.is_absolute() and native_path.resolve().is_relative_to(root):
        candidate = native_path
    elif windows.drive:
        if not host_root or not windows.is_absolute():
            raise ValueError("未配置此 Windows 截图目录的映射，请使用截图工具返回的相对文件名")
        try:
            relative = windows.relative_to(PureWindowsPath(host_root))
        except ValueError:
            raise ValueError("截图不在已配置的 Windows 共享截图目录内") from None
        candidate = root.joinpath(*relative.parts)
    else:
        candidate = Path(value.replace("\\", "/"))
        if not candidate.is_absolute():
            candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("仅允许上传共享截图目录内的图片")
    if candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        raise ValueError("仅支持 PNG、JPEG、GIF、WebP 截图")
    if not candidate.is_file():
        raise FileNotFoundError("共享目录中未找到截图；请先截图，再传入工具返回的文件名，不要猜测其他路径")
    return candidate
