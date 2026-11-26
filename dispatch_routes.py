"""在本地桌面服务中提供多模型调度页面和接口。"""

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from dispatch_engine import DispatchConfig, Dispatcher, TaskInput
from library_client import LocalKnowledgeClient
from project_client import LocalProjectClient
from project_index import refresh as refresh_project_index
from aoi_tools import tool_environment, check_sources, build_profile
import asyncio


def install_dispatch(app, data: Path, assets: Path, allowed_origins=None, native_chat=False):
    engine = Dispatcher(data)
    engine.knowledge_provider = LocalKnowledgeClient(data.parent / "library/config.json")
    engine.project_provider = LocalProjectClient()
    project_refresh_lock = asyncio.Lock()
    router = APIRouter(prefix="/dispatch")
    origins = allowed_origins or {"http://127.0.0.1:8000", "http://localhost:8000"}

    async def payload(request):
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            raise HTTPException(403, "只接受本机调度界面提交")
        if request.headers.get("x-dispatch-client") != "local":
            raise HTTPException(403, "缺少本机调度请求标识")
        body = await request.body()
        if len(body) > 10_000_000:
            raise HTTPException(413, "提交内容过大，请减少输入材料")
        try:
            return json.loads(body) if body else {}
        except ValueError:
            raise HTTPException(400, "请求内容不是有效 JSON") from None

    def failure(exc):
        if isinstance(exc, ValidationError):
            messages = ["配置或输入不符合要求：" + str(item["msg"]) for item in exc.errors(include_input=False)]
            return HTTPException(400, "；".join(messages[:4]))
        return HTTPException(400, str(exc)[:600])

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse)
    async def page(request: Request):
        if native_chat and request.query_params.get("settings") != "1":
            return RedirectResponse("/chat")
        return HTMLResponse((assets / "index.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @router.get("/app.js")
    async def script():
        return FileResponse(assets / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @router.get("/style.css")
    async def stylesheet():
        return FileResponse(assets / "style.css", media_type="text/css", headers={"Cache-Control": "no-store"})

    @router.get("/theme.css")
    async def theme():
        return FileResponse(assets / "theme.css", media_type="text/css", headers={"Cache-Control": "no-store"})

    @router.get("/window.js")
    async def window_script():
        return FileResponse(assets / "window.js", media_type="text/javascript", headers={"Cache-Control": "no-store"})

    @router.get("/api/config")
    async def config():
        try:
            return JSONResponse(engine.store.public(), headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise failure(exc) from None

    @router.get("/api/library")
    async def library():
        return engine.knowledge_provider.status()

    @router.post("/api/billing/refresh")
    async def refresh_billing(request: Request):
        await payload(request)
        return await engine.refresh_billing()

    @router.get("/api/projects")
    async def projects():
        return engine.project_provider.status()

    @router.get("/api/tools")
    async def tools_status():
        return await asyncio.to_thread(tool_environment)

    @router.get("/api/project-mcps")
    async def project_mcps():
        from dispatch_mcp import DispatchMCP, installed_plugins
        return {"plugins": DispatchMCP(await installed_plugins(engine.mcp_storage)).catalog()}

    @router.post("/api/projects/search")
    async def search_code(request: Request):
        raw = await payload(request)
        try:
            return {"matches": await asyncio.to_thread(engine.project_provider.index.search, str(raw.get("repository", "")), str(raw.get("query", ""))[:500], 12)}
        except (ValueError, OSError) as exc:
            raise failure(exc) from None

    @router.post("/api/projects/read")
    async def read_code(request: Request):
        raw = await payload(request)
        try:
            return await asyncio.to_thread(engine.project_provider.index.read, str(raw.get("repository", "")), str(raw.get("path", "")), int(raw.get("start_line", 1)), int(raw.get("end_line", 100)))
        except (ValueError, OSError) as exc:
            raise failure(exc) from None

    @router.post("/api/projects/check")
    async def check_code(request: Request):
        raw = await payload(request)
        try:
            if not isinstance(raw.get("paths"), list) or any(not isinstance(item, str) for item in raw["paths"]):
                raise ValueError("请选择代码文件路径")
            return await asyncio.to_thread(check_sources, str(raw.get("repository", "")), raw["paths"])
        except (ValueError, OSError, subprocess.TimeoutExpired, ET.ParseError) as exc:
            raise failure(exc) from None

    @router.post("/api/projects/profile")
    async def project_profile(request: Request):
        raw = await payload(request)
        try:
            return await asyncio.to_thread(build_profile, str(raw.get("repository", "")), str(raw.get("path", "")))
        except (ValueError, OSError, ET.ParseError) as exc:
            raise failure(exc) from None

    @router.post("/api/projects/refresh")
    async def refresh_projects(request: Request):
        await payload(request)
        if project_refresh_lock.locked():
            raise HTTPException(409, "代码索引正在更新，请稍候")
        async with project_refresh_lock:
            return await asyncio.to_thread(refresh_project_index)

    @router.put("/api/config")
    async def update_config(request: Request):
        raw = await payload(request)
        try:
            return engine.store.update(DispatchConfig.model_validate(raw))
        except ValueError as exc:
            raise failure(exc) from None

    @router.get("/api/routes")
    async def routes():
        return engine.preview()

    @router.post("/api/platforms/{platform_id}/keys/{key_id}/reveal")
    async def reveal_platform_key(platform_id: str, key_id: str, request: Request):
        await payload(request)
        try:
            return JSONResponse({"secret": engine.store.reveal_key(platform_id, key_id)},
                                headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise failure(exc) from None

    @router.post("/api/models/{model_id}/test")
    async def test_model(model_id: str, request: Request):
        raw = await payload(request)
        try:
            return await engine.test_model(model_id, raw.get("draft"))
        except ValueError as exc:
            raise failure(exc) from None

    @router.post("/api/models/{model_id}/catalog")
    async def catalog(model_id: str, request: Request):
        raw = await payload(request)
        config, keys = engine.store.load()
        config, keys = engine.store.apply_draft(config, keys, model_id, raw.get("draft"))
        item = next((item for item in config.models if item.id == model_id), None)
        if item is None:
            raise HTTPException(400, "模型配置不存在")
        if not item.base_url:
            raise HTTPException(400, "请填写服务地址 Base URL；当前表单尚未保存也可以直接读取")
        if not engine.store.resolve(config, keys, model_id):
            raise HTTPException(400, "请填写 API key，或选择已有的密钥来源；当前表单尚未保存也可以直接读取")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                base = item.base_url.rstrip("/")
                paths = [base + "/models"]
                if not base.endswith("/v1"):
                    paths.append(base + "/v1/models")
                for path in paths:
                    response = await client.get(path, headers={"Authorization": "Bearer " + engine.store.resolve(config, keys, model_id)})
                    if response.is_success:
                        result = response.json()
                        break
                else:
                    response.raise_for_status()
            models = [row["id"] for row in result.get("data", []) if isinstance(row, dict) and isinstance(row.get("id"), str)]
            if not models:
                raise ValueError("服务商返回的模型列表为空；可以直接手动填写模型 ID")
            return {"models": sorted(set(models))[:1000]}
        except Exception as exc:
            raise HTTPException(400, "读取模型列表失败：" + engine._clean_error(exc, keys)) from None

    @router.post("/api/platforms/{platform_id}/catalog")
    async def platform_catalog(platform_id: str, request: Request):
        raw = await payload(request)
        config, keys = engine.store.load()
        try:
            config, keys = engine.store.apply_platform_draft(config, keys, platform_id, raw.get("draft"))
        except ValueError as exc:
            raise failure(exc) from None
        platform = next((item for item in config.platforms if item.id == platform_id), None)
        values = engine.store._key_values(keys, platform_id)
        api_keys = [value for entry, value in zip(platform.key_entries or [], values) if entry.enabled]
        if platform is None:
            raise HTTPException(400, "平台配置不存在")
        if not platform.base_url:
            raise HTTPException(400, "请填写平台服务地址")
        if not api_keys:
            raise HTTPException(400, "请填写平台 API key；支持每行一个备用 key")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                base = platform.base_url.rstrip("/")
                paths = [base + "/models"] + ([] if base.endswith("/v1") else [base + "/v1/models"])
                result = None
                last_error = None
                for key in api_keys:
                    try:
                        for path in paths:
                            response = await client.get(path, headers={"Authorization": "Bearer " + key})
                            if response.is_success:
                                result = response.json()
                                break
                            if response.status_code not in {401, 403, 404, 429}:
                                response.raise_for_status()
                            try:
                                response.raise_for_status()
                            except httpx.HTTPStatusError as exc:
                                last_error = exc
                            if response.status_code in {401, 403, 429}:
                                break
                    except httpx.RequestError as exc:
                        last_error = exc
                    if result is not None:
                        break
                if result is None:
                    raise last_error or ValueError("平台未返回模型列表")
            models = sorted({row["id"] for row in result.get("data", []) if isinstance(row, dict) and isinstance(row.get("id"), str)})[:1000]
            if not models:
                raise ValueError("服务商返回的模型列表为空")
            return {"models": models, "platform_id": platform_id}
        except Exception as exc:
            raise HTTPException(400, "读取平台模型列表失败：" + engine._clean_error(exc, keys)) from None

    @router.get("/api/jobs")
    async def jobs():
        return {"jobs": engine.list_jobs()}

    @router.get("/api/conversations")
    async def conversations():
        return {"conversations": engine.list_conversations()}

    @router.post("/api/conversations")
    async def create_conversation(request: Request):
        await payload(request)
        return JSONResponse(engine.create_conversation(), status_code=201)

    @router.get("/api/conversations/{conversation_id}")
    async def conversation(conversation_id: str):
        try:
            return engine.conversation(conversation_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str, request: Request):
        await payload(request)
        try:
            await engine.delete_conversation(conversation_id)
        except ValueError as exc:
            raise failure(exc) from None
        except OSError:
            raise HTTPException(500, "无法写入本地删除记录，请检查数据目录权限。") from None
        return {"ok": True}

    @router.post("/api/conversations/{conversation_id}/messages")
    async def conversation_message(conversation_id: str, request: Request):
        raw = await payload(request)
        if not isinstance(raw, dict):
            raise HTTPException(400, "消息内容必须是 JSON 对象")
        try:
            engine.conversation(conversation_id)
            mode = raw.get("chat_mode", "auto")
            if mode not in ("auto", "single", "collaboration"):
                raise ValueError("请选择单模型或多模型模式。")
            task = TaskInput.model_validate({**raw, "conversation_id": conversation_id, "mode": mode})
            return JSONResponse(await engine.start(task), status_code=202)
        except ValueError as exc:
            raise failure(exc) from None

    @router.post("/api/jobs")
    async def create_job(request: Request):
        raw = await payload(request)
        try:
            task = TaskInput.model_validate(raw)
            return JSONResponse(await engine.start(task), status_code=202)
        except ValueError as exc:
            raise failure(exc) from None

    def get_job(job_id):
        job = engine.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        return job

    @router.get("/api/jobs/{job_id}")
    async def job(job_id: str):
        return get_job(job_id)

    @router.get("/api/jobs/{job_id}/changes")
    async def changes(job_id: str):
        try:
            tracked = engine.project_changes(get_job(job_id))
            return {"files": tracked.list() if tracked else []}
        except (ValueError, OSError) as exc:
            raise failure(exc) from None

    @router.get("/api/jobs/{job_id}/changes/{change_id}")
    async def change_detail(job_id: str, change_id: str):
        try:
            tracked = engine.project_changes(get_job(job_id))
            if tracked is None:
                raise ValueError("本轮没有改动记录。")
            return tracked.detail(change_id)
        except (ValueError, OSError) as exc:
            raise failure(exc) from None

    @router.post("/api/jobs/{job_id}/changes/{change_id}/restore")
    async def restore_change(job_id: str, change_id: str, request: Request):
        await payload(request)
        try:
            return await engine.restore_project_change(get_job(job_id), change_id)
        except (ValueError, OSError) as exc:
            raise failure(exc) from None

    @router.post("/api/jobs/{job_id}/terminal/{request_id}")
    async def terminal_decision(job_id: str, request_id: str, request: Request):
        raw = await payload(request)
        if not isinstance(raw, dict) or type(raw.get("approved")) is not bool:
            raise HTTPException(400, "请选择运行或跳过这条命令。")
        try:
            return engine.decide_terminal(job_id, request_id, raw["approved"])
        except ValueError as exc:
            raise failure(exc) from None

    @router.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        await payload(request)
        get_job(job_id)
        return await engine.cancel(job_id)

    @router.get("/api/jobs/{job_id}/report")
    async def report(job_id: str):
        job = get_job(job_id)
        from fastapi.responses import Response
        return Response(
            engine.export(job), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="agentscope-report.md"'},
        )

    app.include_router(router)
    return engine
