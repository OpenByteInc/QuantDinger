# QuantDinger 安装与部署总览

这篇文档用于先选对安装方式，再进入对应的逐步教程。QuantDinger 的服务端、Web 页面和手机客户端是不同层次：Windows、Linux 或云服务器负责运行服务；H5、Android 和 iOS 是访问同一服务的客户端。

## 先选择你的目标

| 目标 | 推荐方式 | 文档 |
| --- | --- | --- |
| 在 Windows 电脑上自用 | Docker Desktop 一键安装 | [Windows 安装](../deployment/WINDOWS_INSTALLATION_CN.md) |
| 在 Linux 工作站或 macOS 上自用 | Docker 一键安装 | [Linux 与 macOS 安装](../deployment/LINUX_MACOS_INSTALLATION_CN.md) |
| 部署到公网云服务器 | Ubuntu/Debian + Docker + HTTPS | [云服务器部署](../deployment/CLOUD_DEPLOYMENT_CN.md) |
| 运营多用户 SaaS | 生产部署 + 注册、邮件、支付和监控 | [SaaS 部署](../deployment/SAAS_DEPLOYMENT_CN.md) |
| 使用手机访问或构建 App | H5、Android 或 iOS 客户端 | [移动端部署](../deployment/MOBILE_CLIENT_DEPLOYMENT_CN.md) |
| 从 AWS Marketplace 部署 | Marketplace 镜像 | [AWS Marketplace](../deployment/AWS_MARKETPLACE_DEPLOYMENT_CN.md) |

## 推荐安装方式

普通用户优先使用 GHCR 预构建镜像。安装程序会下载 Compose 配置、生成必要密钥、创建管理员并启动服务，不需要在本机编译 Python 或 Vue。

Linux 或 macOS：

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

默认安装目录分别是 `~/quantdinger` 和 `%USERPROFILE%\quantdinger`。安装程序可以重复执行用于升级；已有 `.env` 值和数据卷会被保留。

## 安装完成后的地址

| 服务 | 默认本机地址 | 用途 |
| --- | --- | --- |
| 桌面 Web | `http://127.0.0.1:8888` | 完整管理、研究、回测和交易界面 |
| 移动 H5 | `http://127.0.0.1:8889` | 手机浏览器界面 |
| 后端健康检查 | `http://127.0.0.1:5000/api/health` | 判断后端是否就绪 |
| OpenAPI | `http://127.0.0.1:5000/openapi.json` | 机器可读 API 契约 |

如果页面打不开，不要反复删除容器或数据卷。先执行 `docker compose ps`，再进入[安装故障排查](../deployment/INSTALL_TROUBLESHOOTING_CN.md)。

## 安装后的正确顺序

1. 按[部署后首次设置](POST_INSTALL_SETUP_CN.md)修改密码、站点地址、注册策略和数据源。
2. 需要公网访问时配置域名、HTTPS、备份和监控。
3. 完成[第一次运行](FIRST_RUN_CN.md)，先研究和回测，再使用模拟盘。
4. 准备真实资金前完成[实盘安全清单](../trading/LIVE_TRADING_SAFETY_CN.md)。

## 源码部署适合谁

只有需要修改后端或本地构建镜像的开发者才应使用源码部署。普通使用和生产服务器应优先使用固定版本的 GHCR 镜像，减少编译环境差异。源码部署步骤见[云服务器部署](../deployment/CLOUD_DEPLOYMENT_CN.md#可选完整源码部署)。

## 安全边界

- 不要把 `5000`、`5432`、`6379` 或 `29092` 直接开放到公网。
- 不要提交 `.env`、交易凭据、模型密钥或支付密钥。
- 不要为了重新安装而删除 PostgreSQL 数据卷；先备份并确认数据恢复方案。
- 手机 App 不是独立后端，必须连接一个可访问的 QuantDinger 服务端。

