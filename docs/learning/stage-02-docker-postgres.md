# Stage 2：Docker 与 PostgreSQL 基础

## 本节目标

完成本节后，你应该能够解释：

1. 镜像、容器、端口映射和命名卷分别是什么。
2. 为什么项目使用 Compose，而不是让每个人手动安装 PostgreSQL。
3. 容器删除后，数据库数据为什么仍然可以保留。
4. pgvector 是什么，以及它和 PostgreSQL 的关系。
5. 为什么 `docker compose down -v` 是需要谨慎执行的命令。

本节只建立数据库运行环境，不编写 SQLAlchemy 模型。

## 1. 先理解四个概念

### 镜像（Image）

镜像是只读的软件模板。`pgvector/pgvector:0.8.2-pg17` 已经包含 PostgreSQL 17 和 pgvector 0.8.2。

可以把镜像理解成安装包，但它还包含运行程序所需的文件和基础系统环境。

### 容器（Container）

容器是镜像的一次运行实例。同一个镜像可以创建多个容器，就像同一个 Python 类可以创建多个对象。

### 端口映射（Ports）

Compose 中：

```yaml
ports:
  - "5432:5432"
```

左侧是 Windows 主机端口，右侧是容器内部端口。FastAPI 在 Windows 上开发时，通过 `localhost:5432` 连接容器中的 PostgreSQL。

### 命名卷（Named Volume）

容器可以删除和重建，但数据库数据不能跟着消失。`postgres_data` 把数据保存在容器生命周期之外：

```text
PostgreSQL 容器 -> /var/lib/postgresql/data -> postgres_data 命名卷
```

## 2. Compose 文件做了什么

[compose.yaml](../../compose.yaml) 定义了一个 `postgres` 服务：

- 使用固定版本的 pgvector 镜像，避免不同时间拉取到不兼容版本。
- 从 `.env` 或默认值读取数据库名称、用户、密码和端口。
- 把主机的 5432 端口映射到容器的 5432 端口。
- 使用命名卷持久化数据。
- 通过 `pg_isready` 判断数据库是否真正可以接受连接。
- 首次创建数据卷时执行初始化 SQL。

初始化脚本 [001-enable-vector.sql](../../infra/postgres/init/001-enable-vector.sql) 会执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

pgvector 不是独立数据库，而是 PostgreSQL 扩展。启用扩展后，PostgreSQL 才认识 `vector` 数据类型和向量距离运算符。

## 3. 启动 Docker Desktop

从 Windows 开始菜单打开 Docker Desktop，等待界面显示引擎已经运行，然后执行：

```powershell
docker version
```

正确结果应该同时包含 `Client` 和 `Server`。只有 Client 表示命令行安装了，但 Docker 后台引擎没有运行。

如果 Docker Desktop 提示 WSL 更新，以管理员身份运行：

```powershell
wsl --update
```

完成后重启 Docker Desktop。

## 4. 准备本地环境变量

在项目根目录执行一次：

```powershell
Copy-Item .env.example .env
```

`.env` 已被 `.gitignore` 排除，不会提交到 Git。本阶段使用的密码只是本机开发密码，不能用于公网部署。

检查 Compose 最终读取到的配置：

```powershell
docker compose config
```

这个命令只解析配置，不启动容器。看到 `postgres` 服务、端口和 `postgres_data` 卷说明结构有效。

## 5. 启动 PostgreSQL

```powershell
docker compose up -d postgres
docker compose ps
```

- `up` 创建并启动服务。
- `-d` 表示在后台运行，不占住当前终端。
- `postgres` 表示本次只启动这个服务。
- `ps` 查看 Compose 管理的容器状态。

首次启动需要下载镜像。最终状态应显示为 `healthy`。如果没有成功，查看日志：

```powershell
docker compose logs postgres
```

## 6. 验证 PostgreSQL 和 pgvector

在容器中运行 PostgreSQL 自带的命令行客户端：

```powershell
docker compose exec postgres psql -U rag_user -d enterprise_rag -c "SELECT version();"
```

检查 pgvector：

```powershell
docker compose exec postgres psql -U rag_user -d enterprise_rag -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
```

结果中应该出现 `vector` 和扩展版本。

## 7. 验证数据持久化

先创建一张只用于学习验证的表并写入一行：

```powershell
docker compose exec postgres psql -U rag_user -d enterprise_rag -c "CREATE TABLE IF NOT EXISTS learning_check (message text NOT NULL); INSERT INTO learning_check VALUES ('volume works');"
```

停止并重新启动容器：

```powershell
docker compose stop postgres
docker compose start postgres
```

再次查询：

```powershell
docker compose exec postgres psql -U rag_user -d enterprise_rag -c "SELECT * FROM learning_check;"
```

如果仍能看到 `volume works`，说明数据保存在命名卷中，而不是只存在于容器可写层。

## 8. 常用命令与风险

```powershell
docker compose ps
docker compose logs postgres
docker compose stop postgres
docker compose start postgres
docker compose down
```

`docker compose down` 会删除容器和网络，但默认保留命名卷，下次启动数据仍在。

下面的命令会连同数据库卷一起删除：

```powershell
docker compose down -v
```

除非明确需要清空本地数据库，否则不要执行它。以后项目产生正式测试数据后更要谨慎。

## 9. 你的 Stage 2 环境任务

完成以下步骤：

1. 启动 Docker Desktop。
2. 创建本地 `.env`。
3. 执行 `docker compose config`。
4. 启动 `postgres` 并确认状态为 `healthy`。
5. 查询 PostgreSQL 版本。
6. 查询 pgvector 扩展版本。
7. 完成停止、启动后的数据持久化验证。

请把下面四项命令的输出发给我：

```powershell
docker version
docker compose ps
docker compose exec postgres psql -U rag_user -d enterprise_rag -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
docker compose exec postgres psql -U rag_user -d enterprise_rag -c "SELECT * FROM learning_check;"
```

## 自检问题

1. 镜像与容器有什么区别？
2. 为什么停止或删除容器后，数据库数据仍然可能存在？
3. `5432:5432` 左右两侧分别代表什么？
4. pgvector 是独立数据库，还是 PostgreSQL 的扩展？
5. `docker compose down` 和 `docker compose down -v` 有什么关键区别？

