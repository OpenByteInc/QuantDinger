# QuantDinger 云服务器部署指南

本文说明当前版本 QuantDinger 在云服务器上的 Docker 部署方式，覆盖推荐的 GHCR 预构建镜像部署、可选源码部署、Nginx、HTTPS、升级和常见排错。

首次安装时如果遇到 Docker 拉镜像或 Postgres 启动问题，也请参考 [安装排错指南](INSTALL_TROUBLESHOOTING.md)。

## 推荐架构

推荐使用一个公网域名加宿主机 Nginx 反向代理：

- Web 访问地址：`https://app.example.com`
- 可选移动 H5 地址：`https://m.example.com`
- 宿主机 Nginx 监听 `80/443`
- Docker `frontend` 绑定到 `127.0.0.1:8888`
- Docker `mobile` 绑定到 `127.0.0.1:8889`
- Docker `backend` 绑定到 `127.0.0.1:5000`
- Docker `postgres` 和 `redis` 只绑定本机地址

公网只开放 `80` 和 `443`。不要把 `5000`、`5432`、`6379` 暴露到公网。

## 1. 准备服务器

推荐配置：

- Ubuntu 22.04 / 24.04 或 Debian 12
- 最低 2 核 4 GB 内存；AI 使用较多时建议 4 核 8 GB
- 30 GB 以上磁盘空间
- 安全组或防火墙开放 `22`、`80`、`443`
- 一个域名，例如 `app.example.com`

配置 DNS：

```text
app.example.com -> 服务器公网 IP
m.example.com   -> 服务器公网 IP  # 可选移动 H5 域名
```

验证解析：

```bash
ping app.example.com
```

## 2. 安装 Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

请使用 Compose v2 命令：`docker compose ...`。

## 3. 选择部署模式

### 推荐：GHCR 预构建镜像部署

普通云服务器部署建议使用这个模式。后端、Web 前端、移动 H5 都从 GHCR 拉取镜像，不需要在服务器上本地构建 Python 或 Node 项目。

```bash
mkdir -p ~/quantdinger
cd ~/quantdinger
curl -O https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/docker-compose.ghcr.yml
curl -o backend.env https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/backend_api_python/env.example
```

首次启动前编辑 `backend.env`：

```ini
ADMIN_USER=your_admin_user
ADMIN_PASSWORD=your_strong_password
FRONTEND_URL=https://app.example.com,https://m.example.com
ALLOW_LOCAL_DESKTOP_BROKERS=false
```

GHCR 后端入口脚本可以在首次启动时自动生成 `SECRET_KEY` 并写回 `backend.env`。你也可以手动设置一个足够长的随机字符串。

可选：创建项目根目录 `.env`，用于 Docker Compose 编排配置：

```ini
FRONTEND_PORT=127.0.0.1:8888
MOBILE_PORT=127.0.0.1:8889
BACKEND_PORT=127.0.0.1:5000
DB_PORT=127.0.0.1:5432
REDIS_PORT=127.0.0.1:6379

# 固定版本，避免一直使用 latest，例如：
# IMAGE_TAG=5.0.1

# postgres/redis 拉取慢时可设置 Docker Hub 镜像前缀：
# IMAGE_PREFIX=docker.m.daocloud.io/library/
```

启动：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
```

### 可选：完整源码部署

只有需要在服务器上基于本地源码构建后端时，才建议使用这个模式。

```bash
git clone https://github.com/OpenByteInc/QuantDinger.git
cd QuantDinger
cp backend_api_python/env.example backend_api_python/.env
./scripts/generate-secret-key.sh
```

编辑 `backend_api_python/.env`：

```ini
ADMIN_USER=your_admin_user
ADMIN_PASSWORD=your_strong_password
FRONTEND_URL=https://app.example.com,https://m.example.com
ALLOW_LOCAL_DESKTOP_BROKERS=false
```

也可以按上面的示例创建项目根目录 `.env`。

启动：

```bash
docker compose pull
docker compose up -d --build
docker compose ps
```

## 4. 理解两个 env 文件

请区分这几类配置文件：

| 文件 | 使用方 | 用途 |
|------|--------|------|
| `backend.env` | `docker-compose.ghcr.yml` 的后端容器 | 应用运行时配置：管理员账号、`SECRET_KEY`、LLM key、OAuth、券商或交易所 key |
| `backend_api_python/.env` | 完整源码部署的后端容器 | 源码部署时的应用运行时配置 |
| 项目根目录 `.env` | Docker Compose | 端口、镜像 tag、镜像地址、Postgres 镜像和数据目录、镜像源 |

除非 Compose 明确需要，不要把交易所 API key 这类业务密钥放到项目根目录 `.env`。

## 5. 配置 Nginx

安装 Nginx：

```bash
sudo apt update
sudo apt install -y nginx
```

创建 `/etc/nginx/sites-available/quantdinger.conf`：

```nginx
server {
    listen 80;
    server_name app.example.com;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name m.example.com;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8889;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

如果不需要独立移动端域名，可以删除第二个 `server` 块。移动 H5 也可以通过你自己配置的端口、路径或域名访问。

启用站点：

```bash
sudo ln -s /etc/nginx/sites-available/quantdinger.conf /etc/nginx/sites-enabled/quantdinger.conf
sudo nginx -t
sudo systemctl reload nginx
```

如果使用 UFW：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 6. 开启 HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d app.example.com -d m.example.com
sudo certbot renew --dry-run
```

如果只配置了 `app.example.com`，证书申请命令里只保留这个域名即可。

访问：

```text
https://app.example.com
https://m.example.com
```

## 7. 可选 API 子域名

推荐部署方式是让 API 通过前端容器保持同源访问：

```text
Browser -> https://app.example.com -> 宿主机 Nginx -> frontend 容器 -> /api -> backend:5000
```

如果确实需要 `api.example.com`，只通过 Nginx 暴露宿主机本地后端端口：

```nginx
server {
    listen 80;
    server_name api.example.com;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

同时在后端运行时 env 中设置 `FRONTEND_URL`，包含所有用户实际访问的前端域名。

## 8. 常用运维

GHCR 部署：

```bash
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs -f backend
docker compose -f docker-compose.ghcr.yml logs -f postgres
docker compose -f docker-compose.ghcr.yml restart backend
```

更新 GHCR 镜像：

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

完整源码部署更新：

```bash
git pull
docker compose pull
docker compose up -d --build
```

大版本升级前建议先备份 Postgres：

```bash
docker exec quantdinger-db pg_dump -U quantdinger quantdinger > quantdinger_backup.sql
```

## 9. Postgres 18 和已有数据

当前默认 Postgres 镜像是 `postgres:18.3-alpine`，`PGDATA=/var/lib/postgresql/18/docker`。

如果已有数据卷是 Postgres 16 初始化的，不要直接用 Postgres 18 启动。请选择：

- 继续使用匹配的 Postgres 16 镜像，直到完成迁移；
- 使用 `pg_dump` 和 `pg_restore` 导出导入；
- 使用正式的 `pg_upgrade` 大版本迁移流程。

只有开发环境且数据库数据可以丢弃时，按你的部署模式选择命令。

GHCR 部署：

```bash
docker compose -f docker-compose.ghcr.yml down -v
docker compose -f docker-compose.ghcr.yml up -d
```

完整源码部署：

```bash
docker compose down -v
docker compose up -d
```

生产数据不要使用 `down -v`。

## 10. 常见问题

### 镜像拉取失败

如果 `redis`、`postgres` 或 Docker Hub 镜像拉取失败，可在项目根目录 `.env` 设置镜像前缀：

```ini
IMAGE_PREFIX=docker.m.daocloud.io/library/
```

然后重试：

```bash
docker compose -f docker-compose.ghcr.yml pull
```

如果 GHCR 镜像拉取失败：

```bash
docker pull ghcr.io/openbyteinc/quantdinger-backend:latest
docker pull ghcr.io/openbyteinc/quantdinger-frontend:latest
docker pull ghcr.io/openbyteinc/quantdinger-mobile:latest
```

常见原因包括网络阻断、包可见性不是 public、或者固定的 tag 不存在。

### 后端启动后立刻退出

查看日志：

```bash
docker compose -f docker-compose.ghcr.yml logs --tail=100 backend
```

常见原因：

- 后端 env 文件语法错误；
- 完整源码部署时 `SECRET_KEY` 仍是默认占位值；
- 数据库没有健康启动；
- 手动覆盖了错误的 `DATABASE_URL`。

### Nginx 502 或页面空白

先检查本机服务：

```bash
curl http://127.0.0.1:8888/health
curl http://127.0.0.1:8889/health
curl http://127.0.0.1:5000/api/health
sudo nginx -t
```

再检查容器：

```bash
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 frontend
docker compose -f docker-compose.ghcr.yml logs --tail=100 backend
```

### 交易所或 LLM 出网需要代理

后端运行时请求外网需要代理时，在 `backend.env` 或 `backend_api_python/.env` 设置 `PROXY_URL`。

在 Docker 容器内不要直接写宿主机的 `127.0.0.1`，除非代理就在同一个容器里。可以使用容器可访问的宿主机地址，例如：

```ini
PROXY_URL=socks5h://host.docker.internal:10808
```

Linux 服务器上可能需要把代理监听到内网地址，或配置 Docker host gateway。

### 公网端口

不要公开这些端口：

- `5000`
- `5432`
- `6379`

公网只开放：

- `80`
- `443`
