# 独立截图感知模块

本目录从上一版独立感知模块移入，正式入口 `run_ocr.py` 使用 RapidOCR、YOLO 图标检测与几何合并生成五分区 JSON。
`service.py` 提供带 Bearer token 的 `/recognize` 接口；`icons.py` 保留无文字控件的原图裁剪。
源码运行不依赖项目外部的 `src/`、`tools/` 或旧 OmniParser 工作目录。

## 环境与模型

在仓库根目录执行，使用独立于 Django 的 Python 3.10 环境：

```bash
python3.10 -m venv perception/.venv
perception/.venv/bin/python -m pip install -r perception/requirements-lock.txt
perception/.venv/bin/python -m pip check
python3 perception/download_models.py
python3 perception/download_models.py --check
```

`models.json` 固定模型大小与 SHA-256。也可将原机权重复制到
`perception/OmniParser/weights/icon_detect/model.pt` 后执行 `--check`。
模型、私有配置、业务截图和记忆库不进入 Git。
当前使用 Microsoft OmniParser-v2.0 的 `icon_detect` YOLOv8 权重，
其许可为 AGPL-3.0，参见 [官方模型卡](https://huggingface.co/microsoft/OmniParser-v2.0)。
`OmniParser/util/` 的来源和许可保存在该目录中。

## 与工作台连接

在已有的 `local/.env` 中增加或核对以下配置，保留数据库、账号及其余配置：

```dotenv
GUI_PERCEPTION_URL=http://host.docker.internal:18781
GUI_PERCEPTION_TOKEN=替换成64位随机十六进制字符串
GUI_PERCEPTION_QWEN=0
GUI_CONTROL_TYPING=0
GUI_ICON_MODEL=qwen3-vl:32b
GUI_CONTROL_MODEL=qwen3-vl:32b
OLLAMA_URL=http://127.0.0.1:11434
```

可用 `python3 -c 'import secrets; print(secrets.token_hex(32))'` 生成 token。
后端与感知服务必须使用同一 token，已有服务继续保留原值即可。
`local/compose.yml` 已补充 Linux `host-gateway` 映射；更改环境变量后需要重建后端容器才能生效。
新建部署可参考配置模板；升级已有平台不要直接用模板替换私有 `.env`。

启动宿主机感知服务：

```bash
perception/.venv/bin/python perception/start.py
```

`start.py` 只从 `local/.env` 读取感知相关变量，不复制数据库凭据。
可用 `--env-file`、`--host`、`--port` 指定配置与监听地址。
已运行旧感知服务时，先核对其工作目录与端口；此源码集成不会自动替换正在运行的服务。
`install_service.py` 仍可用于注册当前用户的 systemd 服务，注册操作会更新 `sgui-perception.service`。

基础模式不依赖 Ollama。若启用 Qwen，请准备模型并设置 `GUI_PERCEPTION_QWEN=1`、`GUI_CONTROL_TYPING=1`。
接口保持现有的模型不可用时降级行为；验收 Qwen 效果时应同时核对日志。

## 验证

```bash
GUI_CONTROL_TYPING=0 perception/.venv/bin/python perception/run_ocr.py \
  --input perception/examples/smoke.png --output-dir /tmp/sgui-perception-smoke
perception/.venv/bin/python perception/smoke.py --input perception/examples/smoke.png
perception/.venv/bin/python -m unittest perception.test_service perception.test_icons -v
```

HTTP 检查需要服务已启动，并且与 `local/.env` 中的 token 一致。
`run_perception.py`、`run_e2e_perception.py` 和 `tools/` 保留为历史实验入口；
正式工作台使用 `run_ocr.py`，可选视觉记忆环境见 `requirements-memory.txt`。
`run_perception.py` 的 TorchScript 模型格式与正式 YOLOv8 权重不同，不能直接互换。

工作台接入和流程说明见 [感知集成说明](../docs/perception-integration.md)。
