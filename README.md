# Project Visualizer

第一版支持从 GitHub 公共仓库链接或本地项目目录导入代码，生成目录树、文件节点依赖图、符号列表和代码预览。

运行：

```powershell
cd "C:\Users\27218\Documents\ChatGPT\Hackson 2\project_visualizer"
python server.py
```

打开 `http://127.0.0.1:8100`。

当前扫描器支持 JavaScript、TypeScript、Python、Java、Go、JSON、Markdown、CSS 和 HTML。公开仓库可以直接读取；私有仓库通过 GitHub App OAuth 读取。先设置环境变量（不要把 Client Secret 或 PEM 私钥提交到 Git）：

```powershell
$env:GITHUB_CLIENT_ID="你的 Client ID"
$env:GITHUB_CLIENT_SECRET="新生成的 Client Secret"
$env:GITHUB_REDIRECT_URI="http://127.0.0.1:8100/api/github/callback"
python server.py
```

私有仓库授权需要 GitHub App 的 `Contents: Read-only` 和 `Metadata: Read-only` 权限，并在 GitHub App 中安装到要分析的仓库。GitHub App 的私钥用于后续安装 Token 流程；当前本地 OAuth 读取流程只从后端环境变量读取密钥。
