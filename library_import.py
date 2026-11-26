"""把本机资料整理成可追溯的知识库；只读取原目录，不运行其中的程序。"""

import argparse
import csv
from collections import defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import zipfile

import httpx

from dispatch_engine import now, save_json

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "local_data/library"
CONFIG = DATA / "config.json"
STATE = DATA / "import-state.json"
API = "http://127.0.0.1:8001"
TEXT_TYPES = {".md", ".txt", ".py", ".cpp", ".h", ".js", ".cjs", ".cmd", ".css", ".patch", ".log"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


def decode(raw):
    encodings = ["utf-16", "utf-8-sig", "gb18030"] if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def extract(path, raw):
    suffix = path.suffix.lower()
    if suffix == ".html":
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(decode(raw), "lxml")
        structured = []

        def json_text(value):
            if isinstance(value, str):
                if value.lstrip().startswith("data:image/"):
                    return "[内嵌图片保留在原文件中，尚未视觉解读]"
                if re.search(r"<\/?[a-zA-Z][^>]*>", value):
                    fragment = BeautifulSoup(value, "lxml")
                    for element in fragment(["script", "style", "svg"]):
                        element.decompose()
                    return fragment.get_text("\n", strip=True)
                return value
            if isinstance(value, list):
                return "\n\n".join(filter(None, (json_text(item) for item in value)))
            if isinstance(value, dict):
                labels = {"sourceSummary": "适用源码范围", "sourcePaths": "源码路径", "updated": "资料更新日期",
                          "term": "条目", "label": "说明项", "question": "问题", "cause": "原因", "solution": "处理方法",
                          "verification": "核对情况", "environment": "适用环境", "title": "标题", "html": "正文"}
                lines = []
                for key, item in value.items():
                    if key in {"aliases", "categories", "defaultEntry", "mergedTopicIds", "id", "category", "topicId", "entryId", "author"}:
                        continue
                    text = json_text(item)
                    if text:
                        lines.append(labels.get(key, key) + "：\n" + text)
                return "\n\n".join(lines)
            return str(value) if value is not None else ""

        for script in soup.find_all("script", type="application/json"):
            if script.get("id") in {"dictionary-template", "topic-aliases"}:
                continue
            value = json.loads(script.string or script.get_text())
            structured.append(json_text(value))
        for item in soup(["script", "style", "noscript", "svg"]):
            item.decompose()
        return "\n\n".join([soup.get_text("\n", strip=True), *structured]), "正文"
    if suffix == ".docx":
        from docx import Document
        document = Document(io.BytesIO(raw))
        paragraphs = [paragraph.text for paragraph in document.paragraphs]
        paragraphs += [" | ".join(cell.text for cell in row.cells) for table in document.tables for row in table.rows]
        return "\n".join(paragraphs), "正文"
    if suffix == ".pptx":
        from pptx import Presentation
        slides = Presentation(io.BytesIO(raw)).slides
        return "\n\n".join(f"第 {index + 1} 页\n" + "\n".join(shape.text for shape in slide.shapes if shape.has_text_frame)
                              for index, slide in enumerate(slides)), "正文"
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n\n".join(f"第 {index + 1} 页\n{page.extract_text() or ''}"
                              for index, page in enumerate(PdfReader(io.BytesIO(raw)).pages)), "正文"
    if suffix == ".json":
        text = decode(raw)
        if len(text) < 18000:
            return text, "正文"
        value = json.loads(text)
        def compact(item, depth=0):
            if depth > 3:
                return f"（{type(item).__name__}，详见原文件）"
            if isinstance(item, list):
                return {"条目总数": len(item), "前3条样例": [compact(row, depth + 1) for row in item[:3]]}
            if isinstance(item, dict):
                return {str(key): compact(val, depth + 1) for key, val in list(item.items())[:60]}
            return item
        return "大型 JSON 结构和样例（不是全部原始数据）：\n" + json.dumps(compact(value), ensure_ascii=False, indent=2), "结构摘要"
    if suffix in TEXT_TYPES:
        text = decode(raw)
        text = re.sub(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=\s]+", "[内嵌图片，详见原文件]", text)
        if len(text) > 120000:
            return text[:100000] + "\n\n[中间部分未纳入向量索引，完整内容见原文件]\n\n" + text[-12000:], "长文本节选"
        return text, "正文"
    return "", "附件目录"


def csv_summary(raw, delimiter=","):
    rows = csv.reader(io.StringIO(decode(raw)), delimiter=delimiter)
    header = next(rows, [])
    examples = []
    statistics = {}
    count = 0
    for row in rows:
        if not row:
            continue
        count += 1
        if len(examples) < 2:
            examples.append(row)
        for index, cell in enumerate(row[:40]):
            try:
                number = float(cell)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                continue
            entry = statistics.setdefault(index, [0, 0.0, number, number])
            entry[0] += 1
            entry[1] += number
            entry[2] = min(entry[2], number)
            entry[3] = max(entry[3], number)
    lines = [f"数据行数：{count}", "字段：" + " | ".join(header[:40]),
             "前两行样例：" + json.dumps(examples, ensure_ascii=False)]
    for index, (amount, total, minimum, maximum) in statistics.items():
        label = header[index] if index < len(header) else f"列{index + 1}"
        lines.append(f"{label}：数值条数={amount}，最小={minimum:.8g}，最大={maximum:.8g}，均值={total / amount:.8g}")
    return "\n".join(lines)[:10000]


def prepare(source):
    files = sorted((path for path in source.rglob("*") if path.is_file()), key=lambda path: str(path.relative_to(source)).casefold())
    manifest = []
    documents = []
    groups = defaultdict(list)
    seen = {}
    stats = defaultdict(int)
    errors = []
    catalog_path = source / "资料源/目录.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else None
    policy_path = DATA / "source-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else {}
    excluded_prefixes = policy.get("exclude_prefixes", [])
    archive_prefixes = policy.get("archive_prefixes", [])
    generated_pages = set()
    if catalog:
        generated_pages.add(catalog["output"])
        for topic_id in catalog["topics"]:
            topic = json.loads((source / "资料源/专题" / (topic_id + ".json")).read_text(encoding="utf-8"))
            generated_pages.add(topic["title"] + ".html")

    def document(bucket, key, name, body):
        cleaned = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", body).strip()
        if not cleaned:
            return
        digest = hashlib.sha256(cleaned.encode()).hexdigest()
        ident = hashlib.sha256((bucket + "/" + key).encode()).hexdigest()[:20]
        path = DATA / "prepared" / bucket / (ident + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cleaned, encoding="utf-8")
        documents.append({"key": bucket + "/" + key, "bucket": bucket, "name": name[:200],
                          "sha256": digest, "path": str(path), "characters": len(cleaned)})

    for index, path in enumerate(files):
        relative = path.relative_to(source).as_posix()
        bucket = "archive" if "_历史备份" in path.relative_to(source).parts else "main"
        if any(relative == prefix.rstrip("/") or relative.startswith(prefix.rstrip("/") + "/") for prefix in archive_prefixes):
            bucket = "archive"
        raw = path.read_bytes()
        record = {"path": relative, "source": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "bucket": bucket}
        manifest.append(record)
        if any(relative == prefix.rstrip("/") or relative.startswith(prefix.rstrip("/") + "/") for prefix in excluded_prefixes):
            record["kind"] = "维护文件，不参与检索"
            stats[record["kind"]] += 1
            continue
        if catalog and (relative in generated_pages or relative.startswith("资料源/") and not relative.startswith("资料源/专题/")):
            record["kind"] = "生成成品或维护源，使用专题唯一正文"
            stats[record["kind"]] += 1
            continue
        suffix = path.suffix.lower()
        header = f"来源：{path}\n相对路径：{relative}\n"
        if bucket == "archive":
            header += "资料性质：历史备份，只供追溯，不能作为当前实现的依据。\n"
        try:
            if catalog and path.parent == source / "资料源/专题" and suffix == ".json":
                from bs4 import BeautifulSoup
                topic = json.loads(decode(raw))
                if topic["id"] not in catalog["topics"]:
                    raise ValueError("专题不在当前目录中")
                for entry in topic["entries"]:
                    if entry["id"] == "topic-notebook" or entry.get("redirect"):
                        continue
                    text = "\n\n".join(field["label"] + "\n" + BeautifulSoup(field["html"], "lxml").get_text("\n", strip=True)
                                        for field in entry["fields"])
                    document("main", "专题/" + topic["id"] + "/" + entry["id"],
                             topic["title"] + "／" + entry["term"] + ".md.txt",
                             header + f"专题：{topic['title']}\n条目：{entry['term']}\n资料版本：{topic['updated']}\n"
                             + "适用范围：以本条正文标明的仓库、分支与算法类为准，不能套用同专题其他条目的范围。\n"
                             + "核对范围：版本对照日期不代表所有原有细节均已重新验证；以条目内版本说明和源码为准。\n\n" + text)
                record["kind"] = "专题唯一正文"
            elif suffix in {".csv", ".tsv"}:
                summary = csv_summary(raw, "\t" if suffix == ".tsv" else ",")
                groups[(bucket, str(path.parent.relative_to(source)), "数据摘要")].append(header + summary)
                record["kind"] = "数据摘要"
            elif suffix in IMAGE_TYPES:
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as image:
                    dimensions = f"{image.width} × {image.height}"
                groups[(bucket, str(path.parent.relative_to(source)), "图片目录")].append(header + f"图片尺寸：{dimensions}；尚未 OCR 或视觉解读，检索仅包含文件名、路径和尺寸。")
                record["kind"] = "图片目录"
            else:
                body, kind = extract(path, raw)
                body = re.sub(r"data:image/[a-zA-Z0-9.+-]+(?:;[^,\s]*)?,[A-Za-z0-9+/=\r\n]+",
                              "[内嵌图片保留在原文件中，尚未视觉解读]", body)
                record["kind"] = kind
                if body.strip():
                    normalized = re.sub(r"\s+", " ", body).strip()
                    digest = hashlib.sha256(normalized.encode()).hexdigest()
                    if (bucket, digest) in seen:
                        record.update(kind="重复正文", canonical=seen[(bucket, digest)])
                        groups[(bucket, "", "重复来源目录")].append(header + "正文相同的来源：" + seen[(bucket, digest)])
                    else:
                        seen[(bucket, digest)] = relative
                        document(bucket, relative, relative.replace("/", "／") + ".txt", header + f"内容类型：{kind}\n\n" + body)
                else:
                    extra = ""
                    if suffix == ".zip":
                        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                            extra = "\n压缩包文件名（未执行、未展开）：" + "；".join(archive.namelist()[:80])
                    groups[(bucket, str(path.parent.relative_to(source)), "其他附件目录")].append(header + f"大小：{len(raw)} 字节；类型：{suffix}。未作为正文解析。" + extra)
            stats[record["kind"]] += 1
        except Exception as exc:
            record.update(kind="解析失败", error=str(exc)[:300])
            errors.append({"path": relative, "error": str(exc)[:300]})
            groups[(bucket, str(path.parent.relative_to(source)), "解析问题目录")].append(header + "解析问题：" + str(exc)[:300])
        if index % 500 == 0:
            print(f"已整理 {index + 1}/{len(files)} 个来源", flush=True)

    for (bucket, directory, kind), entries in groups.items():
        part = []
        length = 0
        sequence = 1
        for entry in entries:
            if part and length + len(entry) > 26000:
                name = f"{directory or '根目录'}／{kind}-{sequence}"
                document(bucket, name, name.replace("\\", "／") + ".txt", "\n\n".join(part))
                part, length, sequence = [], 0, sequence + 1
            part.append(entry)
            length += len(entry)
        if part:
            name = f"{directory or '根目录'}／{kind}-{sequence}"
            document(bucket, name, name.replace("\\", "／") + ".txt", "\n\n".join(part))

    report = {"source": str(source), "updated_at": now(), "source_files": len(files), "source_bytes": sum(item["size"] for item in manifest),
              "kinds": dict(stats), "prepared_documents": len(documents), "prepared_characters": sum(item["characters"] for item in documents),
              "errors": errors, "files": manifest, "documents": documents}
    save_json(DATA / "manifest.json", report)
    print(json.dumps({key: value for key, value in report.items() if key not in {"files", "documents"}}, ensure_ascii=False), flush=True)
    return report


def reconcile_documents(client, state, desired_keys):
    """新索引就绪后仅移除本导入器拥有的旧文档，保留手动上传内容。"""
    retired = list(state.get("retired_documents", []))
    for key, entry in state["documents"].items():
        if key not in desired_keys:
            retired.append({"id": entry["id"], "bucket": entry["bucket"]})
        if entry.get("previous_id"):
            retired.append({"id": entry["previous_id"], "bucket": entry["bucket"]})
    active_ids = {entry["id"] for key, entry in state["documents"].items() if key in desired_keys}
    removed = set()
    for entry in retired:
        if entry["id"] in removed or entry["id"] in active_ids:
            continue
        response = client.delete(API + f"/knowledge_bases/{state[entry['bucket']]}/documents/{entry['id']}")
        if response.is_error and response.status_code != 404:
            raise RuntimeError("移除旧知识文档失败：" + str(response.status_code))
        removed.add(entry["id"])
    state["documents"] = {key: {**entry, "previous_id": None} for key, entry in state["documents"].items() if key in desired_keys}
    state["retired_documents"] = []
    return removed


def retry_transient_documents(client, report, state, failures, attempts):
    """有限重试本次导入遇到的写锁或租约争用，不覆盖用户手动上传的资料。"""
    by_id = {entry["id"]: key for key, entry in state["documents"].items()}
    prepared = {entry["key"]: entry for entry in report["documents"]}
    retried = []
    for failure in failures:
        key = by_id.get(failure["id"])
        error = failure.get("error") or ""
        if key not in prepared or attempts.get(key, 0) >= 3 or not any(
                marker in error for marker in ["database is locked", "Lost lease"]):
            continue
        entry = prepared[key]
        uploaded = request(client, "POST", f"/knowledge_bases/{state[entry['bucket']]}/documents", files={
            "file": (entry["name"], Path(entry["path"]).read_bytes(), "text/plain")})
        old = state["documents"][key]
        state.setdefault("retired_documents", []).append({"id": old["id"], "bucket": old["bucket"]})
        state["documents"][key] = {**old, "id": uploaded["document_id"]}
        attempts[key] = attempts.get(key, 0) + 1
        retried.append(failure["id"])
    return retried


def request(client, method, path, **kwargs):
    response = client.request(method, API + path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"本机知识库接口失败：{method} {path}，{response.status_code}：{response.text[:400]}")
    return response.json() if response.content else None


def run(source, use_prepared=False):
    DATA.mkdir(parents=True, exist_ok=True)
    report = json.loads((DATA / "manifest.json").read_text(encoding="utf-8")) if use_prepared else prepare(source)
    if use_prepared:
        if Path(report["source"]) != source:
            raise ValueError("预处理清单的来源目录与本次请求不一致")
        for item in report["files"]:
            if hashlib.sha256((source / item["path"]).read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("原资料已变化，请重新整理：" + item["path"])
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"documents": {}}
    with httpx.Client(headers={"X-User-ID": "local"}, timeout=120) as client:
        credentials = request(client, "GET", "/credential/")["credentials"]
        credential = next((item for item in credentials if item["data"].get("type") == "local_embedding_credential"), None)
        credential_id = credential["id"] if credential else request(client, "POST", "/credential/", json={
            "data": {"type": "local_embedding_credential", "name": "本地中文知识检索（无需密钥）"}})["credential_id"]
        existing = request(client, "GET", "/knowledge_bases/", params={"page_size": 128})["knowledge_bases"]
        for bucket, name in [("main", "AOI 项目资料"), ("archive", "AOI 历史备份")]:
            knowledge = next((item for item in existing if item["name"] == name), None)
            state[bucket] = knowledge["id"] if knowledge else request(client, "POST", "/knowledge_bases/", json={
                "name": name, "description": f"来源：{source}；本地中文检索。" + ("历史资料，仅供追溯。" if bucket == "archive" else "当前文档、源码参考、测试数据摘要与附件目录。"),
                "embedding_model_config": {"type": "local_embedding_credential", "credential_id": credential_id,
                                           "model": "bge-small-zh-v1.5-local", "dimensions": 512},
                "chunker_config": {"type": "approx_token", "parameters": {"chunk_size": 180, "overlap": 24}},
            })["knowledge_base_id"]
        save_json(STATE, state)
        for index, entry in enumerate(report["documents"]):
            old = state["documents"].get(entry["key"])
            if old and old["sha256"] == entry["sha256"]:
                continue
            uploaded = request(client, "POST", f"/knowledge_bases/{state[entry['bucket']]}/documents", files={
                "file": (entry["name"], Path(entry["path"]).read_bytes(), "text/plain")})
            if old:
                retired = state.setdefault("retired_documents", [])
                retired.append({"id": old["id"], "bucket": old["bucket"]})
                if old.get("previous_id"):
                    retired.append({"id": old["previous_id"], "bucket": old["bucket"]})
            state["documents"][entry["key"]] = {"sha256": entry["sha256"], "id": uploaded["document_id"],
                                                    "bucket": entry["bucket"], "previous_id": old["id"] if old else None}
            save_json(STATE, state)
            if index % 20 == 0:
                print(f"已提交 {index + 1}/{len(report['documents'])} 份知识文档", flush=True)

        retry_attempts = {}
        while True:
            counts = {}
            for bucket in ["main", "archive"]:
                data = request(client, "GET", "/knowledge_bases/", params={"id": state[bucket]})["knowledge_bases"][0]
                counts[bucket] = {key: value for key, value in data.items() if "count" in key or "status" in key}
            print("索引进度：" + json.dumps(counts, ensure_ascii=False), flush=True)
            records = []
            for bucket in ["main", "archive"]:
                page = 1
                while True:
                    result = request(client, "GET", f"/knowledge_bases/{state[bucket]}/documents", params={"page": page, "page_size": 128})
                    records.extend({**item, "bucket": bucket} for item in result["documents"])
                    if page * 128 >= result["total"]:
                        break
                    page += 1
            desired_ids = {state["documents"][entry["key"]]["id"] for entry in report["documents"]}
            desired_records = [record for record in records if record["id"] in desired_ids]
            status_counts = defaultdict(int)
            for record in desired_records:
                status_counts[record["status"]] += 1
            if not any(status_counts[status] for status in ["pending", "parsing", "chunking", "indexing"]):
                if len(desired_records) != len(desired_ids):
                    raise RuntimeError("本次导入存在缺失文档，保留旧索引，请重新同步")
                failures = [item for item in desired_records if item["status"] == "error"]
                if failures and retry_transient_documents(client, report, state, failures, retry_attempts):
                    save_json(STATE, state)
                    print("已重新排队临时失败的资料；旧文档保留至新索引就绪", flush=True)
                    time.sleep(2)
                    continue
                if failures:
                    save_json(DATA / "index-errors.json", failures)
                    raise RuntimeError(f"有 {len(failures)} 份资料未建立索引，详见 local_data/library/index-errors.json")
                break
            time.sleep(8)

        removed_ids = reconcile_documents(client, state, {entry["key"] for entry in report["documents"]})
        save_json(STATE, state)
        records = [item for item in records if item["id"] not in removed_ids]
        config = {"source": str(source), "knowledge_base_ids": [state["main"]], "archive_knowledge_base_id": state["archive"],
                  "name": "AOI 项目资料", "updated_at": now(),
                  "source_files": sum(item["bucket"] == "main" for item in report["files"]), "all_source_files": report["source_files"],
                  "archive_source_files": sum(item["bucket"] == "archive" for item in report["files"]),
                  "prepared_documents": report["prepared_documents"],
                  "ready_documents": sum(item["bucket"] == "main" and item["status"] == "ready" for item in records),
                  "archive_ready_documents": sum(item["bucket"] == "archive" and item["status"] == "ready" for item in records),
                  "source_bytes": report["source_bytes"], "enabled": True}
        save_json(CONFIG, config)
        save_json(DATA / "index-errors.json", [])
        print("资料接入完成：" + json.dumps(config, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="更新本地 AOI 资料知识库，不修改来源文件")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--prepared", action="store_true", help="校验后使用已生成的正文清单")
    args = parser.parse_args()
    source = args.source or (Path(json.loads(CONFIG.read_text(encoding="utf-8"))["source"]) if CONFIG.exists()
                             else None)
    if source is None:
        parser.error("首次导入请使用 --source 指定本地资料目录")
    run(source.resolve(strict=True), use_prepared=args.prepared)
