# 管理员凭据与系统设置保存排错指南

本文说明 Docker 部署中的初始管理员凭据、已有数据库数据卷，以及系统设置页面
因写入权限导致的保存失败问题。

其他安装问题请参阅[安装排错指南](INSTALL_TROUBLESHOOTING.md)。

## 初始管理员行为

数据库为空时，QuantDinger 会使用 `ADMIN_USER`、`ADMIN_PASSWORD` 和可选的
`ADMIN_EMAIL` 创建初始管理员。密码以哈希保存（标准镜像使用 bcrypt），不会
保存明文。

为保持向后兼容，手动 Docker 部署如果没有修改示例管理员配置，仍可使用
`quantdinger` / `123456` 登录。不要把该默认凭据暴露到公网；请在首次启动前
设置非默认密码，或首次登录后立即修改。一键安装器会拒绝用户选择 `123456`
作为密码。

已有 PostgreSQL 数据卷会被视为需要保留的用户数据。修改 `ADMIN_USER` 或
`ADMIN_PASSWORD` 不会无条件覆盖现有账户。只有同时满足以下条件时，系统才会
自动迁移旧默认管理员：

- 第一个活动管理员仍使用用户名 `quantdinger` 和密码 `123456`；
- 环境文件中明确配置了有效且非默认的管理员用户名和密码；
- 目标用户名没有被其他账户占用。

如果管理员已经修改过密码，QuantDinger 绝不会覆盖。如果目标用户名已经被
其他账户使用，系统会停止迁移，而不是自动提权或合并该账户。

## 系统设置的存储位置

系统设置页面会把配置保存到 PostgreSQL（`qd_runtime_settings` 表）。后端、各
worker 和 Celery 进程启动时都会加载这些配置，之后的修改约 10 秒内自动生效，
无需重启。保存的配置在重启和重新部署后依然保留，因此没有共享宿主机 `.env`
的平台（例如 Railway）也不会丢失设置。API Key 等密码类字段使用
`CREDENTIAL_ENCRYPTION_KEY` 加密存储。

环境变量文件仍是基础配置：系统设置中保存的值会覆盖 `.env` 或容器环境变量中的
同名配置。进程级配置（`SECRET_KEY`、`CREDENTIAL_ENCRYPTION_KEY` 以及进程角色、
策略拓扑相关配置）只在进程启动时生效，因此系统设置仍把它们写入 `/app/.env`，
修改后需要重启。

## 系统设置提示保存失败

保存失败通常表示 PostgreSQL 不可用，或进程级配置无法写入 `/app/.env`。它在宿主机上对应：

| 部署方式 | 宿主机文件 |
| --- | --- |
| 所有受支持的 Compose 部署 | 项目根目录 `.env` |

后端应用以 UID/GID `10001` 运行。当前后端镜像先以 root 启动，初始化挂载文件，
把 `/app/.env` 的所有权调整为 `10001:10001`、权限收紧为 `600`，然后再降权。
一键安装和手动 Docker Compose 安装都会执行这套逻辑。

检查运行用户和写权限：

```bash
docker compose exec -u 10001:10001 -T backend sh -c '
  id
  ls -ln /app/.env
  test -r /app/.env && test -w /app/.env \
    && echo writable=yes || echo writable=no
'
```

如果手动下载后使用的是 `docker-compose.ghcr.yml`，请在 `docker compose` 后面
加上 `-f docker-compose.ghcr.yml`。

正常结果应包含 UID `10001`、文件所有者和组 `10001 10001`、权限 `600`，以及
`writable=yes`。

### 修复仍使用旧镜像的部署

应先更新后端镜像。只重新下载 `install.sh` 不会更新已经运行的容器：

```bash
docker compose pull backend
docker compose up -d --force-recreate backend
```

如果旧镜像留下了 root 所有的宿主机文件，只修复后端运行配置文件：

```bash
# 所有受支持的 Compose 部署
sudo chown 10001:10001 .env
sudo chmod 600 .env
```

不要使用 `chmod 755` 或 `chmod -R 777`。这些文件包含管理员凭据、API Key、
OAuth Secret 以及券商或交易所设置。`755` 会让其他本地用户读取敏感信息，
而且仍不能让 UID `10001` 写入 root 所有的文件。

## 只读挂载与特殊 Docker 部署

自动修复无法把有意设置的只读挂载变成可写。以下情况中，系统设置页面按设计
不可写：

- `/app/.env` 使用了 `:ro` 只读挂载；
- 启用了 `docker-compose.production.yml`；
- 容器通过 `user: 10001:10001` 强制从非 root 用户启动，无法预先调整所有权；
- rootless Docker、user namespace、NFS 或其他文件系统策略拒绝 `chown`。

加固只读部署请在宿主机修改环境文件，然后重建服务：

```bash
docker compose pull
docker compose up -d --force-recreate
```

返回[主 README](../../README.md)。
