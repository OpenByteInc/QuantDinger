# Linux 与 macOS 安装 QuantDinger

本指南适用于 Linux 工作站、Linux 单机服务器和 macOS 本机体验。公网生产环境还需继续完成域名、HTTPS、备份与监控配置。

## 1. 前置条件

- Linux 推荐 Ubuntu 22.04/24.04 或 Debian 12；macOS 使用仍受 Docker Desktop 支持的版本。
- Docker Engine 或 Docker Desktop，且 `docker compose version` 可用。
- 最低 4 核、8 GB 内存、20 GB 可用磁盘；生产环境建议 8 核、16 GB 和 50 GB 以上磁盘。
- `curl` 和正常的 GitHub/GHCR 网络访问。

Linux 安装 Docker：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
newgrp docker
docker info
docker compose version
```

macOS 请安装 Docker Desktop 并等待状态显示 Docker Engine 正在运行。

## 2. 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

指定安装目录：

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash -s -- /opt/quantdinger
```

写入 `/opt` 时需要确保当前用户对目录有权限。不要直接用 root 运行整套服务来规避权限问题。

## 3. 验证安装

```bash
cd ~/quantdinger
docker compose ps
docker compose logs --tail 100 backend
curl -fsS http://127.0.0.1:5000/api/health
```

浏览器打开 `http://127.0.0.1:8888`。远程服务器上不要先把 8888 暴露到公网；使用 SSH 隧道临时检查，或继续配置 Nginx 和 HTTPS。

```bash
ssh -L 8888:127.0.0.1:8888 user@server
```

## 4. 公网部署

公网服务器必须继续完成[云服务器部署](CLOUD_DEPLOYMENT_CN.md)中的域名、Nginx、HTTPS、防火墙和固定镜像版本步骤。仅开放 `22`、`80`、`443`，内部服务保持回环或 Docker 私网绑定。

## 5. 升级和日常运维

再次运行安装器会保留现有配置并同步新增环境字段。升级前先备份数据库：

```bash
cd ~/quantdinger
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f --tail 200 backend
```

停止服务但保留数据：

```bash
docker compose stop
```

不要使用 `docker compose down -v`，除非已经确认要永久删除数据卷。

## 6. macOS 注意事项

- Docker Desktop 必须保持运行；关闭应用会停止所有容器。
- Apple Silicon 使用多架构镜像。若第三方镜像不支持当前架构，先检查具体失败服务，不要全局强制 `linux/amd64`。
- macOS 能构建 iOS 客户端，但服务端仍建议用 Docker 运行；iOS 打包见[移动端部署](MOBILE_CLIENT_DEPLOYMENT_CN.md)。

下一步完成[部署后首次设置](../getting-started/POST_INSTALL_SETUP_CN.md)。遇到镜像、数据库或健康检查问题时查看[安装故障排查](INSTALL_TROUBLESHOOTING_CN.md)。

