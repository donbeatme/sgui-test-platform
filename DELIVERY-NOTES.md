# 2026-10-28 源码交付说明

本目录是当前工作区的源码快照，包含尚未推送到公开仓库的最新修改。
配套《SGUI-甲方部署环境与Docker配置-20261028.md》在源码 ZIP 外单独交付，请交甲方 IT 按文档准备环境。

- 部署入口：local/compose.yml；平台访问端口默认 8778。
- 新环境需自行创建 local/.env，按 Vue/package-lock.json 构建 Vue/dist。
- 本包不包含环境软件、Docker 镜像、node_modules、Python venv、浏览器本体、实际配置密码或业务数据库。
- 原携程项目、用例、模型配置和附件不随源码迁移，初始化后是新平台。
- 去掉了开发电脑专用的固定路径启动脚本；使用配套文档中的可移植命令。
- 导出副本清空了根 .env.example 的 AI_API_KEY 默认值、WeKnora 技能的 API Key 默认值，并同步技能 ZIP；Shell 脚本统一为 LF 换行。
- LICENSE、NOTICE 和第三方声明按原内容保留。
- SOURCE-MANIFEST.sha256 记录包内文件校验值，清单本身不包含在其校验项中。

原工作区文件未因打包而被修改。
