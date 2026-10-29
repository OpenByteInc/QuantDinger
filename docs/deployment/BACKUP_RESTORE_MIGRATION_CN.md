# 备份、恢复与迁移

本文面向自托管管理员。备份的目标不是“保存一个数据库文件”，而是能够在另一台机器上恢复出可登录、可查询、可继续运行的 QuantDinger。

## 必须保存的内容

| 内容 | 是否必须 | 说明 |
| --- | --- | --- |
| PostgreSQL 数据库 | 是 | 用户、策略、回测、订单、系统配置等持久数据 |
| `.env` | 是 | 端口、部署模式和外部服务配置 |
| `CREDENTIAL_ENCRYPTION_KEY` | 是 | 解密已保存券商、交易所和第三方凭证；丢失后无法恢复 |
| 反向代理与证书配置 | 生产环境必须 | 域名、HTTPS 和代理规则 |
| 自定义策略、脚本和挂载目录 | 按需 | 不在数据库或镜像内的本地资产 |
| 镜像版本或 `IMAGE_TAG` | 建议 | 用于重现备份时的运行版本 |

不要把 `.env`、密钥或数据库备份提交到 Git。备份应加密，并与生产服务器分开保存。

## 创建一致性备份

1. 记录当前版本：

   ```bash
   docker compose ps
   docker compose images
   ```

2. 创建目录并导出 PostgreSQL 自定义格式备份：

   ```bash
   mkdir -p backups
   docker exec quantdinger-db pg_dump -U quantdinger -Fc -d quantdinger -f /tmp/quantdinger.dump
   docker cp quantdinger-db:/tmp/quantdinger.dump ./backups/quantdinger.dump
   ```

3. 将 `.env`、代理配置和自定义挂载目录复制到加密备份介质。
4. 记录备份时间、QuantDinger 版本、数据库容器版本和校验和。
5. 在隔离环境执行一次恢复演练。没有通过恢复演练的文件不能算有效备份。

持续写入较多的生产实例，建议先暂停策略和任务入口，待数据库导出完成后再恢复运行。

## 恢复到新环境

1. 安装与原环境兼容的 Docker 和 QuantDinger 版本。
2. 恢复 `.env`，尤其保持原 `CREDENTIAL_ENCRYPTION_KEY` 不变。
3. 启动 PostgreSQL，停止可能写入数据库的应用服务。
4. 复制并恢复备份：

   ```bash
   docker cp ./backups/quantdinger.dump quantdinger-db:/tmp/restore.dump
   docker exec quantdinger-db pg_restore -U quantdinger -d quantdinger --clean --if-exists /tmp/restore.dump
   ```

5. 运行当前版本的数据库迁移并启动完整服务：

   ```bash
   docker compose up -d migration
   docker compose up -d
   ```

6. 按顺序验证健康检查、管理员登录、策略与回测历史、券商账户、通知和任务队列。

建议先恢复到临时环境验证，再切换生产域名。恢复过程中不要执行 `docker compose down -v`，它会删除数据卷。

## 迁移到另一台主机

迁移应采用“旧机备份 → 新机恢复 → 验收 → 切流”的方式：

1. 降低 DNS TTL，并记录旧环境版本。
2. 在旧机暂停新策略、计划任务和写入入口。
3. 创建最终数据库备份并复制配置和自定义资产。
4. 在新机恢复，用临时域名完成验收。
5. 切换 DNS 或负载均衡器，观察健康、日志和订单状态。
6. 保留旧机为只读回退点，确认稳定后再下线。

跨版本迁移时，先用旧版本恢复成功，再按[升级与回滚](UPGRADE_ROLLBACK_CN.md)逐版升级。不要把数据库直接恢复到一个未经验证的更旧应用版本。

## 定期检查清单

- 每日或按业务恢复点目标备份数据库。
- 每次升级前做一次不可覆盖的完整备份。
- 至少每季度完成一次隔离恢复演练。
- 定期验证备份校验和、加密密钥和保留周期。
- 备份凭证只授予必要人员，下载和恢复操作应留审计记录。

相关文档：[升级与回滚](UPGRADE_ROLLBACK_CN.md) · [生产加固](PRODUCTION_HARDENING_CN.md) · [可观测性](OBSERVABILITY_CN.md)
