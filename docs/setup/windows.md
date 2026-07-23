# Windows 环境准备

这份文档只安装项目真正需要的工具。不要在系统 Python 3.9 中直接安装本项目依赖。

## 1. 当前电脑检查结果

- Windows 10 家庭中文版
- 内存 16 GB
- Python 3.9.9 已安装，但低于本项目要求
- `python -m pip` 可用，独立的 `pip` 命令未加入 PATH
- Git 已安装并能在 PowerShell 中使用
- Node.js 和 npm 已安装
- Docker 尚未安装或尚未加入 PATH
- WSL 2 和 BIOS 虚拟化状态仍需确认

本项目统一使用 Python 3.11。选择 3.11 而不是追求最新版本，是为了兼顾 Agent 生态兼容性和较长的维护周期。

## 2. 安装 Python 3.11

从 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/) 安装最新的 Python 3.11.x 64 位版本。安装界面需要勾选：

- `Add python.exe to PATH`
- `Install launcher for all users`

安装后关闭并重新打开 PowerShell，执行：

```powershell
py -0p
py -3.11 --version
```

第一条命令列出电脑上的所有 Python。第二条应该输出 `Python 3.11.x`。

## 3. 创建项目虚拟环境

在项目根目录创建虚拟环境：

```powershell
cd D:\xuexi\projects\agent_project
py -3.11 -m venv .venv
```

如果你使用 **PowerShell**，激活命令是：

```powershell
.\.venv\Scripts\Activate.ps1
python --version
```

如果 PowerShell 阻止激活脚本，仅对当前用户设置一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

如果你使用 **CMD**（提示符类似 `D:\xuexi\projects>`），激活命令是：

```bat
cd /d D:\xuexi\projects\agent_project
.venv\Scripts\activate.bat
python --version
```

激活成功后，命令行开头会出现 `(.venv)`。执行 `where python` 时，第一项必须是：

```text
D:\xuexi\projects\agent_project\.venv\Scripts\python.exe
```

提示符出现 `(.venv)` 后，安装后端依赖：

```powershell
python -m pip install --upgrade pip
cd .\backend
python -m pip install -e ".[dev]"
cd ..
```

虚拟环境的作用是把本项目依赖与系统 Python 隔离。删除 `.venv` 不会删除源码，依赖也可以根据 `pyproject.toml` 重装。

## 4. 验证 Git

执行：

```powershell
git --version
git status
```

你当前只需要理解四条命令：

```powershell
git status
git diff
git add <文件>
git commit -m "说明这次完成了什么"
```

暂时不要背更多命令，也不要在不理解时使用 `reset --hard`。

## 5. 安装 Docker Desktop

安装前，在“任务管理器 -> 性能 -> CPU”查看“虚拟化”是否显示“已启用”。如果未启用，需要进入 BIOS 开启 Intel VT-x 或 AMD-V。

从 [Docker Desktop 官方安装页](https://docs.docker.com/desktop/setup/install/windows-install/) 下载安装程序。安装前，以管理员身份打开 PowerShell：

```powershell
wsl --install
wsl --update
```

重启电脑，安装 Docker Desktop，并在设置中选择 WSL 2 后端。安装完成后验证：

```powershell
wsl --status
docker version
docker compose version
docker run --rm hello-world
```

本阶段不要求你理解 Dockerfile。先把 Docker 理解成“用统一方式运行 PostgreSQL、Redis 等外部服务”。

## 6. 启动当前后端

先激活虚拟环境，然后执行：

```powershell
cd D:\xuexi\projects\agent_project\backend
python -m uvicorn app.main:app --reload
```

这里刻意使用 `python -m uvicorn`，确保调用的是当前虚拟环境中的 Uvicorn，避免误用全局 Python 的命令。

浏览器访问：

- Swagger 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

运行测试：

```powershell
cd D:\xuexi\projects\agent_project\backend
python -m pytest
python -m ruff check .
```

## 7. 常见错误

### `py -3.11` 找不到版本

Python 3.11 没有安装成功，或安装后尚未重开 PowerShell。先执行 `py -0p` 查看实际版本。

### `uvicorn` 不是命令

通常是没有激活 `.venv`，或者依赖安装失败。可以直接执行：

```powershell
python -m uvicorn app.main:app --reload
```

### `docker` 不是命令

Docker Desktop 尚未安装完成、没有启动，或安装后没有重新打开终端。

### 端口 8000 被占用

临时换一个端口：

```powershell
uvicorn app.main:app --reload --port 8001
```
