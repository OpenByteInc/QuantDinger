# Windows 安装 QuantDinger

本指南适用于 Windows 10/11 本机自用或局域网测试。生产公网部署建议使用 Linux 云服务器。

## 1. 前置条件

- 64 位 Windows 10 22H2 或 Windows 11。
- BIOS/UEFI 已开启虚拟化。
- 安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，启用 WSL 2 后端和 Docker Compose v2。
- 至少 8 GB 内存、20 GB 可用磁盘；同时运行回测与 AI 任务建议 16 GB 内存。

在 PowerShell 验证：

```powershell
docker version
docker compose version
docker info
```

`docker info` 失败通常表示 Docker Desktop 尚未启动，而不是 QuantDinger 故障。

## 2. 一键安装

以普通用户打开 PowerShell：

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

安装器会询问管理员账号、密码、邮箱和端口。默认目录是 `%USERPROFILE%\quantdinger`。如需指定目录：

```powershell
$env:QUANTDINGER_INSTALL_DIR = "D:\QuantDinger"
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

不要在已经有数据的实例上随意更换安装目录，否则 Docker Compose 会把它识别为另一个项目。

## 3. 验证安装

```powershell
Set-Location "$HOME\quantdinger"
docker compose ps
docker compose logs --tail 100 backend
```

浏览器打开：

- Web：`http://127.0.0.1:8888`
- 移动 H5：`http://127.0.0.1:8889`
- 健康检查：`http://127.0.0.1:5000/api/health`

所有核心服务应处于 `running` 或 `healthy`。数据库迁移第一次启动可能需要几分钟。

## 4. 升级

再次执行同一安装命令即可同步 Compose、环境模板和镜像。安装器保留已有配置，并在需要时备份 `.env`。

也可以在安装目录手动执行：

```powershell
docker compose pull
docker compose up -d
docker compose ps
```

升级前先备份数据库。不要用 `docker compose down -v`，`-v` 会删除数据卷。

## 5. 停止、启动和查看日志

```powershell
docker compose stop
docker compose start
docker compose restart backend
docker compose logs -f --tail 200 backend
```

## 6. 局域网访问

默认端口只绑定到 `127.0.0.1`，其他设备无法访问。需要局域网访问时，应先理解访问风险，再修改 `.env` 中的前端绑定和 Windows 防火墙。不要把数据库、Redis、Kafka 或后端调试端口开放到公网。

正式公网服务请使用域名、HTTPS 和反向代理，参照[云服务器部署](CLOUD_DEPLOYMENT_CN.md)。

## 7. 常见 Windows 问题

- WSL 2 未安装：执行 `wsl --install` 后重启，再启动 Docker Desktop。
- 虚拟化未开启：在任务管理器“性能 → CPU”确认“虚拟化：已启用”。
- 端口占用：用 `Get-NetTCPConnection -LocalPort 8888` 查找进程，或在安装器中选择其他端口。
- Docker 内存不足：在 Docker Desktop/WSL 配置中增加内存，不要只反复重启容器。
- PowerShell 无法下载：检查代理、TLS 和 GitHub Raw 访问；不要从不可信镜像复制安装脚本。

更多故障见[安装故障排查](INSTALL_TROUBLESHOOTING_CN.md)。安装成功后进入[部署后首次设置](../getting-started/POST_INSTALL_SETUP_CN.md)。

