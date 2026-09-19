# SGUI 自动化测试平台

公开源码包含部署所需的前端、后端、执行器和配置模板。首次下载的用户请直接阅读下方「二、其他电脑：首次部署」；「一」记录的是原作者电脑的日常启动方式。真实账号、模型密钥、业务数据、依赖缓存和构建产物不随源码分发。

下载方式：[下载 ZIP](https://github.com/donbeatme/sgui-test-platform/archive/refs/heads/main.zip)，或在 PowerShell 中克隆：

```powershell
git clone https://github.com/donbeatme/sgui-test-platform.git D:\SGUI\guicase-next
cd D:\SGUI\guicase-next
```

使用 ZIP 时，可将解压目录放到上述路径后按部署步骤操作。

此目录是当前运行在 **http://127.0.0.1:8778** 的 SGUI 平台源码，实际路径为 `D:\wharttest\guicase-next`。它包含 Vue 前端、Django 后端和自动化服务，不是纯前端项目。

平台支持项目和模块管理、需求文档管理、文档/图片生成测试用例、草稿编辑与入库、接口测试、UI 自动化、知识库和任务调度。AI 生成功能需要配置可用的大模型 API；图片分析还需要支持图片输入的模型。

## 目录结构

```text
guicase-next/
├── Vue/                  # 前端源码，构建结果在 dist/
├── Django/               # 后端源码
│   ├── guicase_workbench/ # SGUI 图文用例工作台
│   ├── workbench_settings.py
│   └── workbench_urls.py
├── Actuator/             # UI 自动化执行器
├── MCP/                  # 平台工具与浏览器集成配置/源码
├── Skills/               # 随项目提供的技能
├── WeixinPluginHost/     # 可选的微信插件服务，当前 local 部署未启用
├── local/
│   ├── compose.yml       # 当前 SGUI 部署入口
│   ├── .env.example      # 新部署配置模板，不含真实凭据
│   ├── .env              # 本机私有配置，不提交、不随源码分发
│   ├── entrypoint.sh     # PDF 依赖、数据库迁移和服务启动
│   ├── bootstrap.py      # 首次管理员和平台工具配置初始化
│   ├── nginx.conf
│   ├── Manage-Workbench.ps1
│   ├── Start-Actuator.ps1
│   └── Stop-Actuator.ps1
├── data/                 # 上传文件、缓存、聊天记录、截图等运行数据
└── readme.md
```

2026-09-19 已移除上述目录原来的 `WHartTest_` 前缀，并同步修改本地挂载、启动脚本、构建配置和跨目录路径。Python 包名 `wharttest_django`、环境变量、已有数据库/卷名及可执行文件名保持兼容；它们不是本次重命名的目录。

**部署本版 SGUI 使用 `local/compose.yml`。** 根目录的 `docker-compose.yml`、`docker-compose.local.yml` 是保留的上游部署方案，不是当前工作台的启动入口。

## 一、这台电脑：日常启动与停止

在 Windows「开始」菜单打开 **PowerShell**，粘贴以下命令。本机已经具备 Docker、前端构建结果、数据库和执行器环境，不需要重复初始化。

启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\wharttest\guicase-next\local\Manage-Workbench.ps1" -Action Start
```

脚本会启动 Docker、当前平台和本机 UI 执行器；为控制内存，会停止原来独立部署的旧平台。完成后打开：

- 工作台：http://127.0.0.1:8778/workbench
- 登录页：http://127.0.0.1:8778/login

查看状态、停止、重新构建前端：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\wharttest\guicase-next\local\Manage-Workbench.ps1" -Action Status
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\wharttest\guicase-next\local\Manage-Workbench.ps1" -Action Stop
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\wharttest\guicase-next\local\Manage-Workbench.ps1" -Action Build
```

需要重启时，先执行 `Stop`，再执行 `Start`。修改前端代码后先 `Build`，完成后刷新浏览器。修改后端代码或 Compose 配置后重新启动服务。

本机 Docker 程序在 `D:\wharttest\runtime\docker-desktop\resources\bin\docker.exe`，Node.js 在 `D:\nodejs`，UI 执行器虚拟环境在 `D:\wharttest\runtime\actuator-venv`。上述 PowerShell 脚本是**这台电脑的环境脚本**，复制到其他电脑后请使用下面的通用部署步骤。

## 二、其他电脑：首次部署

### 1. 准备环境和源码

- 安装 Docker Engine + Compose V2；Windows 可使用启用 WSL2/Linux 容器的 Docker Desktop，无需另装双系统。
- 安装 Node.js 和 npm。本项目本次验证使用 Node.js `24.11.1`、npm `11.17.0`。
- 建议使用 16 GB 或以上内存的电脑；多个服务、浏览器和模型请求会共同占用内存。
- 仅在需要运行独立 UI 执行器时安装 Python 3.11 或以上。平台后端的 Python 环境包含在 Docker 镜像内。
- 能够访问镜像仓库、npm 和 Python 软件源；AI 功能还需可用的模型服务。

将**本仓库的整套源码**放入例如 `D:\SGUI\guicase-next`。不要重新拉取上游原版当作本次融合平台。再次分发源码时，排除 `node_modules`、虚拟环境、私有 `.env`、`login-credentials.txt`、执行器账号配置、业务 `data`、`local/test-output` 和历史备份目录。本仓库已排除这些本机运行内容。

需要把环境和数据放在 D 盘时：源码放在 D 盘；在 Docker Desktop 设置中将磁盘映像存储位置设置到 D 盘；npm 缓存、Python 虚拟环境和 Playwright 浏览器也使用下面示例中的 D 盘路径。**Docker 命名卷位于 Docker 的磁盘映像中，不在源码的 `data` 文件夹内。**

以下部署命令在 **PowerShell** 中输入，从项目根目录执行。先启动 Docker Desktop，并确认 `docker info` 正常返回：

```powershell
cd D:\SGUI\guicase-next
docker info
docker compose version
node --version
npm.cmd --version
```

### 2. 创建配置

仅首次部署执行；已有 `.env` 时保留现有配置：

```powershell
Copy-Item .\local\.env.example .\local\.env
notepad .\local\.env
```

至少将以下占位值替换为自己的值：

| 配置项 | 含义 |
| --- | --- |
| `POSTGRES_PASSWORD` | PostgreSQL 首次初始化密码 |
| `DJANGO_ADMIN_USERNAME` | 首次创建的管理员用户名 |
| `DJANGO_ADMIN_PASSWORD` | 首次创建的管理员密码 |
| `DJANGO_ADMIN_EMAIL` | 管理员邮箱 |
| `DJANGO_SECRET_KEY` | 长随机字符串，用于 Django 签名 |
| `WHARTTEST_API_KEY` | 长随机字符串，用于本地 MCP 与后端通信 |

可以使用 `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"` 生成随机值，每个密钥分别生成。此模板已经固定了本机使用的镜像摘要；首次启动需要下载这些镜像。

**账号创建完成后，修改 `.env` 的管理员密码不会自动重置数据库中的已有密码。** 忘记密码时使用本文后面的重置命令。

### 3. 安装并构建前端

```powershell
cd .\Vue
npm.cmd ci --cache ..\.cache\npm
npm.cmd run build
cd ..
```

检查生成了 `Vue\dist\index.html`。Nginx 挂载的是此目录；没有构建结果时平台页面无法正常显示。

### 4. 创建数据卷并启动

当前 Compose 声明 PostgreSQL、Qdrant 为外部卷，首次部署需要先创建；已经存在时下列命令会复用它们。

```powershell
docker volume create guicase-next_postgres-data
docker volume create guicase-next_qdrant-data
docker compose --env-file local/.env -p guicase-next -f local/compose.yml config --quiet
docker compose --env-file local/.env -p guicase-next -f local/compose.yml up -d --wait --wait-timeout 600
docker compose --env-file local/.env -p guicase-next -f local/compose.yml ps
```

第一次后端启动会安装 PDF 解析补充依赖、执行数据库迁移、创建管理员并配置工具服务，等待时间可能较长。成功后访问 http://127.0.0.1:8778 ，用 `.env` 中配置的管理员登录，在「项目管理」中新建项目。

Windows 使用 `npm.cmd` 可以避免 PowerShell 的 `npm.ps1` 执行策略问题。Linux/macOS 的 Docker 和 npm 步骤相同，将 `Copy-Item` 改为 `cp`、`npm.cmd` 改为 `npm`、目录改为实际路径即可。Windows 专用的 `Manage-Workbench.ps1` 不作为跨平台启动入口。

### 5. 配置 AI 并使用工作台

1. 登录后创建或选择项目。
2. 在模型配置中填写模型名称、API 地址和 API Key，测试连接并激活。
3. 打开「图文用例工作台」，上传需求文档；图片为可选材料。
4. 图片分析阶段选择支持图片输入的模型；文字生成阶段选择可用的文本模型。可以使用不同模型。
5. 生成完成后检查草稿的模块、前置条件、步骤和预期结果，修改后确认入库。
6. 在「用例管理」中查看正式用例；实际 UI 自动化还需启动执行器、配置可执行步骤和被测系统环境。

新部署不会附带本机已有的项目、模型 API Key、上传资料和生成记录。没有配置模型 API 时，普通项目/用例管理仍可使用，AI 生成无法调用模型。

## 三、UI 自动化执行器（可选）

只需要生成和管理用例时，可以先跳过本节。本机日常 `Start` 已包含执行器启动。

其他 Windows 电脑可单独准备执行器环境。以下仍在项目根目录执行；虚拟环境和浏览器均放在 D 盘：

```powershell
py -3.11 -m venv D:\SGUI\runtime\actuator-venv
$python = 'D:\SGUI\runtime\actuator-venv\Scripts\python.exe'
$env:PIP_CACHE_DIR = 'D:\SGUI\cache\pip'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\SGUI\runtime\playwright-browsers'
& $python -m pip install -r .\Actuator\requirements.txt
& $python -m playwright install chromium
Copy-Item .\Actuator\config.example.toml .\local\actuator-custom.toml
notepad .\local\actuator-custom.toml
```

将 `server.api_url` 改为 `http://127.0.0.1:8778`，`server.ws_url` 改为 `ws://127.0.0.1:8778/ws/ui/actuator/`，设置 `use_gui = false` 并填写实际平台账号密码。浏览器资料、截图和 Trace 路径建议配置为本项目 `data/actuator/` 下的绝对路径。需要无界面运行时设置 `browser.headless = true`。

启动（每次在同一个 PowerShell 会话中设置浏览器路径）：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\SGUI\runtime\playwright-browsers'
& 'D:\SGUI\runtime\actuator-venv\Scripts\python.exe' .\Actuator\main.py --no-gui --config .\local\actuator-custom.toml
```

窗口保持运行，在平台「UI 自动化 → 执行器」确认在线；按 `Ctrl+C` 停止。独立执行器配置包含账号密码，不要提交或分发。

## 四、服务、数据与日常维护

| 服务 | 作用 | 本机入口/数据 |
| --- | --- | --- |
| `frontend` | SGUI 前端、API/WebSocket 反向代理 | `127.0.0.1:8778` |
| `backend` | Django、Celery worker、定时调度 | `127.0.0.1:8779` |
| `postgres` | 账号、项目、模块、正式用例、工作台任务等 | 卷 `guicase-next_postgres-data` |
| `redis` | 任务队列和任务结果 | 卷 `guicase-next_redis-data` |
| `qdrant` | 知识库向量数据 | 卷 `guicase-next_qdrant-data` |
| `mcp` | 平台工具服务 | 容器网络内部访问 |
| `playwright-mcp` | Agent 浏览器工具 | 容器网络内部访问 |

源文件上传、PDF 解析依赖和缓存、聊天 SQLite、UI 截图等存放于项目 `data/`。静态后端资源位于 `guicase-next_backend-static` 卷，可重新生成。仅复制源码和 `data/` **不能**迁移完整业务数据库；迁移需同时备份 PostgreSQL、Qdrant、`data/` 和私有配置。暂停新任务并等待当前任务完成后再做一致性备份。

通用维护命令，在项目根目录的 PowerShell 中执行：

```powershell
# 状态
docker compose --env-file local/.env -p guicase-next -f local/compose.yml ps
# 停止，保留容器和数据
docker compose --env-file local/.env -p guicase-next -f local/compose.yml stop
# 再次启动；也会应用 Compose 挂载/配置的变更
docker compose --env-file local/.env -p guicase-next -f local/compose.yml up -d --wait --wait-timeout 600
# 查看启动日志
docker compose --env-file local/.env -p guicase-next -f local/compose.yml logs --tail 100 backend frontend
# 查看 Web 服务/后台任务状态及后端错误日志
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend supervisorctl status
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend tail -n 80 /var/log/django_err.log
# 重置管理员密码，会提示交互输入；用户名不是 admin 时替换末尾参数
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend python manage.py changepassword admin
```

在这台电脑中若提示 `docker` 命令不存在，可先执行：

```powershell
$env:Path = 'D:\wharttest\runtime\docker-desktop\resources\bin;D:\nodejs;' + $env:Path
cd D:\wharttest\guicase-next
```

目前端口只绑定本机回环地址。其他人需要局域网访问时，还要相应配置 Compose 端口绑定、Django 允许主机、CORS/CSRF 来源和防火墙；默认命令用于本地部署。

## 五、常见问题

| 现象 | 检查方法 |
| --- | --- |
| Docker 提示命名管道或 engine 不存在 | 先启动 Docker Desktop，确认 Linux 容器引擎和 `docker info` 正常；本机可使用日常 `Start` 脚本 |
| `external volume ... not found` | 执行首次部署步骤中的两条 `docker volume create` |
| 页面空白或 Nginx 403 | 检查 `Vue/dist/index.html`，重新 `npm run build`；检查前端容器状态 |
| 后端一直等待或 unhealthy | 查看 `logs backend` 和 `supervisorctl status`；首次 PDF 依赖安装需软件源可达 |
| PostgreSQL 密码错误 | 已有卷中的数据库密码不会因修改 `.env` 自动更改，应恢复匹配配置或按数据库流程修改密码 |
| 管理员无法登录 | 管理员只在不存在时创建；使用 `changepassword` 重置，之后同步本机执行器的登录配置 |
| AI 生成失败 | 检查模型地址、API Key、模型能力、网络、任务失败信息和后台 worker 状态 |
| 图片无法解析 | 检查所选视觉模型是否支持图片输入，不能只根据模型显示名称判断 |
| UI 执行器离线/浏览器找不到 | 检查平台地址、账号密码、执行器进程和 `PLAYWRIGHT_BROWSERS_PATH` 是否与安装时一致 |

本版本已在原部署机器完成前端构建、后端检查、登录、工作台读取与 UI 冒烟测试。运行数据和本机验证产物不随公开源码分发。

## 许可与来源

此平台基于 WHartTest 源码扩展了 SGUI 图文用例工作台及界面，保留原有许可证和版权信息。原 LICENSE 除 Apache 2.0 引用外还包含品牌/标识修改等附加条件，请阅读完整原文，并在取得所需许可后使用或分发相关改版。许可证见 [LICENSE](LICENSE)，上游项目为 https://github.com/MGdaasLab/WHartTest 。镜像名称和内部模块名称中的上游标识用于兼容，不影响当前目录结构。
