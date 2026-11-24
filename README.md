# Project Visualizer

第一版支持从公开 GitHub 仓库链接或本地项目目录导入代码，生成目录树、文件节点依赖图、符号列表和代码预览。项目内容在本地解析，不需要 GitHub 登录或密钥配置。

运行：

```powershell
cd "C:\Users\27218\Documents\ChatGPT\Hackson 2\project_visualizer"
python server.py
```

打开 `http://127.0.0.1:8100`。

当前扫描器支持 JavaScript、TypeScript、Python、Java、Go、JSON、Markdown、CSS 和 HTML。GitHub 输入支持公开仓库链接，私有项目可以通过选择本地目录导入。
