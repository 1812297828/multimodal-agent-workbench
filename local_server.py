"""在本机运行 AgentScope、SQLite 存储和官方网页界面。"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentscope.app import create_app
from agentscope.app.hub import GitHubMCPHub
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.rag import ApproxTokenChunker, QdrantStore
from dispatch_routes import install_dispatch
from local_embedding import LocalEmbeddingCredential
from aoi_hubs import AOIMCPHub, AOISkillHub, default_mcps, skill_paths
from clawhub_local import LocalClawSkillHub

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "local_data"
RUNTIME = ROOT / ".runtime"
WEB = ROOT / "examples" / "web_ui" / "frontend" / "dist"
INSTANCE_ID = hashlib.sha256(os.path.normcase(str(ROOT)).encode()).hexdigest()
CONTROL = RUNTIME / "service.json"
TOKEN = secrets.token_urlsafe(32)
DATA.mkdir(exist_ok=True)

storage = AsyncSQLAlchemyStorage(
    "sqlite+aiosqlite:///" + (DATA / "agentscope.db").as_posix(),
    auto_migrate=True,
)
backend = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    workspace_manager=LocalWorkspaceManager(basedir=str(DATA / "workspaces"), default_mcps=default_mcps(), skill_paths=skill_paths()),
    extra_credentials=[LocalEmbeddingCredential],
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=QdrantStore(path=str(DATA / "vectors")),
    ),
    knowledge_chunkers=[ApproxTokenChunker],
    blob_store=LocalBlobStore(root_dir=DATA / "blobs"),
    mcp_hubs=[AOIMCPHub(), GitHubMCPHub()],
    skill_hubs=[AOISkillHub(), LocalClawSkillHub()],
    extra_middlewares=[Middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )],
)

@asynccontextmanager
async def local_lifespan(_):
    yield
    await dispatcher.close()


app = FastAPI(docs_url=None, openapi_url=None, lifespan=local_lifespan)
dispatcher = install_dispatch(app, DATA / "dispatch", ROOT / "dispatch_ui", native_chat=True)
dispatcher.mcp_storage = storage


@app.get("/_local/status")
async def local_status():
    return {"instance_id": INSTANCE_ID, "pid": os.getpid()}


@app.post("/_local/shutdown")
async def shutdown(x_local_token: str = Header(default="")):
    if not secrets.compare_digest(x_local_token, TOKEN):
        raise HTTPException(403, "本地控制凭据不匹配")
    for server in servers:
        server.should_exit = True
    return {"status": "stopping"}


class LocalWebUI(StaticFiles):
    """支持网页路由刷新，并预填本地连接和中文界面。"""

    async def get_response(self, path, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix:
                raise
            response = await super().get_response("index.html", scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            html = (WEB / "index.html").read_text(encoding="utf-8")
            bootstrap = """<script>
if (!localStorage.getItem('server_url') ||
    localStorage.getItem('server_url') === location.origin + '/api') {
  localStorage.setItem('server_url', 'http://127.0.0.1:8001');
}
if (!localStorage.getItem('username')) localStorage.setItem('username', 'local');
if (!localStorage.getItem('i18nextLng')) localStorage.setItem('i18nextLng', 'zh');
</script>"""
            html = html.replace('<html lang="en">', '<html lang="zh-CN">')
            return HTMLResponse(
                html.replace("<head>", "<head>" + bootstrap, 1),
                headers={"Cache-Control": "no-store"},
            )
        return response


app.mount("/", LocalWebUI(directory=str(WEB), html=True))
servers = [
    uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000)),
    uvicorn.Server(uvicorn.Config(backend, host="127.0.0.1", port=8001)),
]


async def serve_all():
    """在同一进程中运行网页和 API，分别执行各自的启动和清理。"""
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    RUNTIME.mkdir(exist_ok=True)
    CONTROL.write_text(
        json.dumps({"pid": os.getpid(), "token": TOKEN, "instance_id": INSTANCE_ID}),
        encoding="utf-8",
    )
    try:
        asyncio.run(serve_all())
    finally:
        if CONTROL.exists():
            record = json.loads(CONTROL.read_text(encoding="utf-8"))
            if record.get("pid") == os.getpid():
                CONTROL.unlink()
