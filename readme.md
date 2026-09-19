# SGUI 自动化测试平台

SGUI 是一个面向需求分析、测试用例生成与自动化执行的测试平台。通过需求文档和可选的 UI 图片生成用例草稿，经评审后入库，并统一管理测试模块、执行任务与测试结果。

[下载源码](https://github.com/donbeatme/sgui-test-platform/archive/refs/heads/main.zip) · [反馈问题](https://github.com/donbeatme/sgui-test-platform/issues) · [许可证](LICENSE)

## 主要功能

- **图文生成用例**：上传需求文档，可补充 UI 图片；支持 AI 任务命名、业务模块划分、用例生成和来源追溯。
- **用例评审与管理**：编辑草稿、执行规则检查、确认入库，按项目和模块维护用例，支持导入与导出。
- **需求与知识库**：管理需求资料，通过文档解析和知识检索为测试分析提供上下文。
- **接口自动化**：管理接口、环境、测试用例与执行任务，查看执行结果。
- **UI 自动化**：管理页面、元素和操作步骤，通过独立执行器运行测试并记录截图、Trace 和执行结果。
- **模型与工具配置**：分别配置文字生成和图片分析模型，使用 MCP 工具、技能库及定时任务扩展测试流程。

AI 生成需要配置可用的模型 API；图片分析需要支持图片输入的模型。普通项目和用例管理不依赖模型 API。生成的用例应经过评审，UI 自动化执行还需要配置可执行步骤与被测系统环境。

## 技术组成

| 部分 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Arco Design |
| 后端 | Django、Django REST Framework |
| 后台任务 | Celery、Redis |
| 数据存储 | PostgreSQL、Qdrant |
| AI 与工具 | LangChain / LangGraph、MCP |
| 浏览器自动化 | Playwright |
| 部署 | Docker Compose、Nginx |

## 环境要求

- **Docker + Compose V2**：Windows 可使用启用 WSL2 和 Linux 容器的 Docker Desktop。
- **Node.js 24 与 npm**：用于安装依赖、构建前端。
- **Python 3.11+（可选）**：仅独立 UI 执行器需要，后端 Python 环境由容器提供。
- **内存**：建议 16 GB 或以上，根据并发任务和浏览器数量调整资源。
- **网络**：首次部署需要访问镜像仓库、npm 和 Python 软件源；AI 功能需要访问配置的模型服务。

## 快速部署

以下命令从项目根目录执行。部署使用 [`local/compose.yml`](local/compose.yml)，其中包含 SGUI 工作台所需的配置。

### 1. 获取源码

```bash
git clone https://github.com/donbeatme/sgui-test-platform.git
cd sgui-test-platform
```

也可下载 ZIP，解压后在该目录打开终端。

### 2. 配置环境变量

首次部署时，将配置模板复制为 `local/.env`。

Windows PowerShell：

```powershell
Copy-Item local/.env.example local/.env
```

Linux / macOS：

```bash
cp local/.env.example local/.env
```

使用文本编辑器打开 `local/.env`，按需填写以下配置；已有部署应保留原配置。

| 配置项 | 说明 |
| --- | --- |
| `POSTGRES_PASSWORD` | 数据库初始化密码，替换模板占位值 |
| `DJANGO_ADMIN_USERNAME` | 首次创建的管理员用户名 |
| `DJANGO_ADMIN_EMAIL` | 管理员邮箱 |
| `DJANGO_ADMIN_PASSWORD` | 管理员初始密码，替换模板占位值 |
| `DJANGO_SECRET_KEY` | Django 签名密钥，使用独立的长随机字符串 |
| `WHARTTEST_API_KEY` | MCP 与后端通信的密钥，使用独立的长随机字符串 |

可使用以下命令生成随机密钥，每个密钥分别生成：

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

模板已指定容器镜像版本。模型服务的 API 地址和 API Key 在登录平台后配置；未使用 MeterSphere 时，保留 `MS_*` 配置为空即可。

### 3. 构建前端

```bash
cd Vue
npm ci
npm run build
cd ..
```

Windows PowerShell 如果阻止运行 `npm.ps1`，将上述 `npm` 替换为 `npm.cmd`。

构建完成后应生成 `Vue/dist/index.html`，该目录由前端容器挂载。

### 4. 启动服务

先确保 Docker 正常运行，再创建 Compose 使用的外部数据卷并启动服务：

```bash
docker info
docker volume create guicase-next_postgres-data
docker volume create guicase-next_qdrant-data
docker compose --env-file local/.env -p guicase-next -f local/compose.yml config --quiet
docker compose --env-file local/.env -p guicase-next -f local/compose.yml up -d --wait --wait-timeout 600
docker compose --env-file local/.env -p guicase-next -f local/compose.yml ps
```

首次启动会下载镜像、安装 PDF 解析补充依赖、执行数据库迁移并创建管理员，可能需要几分钟。已经存在的数据卷会被复用。

### 5. 登录平台

打开 [http://localhost:8778](http://localhost:8778)，使用 `local/.env` 中配置的管理员账号登录。

管理员仅在账号不存在时创建。修改环境变量中的初始密码不会重置已有账号，重置方法见下方「日常维护」。

## 使用流程

1. **创建项目**：登录后，在项目管理中创建项目并进入工作台。
2. **配置模型**：填写模型名称、API 地址和 API Key，测试连接并激活；需要图片分析时配置支持图片输入的模型。
3. **准备资料**：在图文用例工作台上传需求文档，可添加对应的 UI 图片和补充说明。
4. **生成草稿**：选择文字生成、图片分析等阶段使用的模型，提交任务并查看进度。
5. **评审入库**：核查模块划分、前置条件、步骤、预期结果和来源引用，修改后确认入库。
6. **管理与执行**：在用例管理中检索、编辑和导出正式用例；根据需要配置接口测试或 UI 自动化执行。

仅文档生成侧重需求规则与业务流程；加入图片后可补充界面控件、选项和页面状态等信息。图片分析结果同样需要人工核对。

## UI 自动化执行器

执行器运行在能够访问被测系统的机器上。仅使用文档生成和用例管理时，可以跳过此部分。

### 安装

在项目根目录创建独立 Python 环境并安装依赖。

Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r Actuator/requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item Actuator/config.example.toml local/actuator-custom.toml
```

Linux / macOS（`python3` 需为 3.11 或以上）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r Actuator/requirements.txt
.venv/bin/python -m playwright install chromium
cp Actuator/config.example.toml local/actuator-custom.toml
```

Linux 若缺少浏览器系统依赖，可执行 `.venv/bin/python -m playwright install --with-deps chromium`。

### 配置与启动

编辑 `local/actuator-custom.toml` 中已有的 `[server]` 段，替换为实际平台地址和账号：

```toml
[server]
api_url = "http://127.0.0.1:8778"
ws_url = "ws://127.0.0.1:8778/ws/ui/actuator/"
use_gui = false
api_username = "your-username"
api_password = "your-password"
```

在无桌面环境运行时，将 `[browser]` 中的 `headless` 设置为 `true`。截图、Trace 和浏览器资料目录可在同一配置文件中调整。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe Actuator/main.py --no-gui --config local/actuator-custom.toml
```

Linux / macOS：

```bash
.venv/bin/python Actuator/main.py --no-gui --config local/actuator-custom.toml
```

保持执行器进程运行，在平台「UI 自动化 → 执行器」中确认在线。按 `Ctrl+C` 停止执行器。执行器配置包含账号信息，不应提交到仓库。

## 日常维护

以下命令从项目根目录执行：

```bash
# 查看状态
docker compose --env-file local/.env -p guicase-next -f local/compose.yml ps

# 停止服务，保留数据
docker compose --env-file local/.env -p guicase-next -f local/compose.yml stop

# 启动服务或应用 Compose 配置变更
docker compose --env-file local/.env -p guicase-next -f local/compose.yml up -d --wait --wait-timeout 600

# 重启服务进程
docker compose --env-file local/.env -p guicase-next -f local/compose.yml restart

# 查看启动日志
docker compose --env-file local/.env -p guicase-next -f local/compose.yml logs --tail 100 backend frontend

# 查看后台进程状态
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend supervisorctl status

# 查看后端错误日志
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend tail -n 80 /var/log/django_err.log

# 交互式重置密码，末尾替换为实际用户名
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend python manage.py changepassword admin
```

修改前端后需重新执行 `npm run build`；修改后端后需重启 `backend`。更新源码或执行数据库迁移前，先备份数据库和上传资料。

## 数据与访问配置

| 位置 | 内容 |
| --- | --- |
| `data/` | 上传资料、聊天记录、解析缓存和运行产物 |
| `guicase-next_postgres-data` 卷 | 账号、项目、模块、用例、生成任务和执行记录等 |
| `guicase-next_qdrant-data` 卷 | 知识库向量数据 |
| `guicase-next_redis-data` 卷 | 任务队列及任务结果 |
| `guicase-next_backend-static` 卷 | 可重新生成的后端静态资源 |

Docker 命名卷由 Docker 管理，独立于项目的 `data/` 目录。迁移部署时，需要同时处理 PostgreSQL、Qdrant、`data/` 和私有配置，不能仅复制源码。

默认仅开放回环地址：前端 `8778`、后端 `8779`。需要局域网或服务器访问时，请同步调整 `local/compose.yml` 中的端口绑定、`DJANGO_ALLOWED_HOSTS`、CORS/CSRF 来源，以及执行器的平台地址。

## 项目结构

```text
sgui-test-platform/
├── Vue/                  # 前端
├── Django/               # 后端及 guicase_workbench 工作台模块
├── Actuator/             # 独立 UI 自动化执行器
├── MCP/                  # 工具服务源码与配置
├── Skills/               # 技能库
├── WeixinPluginHost/     # 可选微信插件，默认部署未启用
├── local/                # SGUI Compose 配置、启动入口与环境变量模板
├── data/                 # 运行时生成的数据目录
├── LICENSE
├── NOTICE
└── readme.md
```

根目录的 `docker-compose.yml` 和 `docker-compose.local.yml` 为保留的上游方案。部署本文介绍的工作台时，请使用 `local/compose.yml`。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| Docker 无法连接或命名管道不存在 | 启动 Docker，确认 `docker info` 正常；Windows 检查 Linux 容器引擎 |
| 提示外部数据卷不存在 | 执行快速部署中的两条 `docker volume create` 命令 |
| 页面空白或返回 403 | 检查 `Vue/dist/index.html` 是否存在，重新构建前端并检查容器状态 |
| 后端一直等待或 unhealthy | 检查后端日志和网络，首次启动可能正在安装解析依赖或执行迁移 |
| 修改 `.env` 后密码未生效 | 已有数据库和账号密码不会随初始化变量自动改变；管理员使用 `changepassword` 重置 |
| AI 生成失败 | 检查模型地址、API Key、连接测试、任务失败信息和 Celery worker 状态 |
| 图片无法解析 | 确认图片分析阶段所选模型支持图片输入，检查文件和任务错误信息 |
| 执行器离线 | 检查平台地址、账号密码、网络和执行器进程 |
| 浏览器无法启动 | 安装 Chromium 及系统依赖；无桌面环境使用无头模式 |

## 反馈与许可

欢迎通过 [Issues](https://github.com/donbeatme/sgui-test-platform/issues) 反馈问题。请提供操作系统、部署方式、复现步骤和已脱敏的错误日志。

本项目基于 [WHartTest](https://github.com/MGdaasLab/WHartTest) 扩展了 SGUI 图文用例工作台及界面，保留原许可证、版权信息与第三方声明。

使用和分发请阅读 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。原许可证除 Apache 2.0 引用外，还包含品牌、标识和来源说明修改等附加条件；本仓库不替代这些条件或授予原权利人之外的许可。
