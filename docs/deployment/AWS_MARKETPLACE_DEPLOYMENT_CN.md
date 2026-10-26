# AWS Marketplace 单机一键部署

本指南适用于 AWS Marketplace 上免费的 QuantDinger 容器产品。软件费用为零，但用户的 AWS
账户仍需承担 EC2 实例、EBS 存储、公网 IPv4、数据传输以及自行启用的其他 AWS 服务费用。

Marketplace 交付方案会将完整系统部署到**一台 EC2 实例**。产品版本页显示的 6 个镜像分别是后端
与工作进程、桌面 Web 客户端、移动 Web 客户端、PostgreSQL、Redis 和 Kafka，它们是同一套应用的
组件，不会创建 6 台服务器。

## 部署前准备

部署账户需要有权创建 CloudFormation、EC2、VPC、IAM、Systems Manager 和安全组资源，并且需要
先接受 Marketplace 产品条款。

请将 `AllowedCidr` 限制为尽可能小的范围。评估时通常填写当前公网 IP 加 `/32`，例如
`203.0.113.10/32`。模板特意不为该参数提供默认值。使用 `0.0.0.0/0` 会把两个 HTTP 界面暴露到
互联网；在配置 HTTPS 和必要的网络控制之前，不要使用这一设置。

默认的 `t3.large`、30 GiB 系统盘和 100 GiB 加密持久数据盘是完整单机栈的建议起步配置。并发
回测、多个实盘工作进程或大规模行情数据场景应选择更大的实例。

## 一键部署

1. 在 AWS Marketplace 打开 QuantDinger 产品并接受条款。
2. 选择目标版本的 CloudFormation 部署方式。
3. 检查模板，填写 Stack 名称，收紧 `AllowedCidr`，并选择实例规格、系统盘和持久数据盘容量。
4. 确认 CloudFormation 将创建 IAM 角色，然后创建 Stack。
5. 等待状态变为 `CREATE_COMPLETE`。首次拉取镜像和数据库迁移通常需要 10-30 分钟。

Stack 输出包括：

- `WebUrl`：桌面 Web 客户端；
- `MobileWebUrl`：移动 Web 客户端；
- `InstanceId`：通过 Systems Manager 管理实例；
- `AdminPasswordParameter`：保存随机管理员初始密码的 Parameter Store 路径。

可在同一区域的 Systems Manager Parameter Store 中查看初始密码，也可使用 AWS CLI：

```bash
aws ssm get-parameter \
  --name /quantdinger/STACK_NAME/admin-password \
  --with-decryption \
  --query Parameter.Value \
  --output text
```

使用用户名 `admin` 和获取到的密码登录。首次登录后请修改管理员密码，并按需配置券商、行情、AI
和通知服务。

## 架构与数据位置

CloudFormation 会创建独立 VPC、公有子网、互联网网关、安全组、实例 IAM 角色，以及一台 Amazon
Linux 2023 x86_64 EC2。模板不开放 SSH，请使用 AWS Systems Manager Session Manager 进入实例。

应用配置、PostgreSQL 数据、Kafka 数据和本地备份保存在独立的加密 EBS 数据盘中。即使 Stack 或
EC2 被替换，CloudFormation 也会保留该数据盘。部署目录为 `/opt/quantdinger`，生成的密钥位于
`/opt/quantdinger/.env`，仅允许文件所有者读取。

这是便于快速启动和评估的单机拓扑。生产环境还应配置 HTTPS、监控、异机备份、恢复演练，以及
符合业务目标的高可用架构。单台 EC2 本身不具备高可用能力。

## 版本升级

升级时使用新 Marketplace 版本附带的模板，不要手动改用未经发布审核的镜像标签。

1. 如 AWS 要求，先接受新的 Marketplace 版本。
2. 创建 EBS 快照，或把经过验证的数据库备份复制到实例之外。
3. 在 CloudFormation 中选择 **Update stack**，用新版本化模板替换当前模板并查看 Change Set。
4. 确认变更主要是版本参数和 Systems Manager 部署关联；除非版本说明明确要求，否则不应替换 EC2
   或 VPC。
5. 执行更新并等待状态变为 `UPDATE_COMPLETE`。
6. 检查 `WebUrl`、`MobileWebUrl`、登录、工作进程、券商连接和应用健康状态。

自动升级会在更换容器前将压缩的 PostgreSQL 备份写入 `/opt/quantdinger/backups`，并保留最近 5
份本地备份；随后拉取固定版本的镜像、执行数据库迁移、启动服务并进行健康检查。更新 Stack 不会
重置管理员密码或应用配置。

## 故障排查与回滚

首先查看 CloudFormation Resources/Events。实际部署命令由 AWS Systems Manager Association 执行，
详细输出可在 Systems Manager 的 **State Manager** 或 **Run Command** 中查看。需要进一步排查时，
通过 Session Manager 登录实例并执行：

```bash
cd /opt/quantdinger
sudo docker compose --env-file .env -f docker-compose.yml ps
sudo docker compose --env-file .env -f docker-compose.yml logs --tail=200 migration backend
```

自动化会保留 `.env.previous`、`docker-compose.yml.previous` 和最近的数据库备份。仅回滚应用时，
恢复上一版 Compose 与镜像设置后重新启动。如果数据库迁移不能向后兼容，应先停止应用写入，再
恢复升级前已验证的数据库备份，然后启动上一版本。请遵循对应版本说明，并通过恢复演练确认备份
可用。

如果因无法拉取 Marketplace 镜像而失败，请确认同一 AWS 账户已经接受产品条款，并确认没有限制
实例角色权限或出站网络访问。

## 删除与成本控制

删除 CloudFormation Stack 会终止 EC2 并删除系统盘，但会特意保留包含 PostgreSQL 数据和本地
备份的加密数据盘。Stack 输出 `RetainedDataVolumeId` 会显示该磁盘 ID。手动删除数据盘前，务必
导出数据或创建需要保留的快照。

停止 EC2 可以停止计算费用，但 EBS 和公网 IPv4 等费用可能仍会继续。删除 Stack 会释放公网 IPv4
并移除模板创建的其他非保留资源；保留的数据盘和快照会继续产生费用，直至用户手动删除。
