# QuantDinger 安装与部署 FAQ

这里回答安装、升级和部署后最常见的问题。涉及数据库密码、迁移或镜像拉取的深入处理见[安装故障排查](INSTALL_TROUBLESHOOTING_CN.md)。

## 安装前需要什么？

Docker 与 Compose v2 是必需条件。个人使用建议至少 4 核、8 GB 内存；同时运行 AI、回测和交易 Worker 建议 8 核、16 GB。公网服务器还需要域名、HTTPS、防火墙和备份空间。

## Docker 已安装，为什么安装器仍报错？

依次检查：

```bash
docker version
docker info
docker compose version
```

`docker info` 失败表示守护进程未运行或当前用户无权限；`docker compose` 失败表示缺少 Compose v2。Windows 还需确认 Docker Desktop 和 WSL 2 已启动。

## 安装后页面打不开怎么办？

在安装目录执行：

```bash
docker compose ps
docker compose logs --tail 200 backend
docker compose logs --tail 100 frontend
```

先确认端口没有被占用，再访问后端健康检查。前端可打开但登录/API 失败通常是后端、反向代理或 `FRONTEND_URL` 配置问题。

## 默认账号和密码是什么？

没有通用默认密码。安装器要求创建管理员。不要从网上复制所谓默认凭据。忘记密码时使用系统密码重置流程或管理员设置排障，不要直接修改数据库哈希。

## 数据保存在哪里？

数据库、Redis 和 Kafka 数据位于 Docker 命名卷，配置位于安装目录 `.env`。删除容器通常不会删除卷，但 `docker compose down -v` 会删除卷。迁移机器前必须同时备份数据库和必要配置。

## 怎样升级？

优先重新执行同一一键安装器，或在安装目录执行：

```bash
docker compose pull
docker compose up -d
docker compose ps
```

生产环境固定 `IMAGE_TAG`，先备份，再阅读版本说明和迁移要求。升级后检查健康、迁移、Worker 和关键业务流程。

## 可以直接重新安装解决问题吗？

不建议。先看日志和健康状态。重新运行安装器是安全的配置同步方式，但删除目录、`.env` 或数据卷可能造成密钥变化和永久数据丢失。

## 如何卸载但保留数据？

执行 `docker compose stop` 或 `docker compose down`，保留安装目录和命名卷。只有在完成备份且确认永久清除时才使用 `down -v`。

## Windows 可以部署 iOS 吗？

Windows 可以运行服务端和构建 Android，但 iOS 签名和归档需要 macOS、Xcode 和 Apple Developer 账号。详见[移动端部署](MOBILE_CLIENT_DEPLOYMENT_CN.md)。

## 手机为什么不能连接电脑上的 localhost？

手机的 `localhost` 指手机本身。使用手机可访问的局域网 IP 或 HTTPS 域名，并确认防火墙、证书和 API 地址。生产 App 不应依赖明文 HTTP。

## 云服务器应该开放哪些端口？

通常只开放 SSH `22`、HTTP `80`、HTTPS `443`。`5000`、`5432`、`6379` 和 `29092` 保持回环或私网绑定。

## 修改 `.env` 为什么没有生效？

环境变量在容器创建时读取。修改后执行：

```bash
docker compose up -d --force-recreate <service-name>
```

先确认编辑的是当前 Compose 项目使用的安装目录，不要在另一个副本中修改。

## OAuth 登录后跳错地址怎么办？

检查 `FRONTEND_URL` 的第一个地址、OAuth 提供商回调地址、反向代理协议头和 HTTPS 域名是否完全一致。桌面站和手机站都使用时，把两个允许的来源写入配置。

## SaaS 只要打开注册和支付吗？

不是。还需要租户隔离、邮件验证、支付 Webhook 幂等、备份恢复、监控告警、审计、条款与隐私政策。按[SaaS 部署指南](SAAS_DEPLOYMENT_CN.md)逐项验收。

## 去哪里继续排查？

- 镜像、PostgreSQL、迁移、健康检查：[安装故障排查](INSTALL_TROUBLESHOOTING_CN.md)
- 登录和后台设置：[管理员与设置排障](ADMIN_AND_SETTINGS_TROUBLESHOOTING_CN.md)
- Nginx、HTTPS 和公网部署：[云服务器部署](CLOUD_DEPLOYMENT_CN.md)
- 邮件、短信、Telegram：对应通知配置文档
- 实盘问题：先停止策略，再按[实盘安全清单](../trading/LIVE_TRADING_SAFETY_CN.md)处理

