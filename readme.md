# SGUI 自动化测试平台

SGUI 是一个面向需求分析、测试用例生成与自动化执行的测试平台。支持使用需求文档、UI 图片、文字说明或它们的组合生成用例草稿，经评审后入库，并统一管理测试模块、执行任务与测试结果。

[下载源码](https://github.com/donbeatme/sgui-test-platform/archive/refs/heads/main.zip) · [反馈问题](https://github.com/donbeatme/sgui-test-platform/issues) · [许可证](LICENSE)

## 主要功能

- **图文生成用例**：上传新文档、选用已有文档、添加 UI 图片或文字说明；支持 AI 任务命名、业务模块划分、控件核对、用例生成和来源追溯。
- **LLM 执行用例**：根据用例步骤和项目入口操作网页，保存执行报告、步骤结果与截图。
- **Playwright 脚本**：从用例逐步实测生成代码，完成独立复跑后可运行、下载；也可从 LLM 执行记录整理脚本。
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
3. **准备资料**：在图文用例工作台点击“选择需求文档”，可上传新文档、选用当前项目已有文档，或混合多选。文档、图片、需求说明均为可选，但至少提供一种；只有任务名称或空白说明时，生成按钮禁用。也支持仅上传图片或仅填写文字需求。弹窗取消不改变原选择，选好的材料可移除、清空、撤销与重做，已有文档不会因此被删除。
4. **生成草稿**：选择文字生成、图片分析等阶段使用的模型，提交任务并查看进度。
5. **评审入库**：核查模块划分、前置条件、步骤、预期结果和来源引用，修改后确认入库。
6. **管理与执行**：在用例管理中检索、编辑和导出正式用例。“来源任务”列显示生成任务名称，点击可返回对应生成记录；没有关联任务的手工或历史用例显示“—”。根据需要配置接口测试或 UI 自动化执行。
7. **生成与运行脚本**：在用例管理点击“生成脚本”，后台逐步操作网页并记录成功语句，完成代码检查后，在新的独立浏览器环境中完整复跑一次。无执行错误即显示“复跑通过”，可运行或下载；这表示脚本可以重跑，不代表业务覆盖已经完备。失败修复在生成阶段处理，原预期和历史记录保留。普通脚本运行不调用 LLM；也支持从 LLM 执行记录保存代码。详见 [Playwright 脚本使用说明](docs/playwright-replay.md)。

仅文档生成侧重需求规则与业务流程；加入图片后可补充界面控件、选项和页面状态等信息。图片分析结果同样需要人工核对。

## UI 自动化执行器

用例管理中的 AI「执行」通过模型调用远程 MCP 浏览器工具。Windows 部署也可以选用本机 Edge，配置方法见 [Windows Edge 浏览器工具](local/BrowserTools.md)。下方执行器用于固定步骤的 UI 自动化，是另一条执行路径。

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


## AI 用例执行与结果追踪

1. 选择项目，配置可用的 LLM 和浏览器 MCP 工具，并在项目凭据或用例前置条件中提供被测地址。
2. 在「用例管理」点击「执行」，或通过「测试套件」批量执行。单条执行与套件执行均会创建独立记录。
3. 在「LLM 对话」观察操作过程；结束后进入「测试管理 → 执行历史 → 查看报告 → 查看详情」，查看本次冻结的步骤、预期、实际观察、日志及截图。
4. 首页「执行统计（含 AI）」汇总这些记录。通过率按 **通过的用例执行次数 ÷ 计划的用例执行次数** 计算，同一用例重复执行会分别计数。

结果区分通过、断言失败、阻塞、系统错误和取消。缺少结构化最终报告、漏执行步骤、修改原预期或引用其他轮次截图，均不能计为通过。遇到验证码或访问拦截应停止并记为阻塞。单次 AI 执行超过 30 分钟会结束并记为阻塞。

业务断言仍由 AI 根据页面与截图判断，后端校验报告结构、步骤覆盖及截图归属；通过并不等同于人工复核或确定性脚本断言。等待人工审批的执行暂记为阻塞，审批后继续的对话不自动改写原结果；需要完整结果时请重新执行用例。

旧套件记录保持可见；升级前只有聊天记录的单条执行不会推断或补记为成功。独立「UI 自动化」执行器仍使用其原有报告入口。

后端回归验证（使用独立测试数据库，不调用外部模型）：

```bash
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend python manage.py test testcases.test_execution_tracking orchestrator_integration.test_execution_guard --noinput
```


## 图文生成、依据确认与执行前检查

新建的图文任务使用以下流程（旧任务保持原有生成结果）：

1. **上传材料**：提供需求文档、补充说明或截图；支持仅图片。任务名可留空。图片需要配置真正支持图片输入的模型，勾选能力标记本身不会赋予模型视觉能力。
2. **整理依据**：文档提取明确要求及可回查的原文摘录；截图按张提取可见控件、状态和无法确定的规则。原文摘录必须能在对应材料中找到。
3. **核对控件、规划与生成**：新图文任务完成图片识别后暂停，状态为“待核对控件”。在“用例结果 → 图片控件”逐图核对，再点击“按核对结果继续生成”。按业务模块组织材料；图片相关批次在生成时携带相应原图，每批最多 6 张，超出则保留完整材料单元拆批。文字生成模型不支持图片时自动使用已配置的图片解析模型。仅文档任务不需要此控件核对步骤。
4. **原材料评审**：评审阶段再次收到该批原图和原文。步骤标明“文档要求 / 图片可见事实 / 推测”，并保留证据编号、模型及来源。缺失原图、输出格式不符或材料过长时显示错误并保留断点，不静默降为仅文字。
5. **核对草稿**：在“用例结果”顶部点击“逐条依据”“待确认问题”“覆盖检查”，查看证据关联、未关联项、AI 评审疑似遗漏及相似用例。引用关系不是完整覆盖率；相似用例只提示，不自动删除。
6. **确认与入库**：无依据的预期、推测、冲突或未解决问题会提示人工判断。在“待确认问题”中核对后，可“确认全部”或勾选用例批量确认，也可逐条确认；补充说明选填（最多 2000 字），同一批无需重复填写。确认表示接受当前用例内容，不会自动修正内容、改变原始 AI 结论或标记执行通过；结构与规则错误仍需修正。系统自动记录确认人、时间、内容版本和接受的原问题。在“人工确认记录”中可整批或单条撤回，撤回后重新检查依据，历史记录仍保留。修改操作、预期、测试数据或定位信息后，旧确认失效；未保存修改或其他人已更新的版本不能直接批量确认。JSON 导入不能继承其他实例的可信确认记录。
7. **配置入口**：在“项目管理”的系统凭据中配置项目地址。同一项目只有一个不同地址时，该项目用例可共用；单条用例中的明确网址优先。配置多个不同地址时，需要在用例中指定本次入口。
8. **执行检查**：用例管理点击“执行”，先检查网址、步骤和预期、未填占位数据、依据确认、激活模型和 MCP 配置。登录及已同步工具清单存在不确定性时会显示提醒。检查仅验证配置，不代表接口连通、已登录或业务测试通过。
9. **观察与报告**：检查通过后进入 AI 执行；单条与套件接口都会再次做相同检查。绕过界面提交的不完整用例会记录为阻塞，不调用执行 Agent。报告仍按本轮步骤断言和截图证据判断，首页与执行历史读取同一结果。

正式用例被修改后，如果依据确认失效，可从执行弹窗的“查看来源与确认依据”回到原任务确认。未采用新版流程的历史用例提示人工复核，不会自动重新生成或补造依据记录。

材料中明确写出的验收范围和排除项会传递到每个生成批次。与当前断言无关的背景疑问可以由原材料评审逐项说明“不影响本用例”，并保留理由；没有来源的预期和推测仍须人工确认，不能通过这种分类免除确认。

原图会在分析、生成和评审阶段发送给配置的模型服务，因此会增加模型调用量与图片 Token 用量。上传前请确认材料适合发送至该服务，并去除不必要的个人信息与密钥。依据评审用于减少无来源的规则，不能替代人工验收，也不保证实际用例通过率提高。

文档依据按材料单元保存。AI 返回的原文摘录必须能在对应原文中核对（忽略空白，不接受改写或跨段拼接）；某一条引用出错不会丢弃同批已通过的内容。系统会携带具体错误，对出错单元最多单独修复两次。仍未通过的引用不会成为已验证证据，结果页的“原文引用待核对”列出来源、页码、错误引用和定位节选，其他材料继续处理。请在评审时检查这些问题及需求覆盖；生成草稿不代表所有材料均已核实。模型连接中断等错误仍会保留断点，可使用“从断点继续”。

模块规划目录过长时，仅精简目录中的预览文字并明确标记，保留每个材料分组及全部标题。完整原文和已核实证据不会被裁剪，仍用于后续生成与评审。

生成用例时，来源单元可由本批真实证据编号自动恢复。不存在或超出本批范围的证据编号不会被接受：相关步骤转为“推测 / 待确认”，保留错误编号及核对原因，入库前仍需人工核实。完全没有可追溯来源的用例仍要求模型修正，不会自动编造来源。

### 图片控件核对与评审入口

用例结果顶部提供“图片控件、待确认问题、逐条依据、覆盖检查”四个入口。逐条依据支持并排查看原图、需求摘录和步骤；窄屏自动改为上下排列。

- 控件清单包含名称、类型、可见文案、可见状态及可选的图片标注框。模型不确定位置时允许没有框，不将空位置当成识别成功。
- 点击图片标注或清单可选中控件。“放大原图”打开完整核对窗口，图片与识别框同步缩放，仍可编辑字段、切换图片、补充控件和重新框选。返回工作台保留本地修改；“取消本次放大修改”恢复打开窗口时的内容（已点击保存的内容不会被取消）。
- 控件编辑支持撤销、重做、清除标注、恢复模型原值和放弃未保存修改。删除已有控件会将其排除并保留记录，可通过“显示已排除 / 删除的控件”恢复；尚未保存的新控件可直接移除。保存成功后清空本次撤销记录。开始生成后控件只读。
- 每张图片勾选“已核对本图”，可以先保存进度再离开；所有图片核对完才能继续生成。修改控件后需重新勾选该图片。
- 确认的控件作为带编号的材料依据进入生成和原图评审，生成后清单显示显式引用它的用例。原始模型识别值、人工修订前后内容、修改人和时间保存在任务进度中。
- 控件核对只确认可见内容，不确认隐藏业务规则。标注坐标只用于图片证据展示，不作为实际网页 DOM 定位器。识别位置、文案和状态仍可能有误，需要结合原图检查。
- 开始生成后，控件材料冻结，避免悄悄改变既有用例依据。旧任务可查看已保存的文字事实，不补造位置；要使用完整的生成前控件核对流程，请新建图文任务。

“模型、规则与提示词”位于工作台顶部，与生成记录、新建任务并列。弹窗内先编辑副本，保存配置才提交；支持撤销、重做、恢复已保存设置和取消。规则定义 JSON 无效时保留编辑窗口。

待上传材料可移除、清空、撤销与重做，不删除本地文件。草稿用例可删除并撤销；步骤编辑支持撤销、重做、重置和取消。草稿至少保留一条用例，正式用例仍在用例管理中维护。步骤导航按页面分为三步：准备素材 → 生成、评审与入库 → 沉淀与复用，同一时间仅突出显示当前页面对应的步骤。

前端编辑历史回归检查：在 `Vue` 目录运行 `node --test tests/localHistory.test.cjs`，再执行 `npm run build`。

完整回归检查：

```bash
docker compose --env-file local/.env -p guicase-next -f local/compose.yml exec backend python manage.py test guicase_workbench testcases.test_preflight testcases.test_execution_tracking orchestrator_integration.test_execution_guard --noinput
```
