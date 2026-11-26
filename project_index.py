"""为多个 AOI 工作目录建立本地代码、符号和版本索引，不修改源工程。"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

from dispatch_engine import now, save_json
from library_import import decode

APP = Path(__file__).resolve().parent
DATA = APP / "local_data/projects"
CATALOG = DATA / "catalog.json"
DATABASE = DATA / "code.sqlite"
PROJECT_ROOT = Path(os.environ.get("AGENTSCOPE_PROJECT_ROOT", str(APP / "projects")))
SOURCE_TYPES = {".py", ".pyi", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx", ".cu", ".cuh", ".proto", ".ui", ".qrc", ".vcxproj", ".props", ".sln", ".cmake", ".js", ".ts", ".tsx", ".jsx", ".qml", ".html", ".css", ".qss"}
SOURCE_NAMES = {"CMakeLists.txt", "requirements.txt", "pyproject.toml", "AGENTS.md"}
EXCLUDED = {".git", ".vs", ".idea", ".venv", "venv", "env", "site-packages", "__pycache__", "node_modules", "backups", "deliverables", "build", "dist", "final-dist", "red-pen-build", "packaged", "bin", "obj", "debug", "release", "rdebug", "x64", "win32", "cmakefiles", "third_party", "thirdparty", "3rdparty", "vendor", "vendors", ".codex-backups", ".workbuddy", ".workbuddy-ai"}


def command(args, cwd=None, timeout=20):
    """所有子命令按参数数组运行，不经过 shell，也不打开独立窗口。"""
    result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return result.returncode, decode(result.stdout), decode(result.stderr)


def git_command(directory, args, timeout=20):
    return command([shutil.which("git") or "git", "--no-optional-locks", "-c", "core.quotePath=false", "-C", str(directory), *args], timeout=timeout)


def git_state(directory):
    if not (directory / ".git").exists():
        return {"git": False, "branch": None, "head": None, "changes": []}
    _, branch, _ = git_command(directory, ["branch", "--show-current"])
    _, head, _ = git_command(directory, ["rev-parse", "HEAD"])
    _, status, _ = git_command(directory, ["status", "--porcelain=v1", "-uno"])
    return {"git": True, "branch": branch.strip(), "head": head.strip(), "changes": status.splitlines()}


def source_file(path):
    return path.suffix.lower() in SOURCE_TYPES or path.name in SOURCE_NAMES


def walk_sources(directory):
    for parent, names, files in os.walk(directory, followlinks=False):
        names[:] = [name for name in names if name.casefold() not in EXCLUDED and not name.startswith(".") and "backup" not in name.casefold()
                    and not (Path(parent) / name / "pyvenv.cfg").exists() and not (Path(parent) / name).is_symlink()]
        for name in files:
            path = Path(parent) / name
            if source_file(path) and not path.is_symlink() and path.stat().st_size <= 4_000_000:
                yield path


def symbols_for(text, suffix):
    if suffix not in {".py", ".pyi", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx", ".cu", ".cuh"}:
        return []
    import tree_sitter
    import tree_sitter_cpp
    import tree_sitter_python
    raw = text.encode("utf-8")
    language = tree_sitter_python.language() if suffix in {".py", ".pyi"} else tree_sitter_cpp.language()
    parser = tree_sitter.Parser(tree_sitter.Language(language))
    tree = parser.parse(raw)
    pending = [tree.root_node]
    result = []
    kinds = {"function_definition", "class_definition", "class_specifier", "struct_specifier", "enum_specifier"}
    while pending:
        node = pending.pop()
        if node.type in kinds:
            name = node.child_by_field_name("name")
            if name is None:
                declarator = node.child_by_field_name("declarator")
                while declarator is not None:
                    nested = declarator.child_by_field_name("declarator")
                    if nested is None:
                        name = declarator
                        break
                    declarator = nested
            if name is not None:
                value = raw[name.start_byte:name.end_byte].decode("utf-8")
                if len(value) <= 220 and "\n" not in value:
                    result.append((value, node.type, node.start_point.row + 1, node.end_point.row + 1))
        pending.extend(reversed(node.named_children))
    return result


def refresh(root=PROJECT_ROOT):
    if root == APP / "projects":
        root.mkdir(exist_ok=True)
    root = root.resolve(strict=True)
    DATA.mkdir(parents=True, exist_ok=True)
    temporary = DATA / ("code-" + str(os.getpid()) + ".sqlite")
    repositories = []
    seen = {}
    total_symbols = 0
    started = time.monotonic()
    with closing(sqlite3.connect(temporary)) as connection:
        connection.executescript("""
            PRAGMA journal_mode=OFF;
            CREATE TABLE blobs(id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE, content TEXT);
            CREATE TABLE files(repo TEXT, path TEXT, blob_id INTEGER, mtime_ns INTEGER, size INTEGER, PRIMARY KEY(repo,path));
            CREATE INDEX files_blob ON files(blob_id);
            CREATE TABLE symbols(blob_id INTEGER, name TEXT, kind TEXT, start_line INTEGER, end_line INTEGER);
            CREATE INDEX symbols_name ON symbols(name COLLATE NOCASE);
            CREATE INDEX symbols_blob ON symbols(blob_id);
            CREATE VIRTUAL TABLE text_search USING fts5(content, content='blobs', content_rowid='id', tokenize='trigram');
        """)
        directories = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        if any(item.is_file() and source_file(item) for item in directories):
            directories.append(root)
        for directory in directories:
            if not directory.is_dir() or directory.name.startswith((".", "_")) or directory.name.casefold() in EXCLUDED or directory.is_symlink():
                continue
            before = git_state(directory)
            root_only = directory == root
            entry = {"id": "项目根目录工具" if root_only else directory.name, "path": str(directory), "scope": "root_files" if root_only else "directory",
                     **before, "source_files": 0, "source_bytes": 0, "solutions": []}
            paths = (item for item in root.iterdir() if item.is_file() and source_file(item)) if root_only else walk_sources(directory)
            for path in paths:
                raw = path.read_bytes()
                if b"\x00" in raw[:2000] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if digest not in seen:
                    text = decode(raw).replace("\r\n", "\n").replace("\r", "\n")
                    blob_id = connection.execute("INSERT INTO blobs(sha256,content) VALUES(?,?)", (digest, text)).lastrowid
                    connection.execute("INSERT INTO text_search(rowid,content) VALUES(?,?)", (blob_id, text))
                    symbols = symbols_for(text, path.suffix.lower())
                    connection.executemany("INSERT INTO symbols VALUES(?,?,?,?,?)", [(blob_id, *symbol) for symbol in symbols])
                    total_symbols += len(symbols)
                    seen[digest] = blob_id
                relative = path.relative_to(directory).as_posix()
                stat = path.stat()
                connection.execute("INSERT INTO files VALUES(?,?,?,?,?)", (entry["id"], relative, seen[digest], stat.st_mtime_ns, len(raw)))
                entry["source_files"] += 1
                entry["source_bytes"] += len(raw)
                if path.suffix.lower() in {".sln", ".vcxproj"} or path.name == "CMakeLists.txt":
                    entry["solutions"].append(relative)
            after = git_state(directory)
            entry["source_state_preserved"] = before == after
            if not entry["source_state_preserved"]:
                entry["notice"] = "扫描期间 Git 状态发生变化，请核对是否有其他程序正在编辑；扫描器没有执行写入。"
            repositories.append(entry)
            connection.commit()
            print(f"已接入 {entry['id']}：{entry['source_files']} 个代码与工程文件", flush=True)
        connection.execute("INSERT INTO text_search(text_search) VALUES('optimize')")
        connection.commit()
    temporary.replace(DATABASE)
    default = repositories[0]["id"] if repositories else None
    catalog = {"root": str(root), "default_repository": default, "updated_at": now(),
               "repositories": repositories, "source_files": sum(item["source_files"] for item in repositories),
               "unique_contents": len(seen), "symbols": total_symbols, "seconds": round(time.monotonic() - started, 2)}
    save_json(CATALOG, catalog)
    print(json.dumps({key: value for key, value in catalog.items() if key != "repositories"}, ensure_ascii=False), flush=True)
    return catalog


class ProjectIndex:
    def status(self):
        if not CATALOG.exists():
            return {"enabled": False, "repositories": [], "source_files": 0}
        return {"enabled": True, **json.loads(CATALOG.read_text(encoding="utf-8"))}

    def repository(self, name=None):
        catalog = self.status()
        name = name or catalog.get("default_repository")
        entry = next((item for item in catalog["repositories"] if item["id"] == name), None)
        if entry is None:
            raise ValueError("未接入的项目，请从项目列表中选择")
        return entry

    def resolve_file(self, repository, path):
        entry = self.repository(repository)
        base = Path(entry["path"]).resolve(strict=True)
        candidate = (base / path).resolve(strict=True)
        if entry.get("scope") == "root_files" and candidate.parent != base:
            raise ValueError("根目录工具只读取根目录文件，请为子目录选择对应工程")
        if not candidate.is_relative_to(base) or not candidate.is_file() or not source_file(candidate):
            raise ValueError("只能读取所选项目内的代码与工程文件")
        if any(part.casefold() in EXCLUDED or part.startswith(".") for part in candidate.relative_to(base).parts):
            raise ValueError("不读取版本库内部数据、缓存或环境目录")
        if candidate.stat().st_size > 4_000_000:
            raise ValueError("文件超过单次读取上限")
        return candidate

    def read(self, repository, path, start=1, end=100):
        if start < 1 or end < start or end - start > 399:
            raise ValueError("一次最多读取 400 行，行号从 1 开始")
        file = self.resolve_file(repository, path)
        lines = decode(file.read_bytes()).splitlines()
        text = "\n".join(f"{index + 1}: {line}" for index, line in enumerate(lines[start - 1:end], start - 1))
        return {"repository": repository, "path": path, "total_lines": len(lines), "start_line": start,
                "truncated": len(text) > 60000, "text": text[:60000]}

    def find_symbols(self, repository, name, limit=30):
        self.repository(repository)
        with closing(sqlite3.connect(DATABASE)) as connection:
            rows = connection.execute("""SELECT f.path,s.name,s.kind,s.start_line,s.end_line FROM symbols s
                JOIN files f ON f.blob_id=s.blob_id WHERE f.repo=? AND instr(lower(s.name),lower(?))>0
                ORDER BY CASE WHEN f.path LIKE '%/tests/%' OR f.path LIKE '%/test/%' THEN 1 ELSE 0 END,
                CASE WHEN lower(s.name)=lower(?) THEN 0 ELSE 1 END,length(s.name) LIMIT ?""",
                                      (repository, name[:150], name[:150], min(max(limit, 1), 50))).fetchall()
        return [{"path": path, "name": symbol, "kind": kind, "line": line, "end_line": end} for path, symbol, kind, line, end in rows]

    def search(self, repository, query, limit=12):
        self.repository(repository)
        tokens = list(dict.fromkeys(re.findall(r"[a-zA-Z_][a-zA-Z_0-9:]{2,60}|[\u4e00-\u9fff]{2,30}", query)))[:12]
        aliases = {"保险丝": ["fuse"], "算法5": ["fuse"], "测宽": ["width", "normal"], "法线": ["normal"],
                   "并行": ["parallel_for", "_ZS_PARALELL"], "缺陷": ["addDefectInfo"], "显微镜": ["microscope", "camera"],
                   "曝光": ["exposure"], "MES": ["mesconfig", "UploadToolWidget"]}
        for word, values in aliases.items():
            if word.casefold() in query.casefold():
                tokens.extend(values)
        tokens = list(dict.fromkeys(tokens))[:16]
        terms = [value for value in tokens if len(value) >= 3]
        if not terms:
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in terms)
        wants_tests = bool(re.search(r"测试|回归|断言|mock|\btest\b", query, re.I))
        with closing(sqlite3.connect(DATABASE)) as connection:
            rows = connection.execute("""SELECT f.path,b.content,f.mtime_ns,bm25(text_search) FROM text_search
                JOIN blobs b ON b.id=text_search.rowid JOIN files f ON f.blob_id=b.id
                WHERE text_search MATCH ? AND f.repo=?
                ORDER BY CASE WHEN f.path LIKE '%/tests/%' OR f.path LIKE '%/test/%' THEN ? ELSE ? END,
                bm25(text_search) LIMIT ?""",
                                      (expression, repository, 0 if wants_tests else 1, 1 if wants_tests else 0, min(max(limit, 1), 40))).fetchall()
        result = []
        for path, text, recorded_mtime, rank in rows:
            file = self.resolve_file(repository, path)
            changed = file.stat().st_mtime_ns != recorded_mtime
            if changed:
                text = decode(file.read_bytes())
            lines = text.splitlines()
            def relevance(line):
                score = sum((1 if token.casefold() in path.casefold() else 3)
                            for token in tokens if token.casefold() in line.casefold())
                if line.lstrip().startswith(("#include", "#pragma")):
                    score = 0
                return score
            matching = [(index, relevance(line)) for index, line in enumerate(lines)]
            matches = sorted((entry for entry in matching if entry[1]), key=lambda entry: (-entry[1], entry[0]))
            if not matches:
                continue
            index = matches[0][0]
            start, end = max(0, index - 6), min(len(lines), index + 16)
            result.append({"repository": repository, "path": path, "line": index + 1, "start_line": start + 1,
                           "index_stale": changed, "text": "\n".join(f"{number + 1}: {line}" for number, line in enumerate(lines[start:end], start))})
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="更新 AOI 本地代码索引，不修改源工程")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    refresh(args.root)
