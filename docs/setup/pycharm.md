# PyCharm 黄色警告排查

黄色波浪线通常表示 IDE 的静态检查警告，不等于程序一定运行失败。当前项目最可能有
两个原因：解释器版本不正确，或者 PyCharm 不知道 `backend` 是 Python 源码根目录。

## 1. 用正确方式打开项目

在 PyCharm 中选择：

```text
File -> Open -> D:\xuexi\projects\agent_project
```

不要只打开某一个 `.py` 文件。左侧 Project 应该能同时看到 `backend`、`docs` 和
`README.md`。

## 2. 配置 Python 3.11 虚拟环境

先按照 `docs/setup/windows.md` 安装 Python 3.11 并创建项目根目录下的 `.venv`。

然后在 PyCharm 中进入：

```text
File -> Settings -> Project: agent_project -> Python Interpreter
```

点击 `Add Interpreter -> Add Local Interpreter -> Existing`，选择：

```text
D:\xuexi\projects\agent_project\.venv\Scripts\python.exe
```

如果右下角仍显示 Python 3.9，说明项目解释器没有切换成功。

## 3. 把 backend 标记为源码目录

在左侧 Project 面板中：

```text
右键 backend -> Mark Directory as -> Sources Root
```

设置成功后，`backend` 文件夹通常会显示为蓝色。这样 PyCharm 才知道
`from app...` 中的 `app` 位于哪里。

## 4. 等待索引并刷新

切换解释器后，等待 PyCharm 底部的索引进度完成。如果依然标黄：

```text
File -> Invalidate Caches -> Invalidate and Restart
```

这一步只清理 PyCharm 索引，不会删除项目代码。

## 5. 不要盲目忽略所有黄色警告

黄色提示也可能是未使用的导入、拼写检查或类型提示。把鼠标停在黄色波浪线上，PyCharm
会显示具体原因。若完成上述配置后仍有警告，请截图时同时包含：

- 黄色波浪线所在代码；
- 鼠标悬停后出现的完整英文提示；
- PyCharm 右下角显示的 Python 版本。

有了这三项，才能准确判断是环境问题还是代码问题。
