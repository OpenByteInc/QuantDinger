# 生产环境加固

[English](PRODUCTION_HARDENING.md)

## 上线前校验

准备项目根目录 `.env`，替换所有默认凭据，然后执行：

```bash
python backend_api_python/scripts/check_production_config.py \
  --env-file .env \
  --require-grafana
```

校验会拒绝默认数据库密码、管理员密码、Grafana 密码、JWT 密钥和凭据加密密钥。
未部署可选的可观测性覆盖层时，请省略 `--require-grafana`。

## 锁定运行环境

生产覆盖层使用 UID/GID `10001` 运行后端进程，移除 Linux capabilities，将根文件系统
设为只读，并限制 CPU、内存与进程数：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.production.yml \
  -f docker-compose.observability.yml \
  up -d --build
```

首次锁定启动前必须准备好全部密钥，因为非 root 容器不能生成或回写这些值。数据库结构
更新只能由 `migration` 服务执行，不要让 API Worker 并发迁移。

## 不受信任代码沙箱

指标和 Strategy API V2 源码不会在 API、交易、求值或回测所有者进程中执行。官方镜像会启动
干净环境的子进程，并在运行用户处理函数前启用 seccomp、Landlock、资源限制和仅 JSON 传输。

- 使用受支持的 `x86_64` 或 `aarch64` Linux 宿主机。内核边界不可用时应将指标/策略执行视为失败，不能改为进程内执行。
- 不要把项目 `.env`、Docker Socket、云凭证目录、数据库 Socket 或交易所凭证文件传给沙箱子进程。
- 保持 `.env` 中策略超时和内存配置为有界值，并按“每个实盘 Strategy API V2 部署一个持久子进程、每次回测一个一次性子进程”规划容量。
- 沙箱实现升级后，应重新创建 API、交易、求值和回测 Worker 容器；只重启前端不会启用新边界。

## 网络边界

- 公网只暴露 TLS 反向代理的 80/443。
- PostgreSQL、两套 Redis、Prometheus、Grafana、Alertmanager 不直接暴露公网。
- 后端 API 若必须独立暴露，应置于认证、限流和 TLS 之后。
- MCP 网络监听必须设置独立的 `QUANTDINGER_MCP_AUTH_TOKEN`，不能复用 Agent Token。

## 数据与恢复

- 备份 PostgreSQL，并定期验证恢复流程。
- 队列任务必须跨主机故障保留时，备份 `celery_redis_data`。
- 缓存 Redis 可以淘汰，不应作为 Celery broker。
- 监控 `redis-jobs` 内存；`noeviction` 会在内存不足时拒绝新写入，而不是丢弃任务。
- 记录密钥轮换、数据库恢复和实盘停机开关的演练结果。
