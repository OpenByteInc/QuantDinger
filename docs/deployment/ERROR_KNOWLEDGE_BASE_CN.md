# 错误知识库与诊断路径

先判断“哪一层失败”，再修改配置。不要为了消除报错而关闭安全检查、删除数据卷或重复重装。

## 先收集这些信息

```bash
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 migration
docker compose logs --tail=200 postgres redis redis-jobs
```

同时记录：发生时间和时区、页面与操作、请求 ID（如有）、版本或镜像摘要、浏览器、最近变更。分享日志前必须删除 Token、Cookie、API Key、邮箱、完整账户号和订单敏感字段。

## 快速判断

| 现象 | 常见原因 | 首要检查 |
| --- | --- | --- |
| 页面打不开 | 前端未启动、端口冲突、代理错误 | `docker compose ps`、8888 端口、代理 upstream |
| 页面能开但接口失败 | 后端不健康、跨域或 URL 配错 | `/api/health`、`FRONTEND_URL`、后端日志 |
| 首次启动一直等待 | 数据库未就绪或迁移失败 | `migration` 与 `postgres` 日志 |
| 登录失败 | 管理员信息、时间、Cookie、OAuth 回调错误 | 系统时间、域名、HTTPS、认证设置 |
| AI 无输出 | Key/模型/端点错误、限流或余额不足 | AI / LLM 连接测试与提供商响应 |
| 数据为空或陈旧 | 数据源未配置、同步失败、市场休市 | 数据状态与同步、标的/时区/频率 |
| 回测失败 | 数据不足、策略参数或资源限制 | 任务详情、Celery 日志、日期范围 |
| 券商连接失败 | 网关不可达、权限/白名单/时间错误 | 网络路径、账户权限、模拟环境 |
| 任务长期排队 | `redis-jobs` 或 worker 不健康 | worker、Celery、磁盘与内存 |
| 升级后不可用 | 环境变量缺失、迁移或版本不兼容 | 发布说明、迁移日志、备份与回滚条件 |

## 分层诊断

1. **浏览器层**：强制刷新，检查控制台和 Network；确认访问的是正确域名和 HTTPS。
2. **入口层**：检查 DNS、证书、反向代理、上传大小和超时。
3. **应用层**：检查 `/api/health`、backend 日志、部署模式和环境变量。
4. **任务层**：检查 Celery、scheduler、strategy workers 和 `redis-jobs`。
5. **数据层**：检查 migration、PostgreSQL、磁盘空间和连接数。
6. **外部服务层**：检查服务商状态、权限、余额、限流、白名单和系统时间。

## 安全的处理顺序

1. 保存现场和日志。
2. 停止继续产生影响的策略或入口。
3. 只修改一个最可能的原因并记录前后状态。
4. 使用健康检查和最小复现验证。
5. 恢复业务后补充根因、影响、修复和预防措施。

## 禁止操作

- 不要执行 `docker compose down -v` 处理普通故障。
- 不要把真实 Secret 粘贴到 Issue 或群聊。
- 不要手工修改迁移历史或生产订单数据来“解除卡住”。
- 不要在未备份和未确认兼容性的情况下回退应用版本。

更详细的安装问题见[安装排错](INSTALL_TROUBLESHOOTING_CN.md)，管理员页面问题见[管理员与设置排错](ADMIN_AND_SETTINGS_TROUBLESHOOTING_CN.md)，生产监控见[可观测性](OBSERVABILITY_CN.md)。
