# 安装故障排查

本指南用于排查首次 Docker 与 Compose 安装问题。请在包含
`docker-compose.yml` 的仓库根目录执行命令。

[English](INSTALL_TROUBLESHOOTING.md)

## 先做这组检查

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 env-sync postgres redis redis-jobs kafka kafka-init migration backend
```

请优先查看第一个失败的依赖服务，而不是只看最后出现的 `backend` 错误。正常启动顺序是
`env-sync`、PostgreSQL/两类 Redis/Kafka 状态服务、`migration` 与 `kafka-init`，然后才是
API 和各 Worker。

## 缺少 PostgreSQL 密码

如果 Compose 提示 `POSTGRES_PASSWORD is required`，不要在已有数据库上随意填写新密码。
源码检出部署请从仓库根目录执行：

```bash
python3 scripts/bootstrap_env.py
```

手动下载的 GHCR 部署请确保 `scripts/bootstrap_env.py` 已按云部署指南下载，然后执行：

```bash
python3 scripts/bootstrap_env.py --compose-file docker-compose.ghcr.yml
```

初始化脚本不会打印密钥。全新安装会生成随机密码；升级时会保留 `.env` 中的值，并尝试从
旧 `DATABASE_URL` 或仍存在的 `quantdinger-db` 容器恢复原密码。如果已经存在 `.env`、
旧环境文件或 PostgreSQL 数据卷但无法恢复密码，脚本会停止，不会生成替代密码。此时请从
`.env` 或服务器备份中恢复原 `POSTGRES_PASSWORD`，也不要执行 `docker compose down -v`。

只有在确认属于全新部署、只是手动预先创建了空 `.env`，并且不存在任何 PostgreSQL 数据时，
才可以增加 `--allow-new-database` 允许脚本生成密码。升级修复不得使用该参数。

## 镜像拉取失败

常见错误包含 `registry-1.docker.io`、`failed to resolve reference`、连接超时，
或 Docker Desktop 没有 HTTPS 代理。

1. 确认宿主机代理端口正在监听。
2. 在 Docker Desktop 打开 **Settings → Resources → Proxies**。
3. 为 HTTP 与 HTTPS 填写实际可用的 HTTP 代理地址。
4. 保存后彻底退出并重启 Docker Desktop。
5. 使用 `docker info` 确认代理生效，再执行 `docker compose pull`。

Windows 可用下面的命令检查：

```powershell
curl.exe -x http://127.0.0.1:10808 https://registry-1.docker.io/v2/
docker info | findstr /i proxy
```

`/v2/` 返回 `UNAUTHORIZED` 表示 Registry 已经可达，只是该接口需要 Docker 的认证
流程；超时或连接失败才表示网络仍未打通。

## PostgreSQL 升级后不健康

如果日志提示数据目录由另一个 PostgreSQL 大版本初始化，不要反复重启容器。
PostgreSQL 数据目录不能跨大版本直接复用。

仅当本地数据可以全部丢弃时：

```bash
docker compose down -v
docker compose up -d
```

> `down -v` 会永久删除 Compose volumes，包括本地数据库。需要保留数据时严禁使用。

需要保留数据时，应先用原 PostgreSQL 大版本启动，再通过 `pg_dump`/`pg_restore`
或 `pg_upgrade` 迁移。更换镜像或数据卷前必须先备份。

## 数据库迁移没有完成

```bash
docker compose ps migration
docker compose logs --tail=200 migration
docker compose logs --tail=100 postgres
```

不要从多个 API Worker 并发执行结构变更。数据库升级由 Compose 中的 `migration`
服务统一负责，其他服务会等待它成功结束。

## 后端退出或健康检查失败

```bash
docker compose logs --tail=200 backend
docker compose exec backend curl -f http://localhost:5000/api/health
```

优先核对：

- 项目根目录 `.env` 是否存在并挂载为 `/app/.env`；
- 升级后是否已从最新 `.env.example` 同步缺失字段；
- `POSTGRES_PASSWORD`、`SECRET_KEY` 与 `CREDENTIAL_ENCRYPTION_KEY` 是否为空或占位值；
- 各服务使用的 PostgreSQL 与 Redis 密码是否一致；
- `migration` 服务是否已成功完成。

管理员登录或系统设置保存问题请阅读
[管理员凭据与系统设置排错](ADMIN_AND_SETTINGS_TROUBLESHOOTING_CN.md)。

## 安全的恢复命令

```bash
# 查看状态与近期日志
docker compose ps
docker compose logs --tail=100 backend kafka trading-worker \
  strategy-dispatcher-worker strategy-evaluator-worker scheduler-worker

# 保留数据卷并重建容器
docker compose up -d --force-recreate

# 保留数据卷并停止容器
docker compose down
```

只有确认备份可用或数据明确可以丢弃时，才执行会删除数据卷的操作。
