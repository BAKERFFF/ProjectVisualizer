from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"

def load_dotenv(path: Path) -> None:
    if not path.is_file(): return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

load_dotenv(ROOT / ".env")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI", "http://127.0.0.1:8100/api/github/callback")
OAUTH_STATES: dict[str, float] = {}
USER_TOKENS: dict[str, str] = {}
IGNORED = {".git", "node_modules", "dist", "build", ".next", "coverage", ".venv", "venv", "__pycache__"}
EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".go", ".json", ".md", ".css", ".html"}


def should_keep(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and not any(part in IGNORED for part in parts) and Path(path).suffix.lower() in EXTENSIONS


def github_files(url: str, token: str | None = None) -> dict[str, str]:
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
        headers = {"User-Agent": "ProjectVisualizer/0.1"}
        if token: headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(api_url, headers=headers)
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
            headers = {"User-Agent": "ProjectVisualizer/0.1"}
            if token: headers["Authorization"] = f"Bearer {token}"
            with urllib.request.urlopen(urllib.request.Request(candidate, headers=headers), timeout=25) as response: data = response.read()
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

    def cookies(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return {key: morsel.value for key, morsel in cookie.items()}

    def redirect(self, location: str, cookies: list[str] | None = None):
        self.send_response(302); self.send_header("Location", location)
        for value in cookies or []: self.send_header("Set-Cookie", value)
        self.end_headers()

    def github_token(self) -> str | None:
        return USER_TOKENS.get(self.cookies().get("pv_session", ""))

    def do_GET(self):
        path = FRONTEND / ("index.html" if self.path == "/" else self.path.lstrip("/"))
        route = urllib.parse.urlparse(self.path)
        if route.path == "/api/github/login":
            if not GITHUB_CLIENT_ID: self.send_json(503, {"error": "尚未配置 GITHUB_CLIENT_ID"}); return
            state = secrets.token_urlsafe(24); OAUTH_STATES[state] = time.time() + 600
            query = urllib.parse.urlencode({"client_id": GITHUB_CLIENT_ID, "redirect_uri": GITHUB_REDIRECT_URI, "state": state, "allow_signup": "false"})
            self.redirect(f"https://github.com/login/oauth/authorize?{query}", [f"oauth_state={state}; HttpOnly; SameSite=Lax; Path=/"]); return
        if route.path == "/api/github/callback":
            params = urllib.parse.parse_qs(route.query); code = params.get("code", [""])[0]; state = params.get("state", [""])[0]
            if not code or not state or state not in OAUTH_STATES or OAUTH_STATES.pop(state) < time.time(): self.send_json(400, {"error": "GitHub OAuth state 无效或已过期"}); return
            payload = urllib.parse.urlencode({"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET, "code": code, "redirect_uri": GITHUB_REDIRECT_URI, "state": state}).encode()
            request = urllib.request.Request("https://github.com/login/oauth/access_token", data=payload, headers={"Accept": "application/json", "User-Agent": "ProjectVisualizer/0.1"})
            try:
                with urllib.request.urlopen(request, timeout=20) as response: token_data = json.loads(response.read().decode())
                token = token_data.get("access_token")
                if not token: raise ValueError(token_data.get("error_description", "GitHub 未返回 access token"))
                session = secrets.token_urlsafe(32); USER_TOKENS[session] = token
                self.redirect("/", [f"pv_session={session}; HttpOnly; SameSite=Lax; Path=/"])
            except Exception as exc: self.send_json(400, {"error": f"GitHub 授权失败：{exc}"})
            return
        if route.path == "/api/github/status":
            token = self.github_token()
            if not token: self.send_json(200, {"connected": False}); return
            try:
                request = urllib.request.Request("https://api.github.com/user", headers={"Authorization": f"Bearer {token}", "User-Agent": "ProjectVisualizer/0.1"})
                with urllib.request.urlopen(request, timeout=15) as response: user = json.loads(response.read().decode())
                self.send_json(200, {"connected": True, "login": user.get("login")})
            except Exception: self.send_json(200, {"connected": False})
            return
        if path.is_file():
            data = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        else: self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/github/disconnect":
            session = self.cookies().get("pv_session"); USER_TOKENS.pop(session or "", None)
            self.send_json(200, {"ok": True}); return
        self._handle_analyze()

    def _handle_analyze(self):
        if self.path != "/api/analyze": self.send_json(404, {"error": "Not found"}); return
        try:
            size = int(self.headers.get("Content-Length", 0)); payload = json.loads(self.rfile.read(size))
            if payload.get("source") == "github": files = github_files(payload.get("url", ""), self.github_token()); source = payload["url"]
            elif payload.get("source") == "local": files = {item["path"]: item.get("content", "") for item in payload.get("files", [])}; source = "local folder"
            else: raise ValueError("source 必须是 github 或 local")
            self.send_json(200, analyze(files, source))
        except Exception as exc: self.send_json(400, {"error": str(exc)})


if __name__ == "__main__":
    print("Project Visualizer running at http://127.0.0.1:8100", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8100), Handler).serve_forever()
