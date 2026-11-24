from __future__ import annotations

import io
import json
import mimetypes
import re
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
IGNORED = {".git", "node_modules", "dist", "build", ".next", "coverage", ".venv", "venv", "__pycache__"}
EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".go", ".json", ".md", ".css", ".html"}


def should_keep(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and not any(part in IGNORED for part in parts) and Path(path).suffix.lower() in EXTENSIONS


def github_files(url: str) -> dict[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc not in {"github.com", "www.github.com"}:
        raise ValueError("目前只支持 github.com 仓库链接")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError("GitHub 链接应为 https://github.com/owner/repository")
    owner, repo = parts[:2]
    branch = None
    if len(parts) >= 4 and parts[2] == "tree": branch = "/".join(parts[3:])
    if branch is None:
        api_url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
        request = urllib.request.Request(api_url, headers={"User-Agent": "ProjectVisualizer/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                metadata = json.loads(response.read().decode("utf-8"))
            branch = metadata.get("default_branch") or "main"
        except Exception as exc:
            raise ValueError("无法读取仓库信息：仓库可能不存在、是私有仓库，或当前网络无法访问 GitHub") from exc
    candidates = [f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{urllib.parse.quote(branch)}"]
    data = None
    last_error = None
    for candidate in candidates:
        try:
            with urllib.request.urlopen(urllib.request.Request(candidate, headers={"User-Agent": "ProjectVisualizer/0.1"}), timeout=25) as response: data = response.read()
            break
        except Exception as exc: last_error = exc
    if data is None: raise ValueError("无法下载仓库代码：请确认仓库是公开的，链接包含正确的 owner/repository，且默认分支存在")
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in archive.infolist():
            name = "/".join(Path(item.filename).parts[1:])
            if name and not item.is_dir() and should_keep(name):
                try: files[name] = archive.read(item).decode("utf-8", errors="replace")
                except Exception: pass
    return files


def imports_for(path: str, text: str) -> list[str]:
    ext = Path(path).suffix.lower()
    patterns = {
        ".py": [r"^\s*(?:from|import)\s+([\w./-]+)"],
        ".js": [r"(?:from|import\s+.*?from)\s+['\"](.+?)['\"]", r"require\(['\"](.+?)['\"]\)"],
        ".jsx": [r"(?:from|import\s+.*?from)\s+['\"](.+?)['\"]"],
        ".ts": [r"(?:from|import\s+.*?from)\s+['\"](.+?)['\"]"],
        ".tsx": [r"(?:from|import\s+.*?from)\s+['\"](.+?)['\"]"],
        ".java": [r"^\s*import\s+([\w.]+)"],
        ".go": [r"\"([^\"]+)\""],
    }
    found = []
    for pattern in patterns.get(ext, []): found.extend(re.findall(pattern, text, re.MULTILINE))
    return list(dict.fromkeys(found))


def symbols_for(path: str, text: str) -> list[dict]:
    ext = Path(path).suffix.lower()
    patterns = {
        ".py": r"^\s*(?:async\s+)?def\s+(\w+)|^\s*class\s+(\w+)",
        ".js": r"(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()",
        ".jsx": r"(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()",
        ".ts": r"(?:function\s+(\w+)|class\s+(\w+)|interface\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()",
        ".tsx": r"(?:function\s+(\w+)|class\s+(\w+)|interface\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()",
        ".java": r"(?:class|interface|enum)\s+(\w+)|(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\(",
        ".go": r"(?:func|type)\s+(\w+)",
    }
    result = []
    for line, value in enumerate(text.splitlines(), 1):
        match = re.search(patterns.get(ext, r"$^"), value)
        if match:
            name = next((item for item in match.groups() if item), None)
            if name: result.append({"name": name, "line": line, "kind": "symbol"})
    return result


def analyze(files: dict[str, str], source: str) -> dict:
    kept = {path: text for path, text in files.items() if should_keep(path)}
    nodes = []
    edges = []
    path_set = set(kept)
    for path, text in kept.items():
        symbols = symbols_for(path, text)
        nodes.append({"id": path, "label": Path(path).name, "path": path, "type": "file", "language": Path(path).suffix.lower().lstrip("."), "lines": len(text.splitlines()), "symbols": symbols, "preview": text[:18000]})
        for imported in imports_for(path, text):
            normalized = imported.replace(".", "/")
            candidates = [p for p in path_set if p.endswith(imported) or p.endswith(normalized) or p.endswith(normalized + Path(p).suffix) or Path(p).stem == Path(imported).stem]
            if candidates: edges.append({"source": path, "target": candidates[0], "kind": "imports"})
    return {"source": source, "files": len(nodes), "nodes": nodes, "edges": edges, "languages": sorted({n["language"] for n in nodes if n["language"]})}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = FRONTEND / ("index.html" if self.path == "/" else self.path.lstrip("/"))
        if path.is_file():
            data = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        else: self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        self._handle_analyze()

    def _handle_analyze(self):
        if self.path != "/api/analyze": self.send_json(404, {"error": "Not found"}); return
        try:
            size = int(self.headers.get("Content-Length", 0)); payload = json.loads(self.rfile.read(size))
            if payload.get("source") == "github": files = github_files(payload.get("url", "")); source = payload["url"]
            elif payload.get("source") == "local": files = {item["path"]: item.get("content", "") for item in payload.get("files", [])}; source = "local folder"
            else: raise ValueError("source 必须是 github 或 local")
            self.send_json(200, analyze(files, source))
        except Exception as exc: self.send_json(400, {"error": str(exc)})


if __name__ == "__main__":
    print("Project Visualizer running at http://127.0.0.1:8100", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8100), Handler).serve_forever()
