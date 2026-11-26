"""为多模型对话提供所选项目的文件操作和本机开发命令。"""

import asyncio
import base64
import codecs
from fnmatch import fnmatch
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from library_import import decode
from project_index import EXCLUDED, git_command, source_file
from dispatch_changes import ProjectChanges, replace_file


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Search(Arguments):
    query: str = Field(min_length=1, max_length=500)
    directory: str = "."
    glob: str = Field(default="*", max_length=120, description="文件名模式，例如 *.cpp")


class Files(Arguments):
    directory: str = "."
    glob: str = Field(default="*", max_length=120)


class Rules(Arguments):
    directory: str = "."


class Git(Arguments):
    action: Literal["status", "diff", "staged", "history"]
    path: str = ""


class SkillRequest(Arguments):
    name: str = Field(default="", max_length=100)
    reference: str = Field(default="", max_length=160, description="技能说明里列出的 references/ 下文本文件")


class Step(Arguments):
    title: str = Field(min_length=1, max_length=140)
    status: Literal["pending", "working", "done"]


class WorkPlan(Arguments):
    steps: list[Step] = Field(min_length=1, max_length=12)


class ToolInput(Arguments):
    action: str
    path: str = ""
    other_path: str = ""
    repository: str = ""
    source: Literal["资料", "代码"] = "代码"
    paths: list[str] = Field(default_factory=list, max_length=8)


class MCPRequest(Arguments):
    plugin: str = Field(default="", max_length=100)
    tool: str = Field(default="", max_length=150)
    arguments: dict = Field(default_factory=dict)


class Read(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    lines: int = Field(default=160, ge=1, le=400)


class Edit(Arguments):
    path: str
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    old_text: str = Field(min_length=1)
    new_text: str


class Create(Arguments):
    path: str
    content: str


class Shell(Arguments):
    command: str = Field(description="Get-Location、Get-ChildItem、Get-Item、Get-Content、Select-String、Get-FileHash、Test-Path 或 Run-Script")
    path: str = "."
    pattern: str = Field(default="", max_length=300, description="Select-String 查询的字面文本")


class Terminal(Arguments):
    command: str = Field(min_length=1, max_length=6000)
    directory: str = "."
    description: str = Field(min_length=1, max_length=200)
    timeout: int = Field(default=180, ge=1, le=600)


class Build(Arguments):
    path: str = Field(description="项目内的 .sln、.slnx、.vcxproj、.csproj、CMake 构建目录或 .py 文件")
    configuration: str = Field(default="", pattern=r"^[\w .-]{0,60}$")
    platform: str = Field(default="", pattern=r"^[\w .-]{0,40}$")


class Test(Arguments):
    runner: str = Field(description="unittest、pytest、ctest、dotnet 或 vstest")
    path: str = Field(default=".", description="项目内的测试文件、项目或测试目录")
    pattern: str = Field(default="test*.py", pattern=r"^[\w*?.-]{1,100}$", description="unittest 查找测试文件的文件名模式")


TOOLS = {
    "project_search": (Search, "在当前工作树中查找文字，包括本轮刚修改或新建的文件。可选择目录和文件名模式。"),
    "project_files": (Files, "按文件名模式在项目里查找文件，例如 *.sln、test_*.py、*.cpp。"),
    "project_rules": (Rules, "读取指定目录适用的 AGENTS.md，按从仓库到子目录的顺序返回。"),
    "project_git": (Git, "查看当前仓库的 Git 状态、未暂存差异、已暂存差异或近期版本历史。"),
    "project_plan": (WorkPlan, "发布或更新当前子任务的进度。多步骤修改可列出调查、修改和验证。"),
    "project_skill": (SkillRequest, "查看已安装的 AOI 与 ClawHub 技能、读取技能说明或按需读取其参考资料。"),
    "project_knowledge": (Search, "搜索已接入的知识库，查看相关资料和来源。仅在本轮开启资料检索时可用。"),
    "project_diagnostics": (ToolInput, "调用 AOI 工具。action 可用 symbols（path 填函数名）、static_check（paths 填文件）、build_profile、compare_source（repository 填另一仓库、other_path 填另一文件）、image_stats、compare_images 或 environment。"),
    "project_mcp": (MCPRequest, "查询本轮启用的 MCP、查询服务器的只读工具，或按工具参数调用。先查询插件和工具后再调用。MCP 可能是远程服务，请勿传递与任务无关的项目资料。"),
    "project_read": (Read, "读取项目文件的指定行，并取得修改时必需的文件版本。"),
    "project_edit": (Edit, "根据读取结果替换文件中唯一匹配的原文，保留其余内容和原编码。"),
    "project_create": (Create, "在现有目录下创建新源码或测试文件，目标文件必须不存在。"),
    "project_powershell": (Shell, "运行 PowerShell 查询或所选项目里的现有 .ps1 文件。Run-Script 仅在用户要求运行该脚本时使用。返回退出码和输出。"),
    "project_terminal": (Terminal, "当内置工具不足时，提交一条本地开发 PowerShell 命令供用户确认。命令在用户确认后运行。禁止用它代替修改文件工具或做 Git 提交、推送及其他发布。"),
    "project_build": (Build, "使用本机 MSBuild、CMake 或 Python 编译指定项目目标，返回退出码与输出。"),
    "project_test": (Test, "实际运行项目测试，返回退出码与输出。请在相关修改与构建完成后验证。"),
}


def project_tool_schemas(knowledge=True, mcp=False, indexed=True):
    return [
        {"type": "function", "function": {"name": name, "description": description,
                                       "parameters": arguments.model_json_schema()}}
        for name, (arguments, description) in TOOLS.items()
        if (knowledge or name != "project_knowledge") and (mcp or name != "project_mcp")
        and (indexed or name != "project_diagnostics")
    ]


def launch_windows(argv, cwd, output):
    """创建受 Windows 作业对象管理的进程，停止时连同子进程一起结束。"""
    import msvcrt
    import win32event
    import win32job
    import win32process

    job = win32job.CreateJobObject(None, "")
    process = None
    try:
        limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
        with open(os.devnull, "rb") as stdin:
            handles = [msvcrt.get_osfhandle(stdin.fileno()), msvcrt.get_osfhandle(output.fileno())]
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            try:
                startup = win32process.STARTUPINFO()
                startup.dwFlags = subprocess.STARTF_USESTDHANDLES
                startup.hStdInput, startup.hStdOutput = handles
                startup.hStdError = handles[1]
                process, thread, _, _ = win32process.CreateProcess(
                    argv[0], subprocess.list2cmdline(argv), None, None, True,
                    subprocess.CREATE_NO_WINDOW | win32process.CREATE_SUSPENDED, None, str(cwd), startup,
                )
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
        try:
            win32job.AssignProcessToJobObject(job, process)
            win32process.ResumeThread(thread)
        finally:
            thread.Close()
    except BaseException:
        if process is not None:
            win32process.TerminateProcess(process, 1)
            process.Close()
        job.Close()
        raise
    return process, job, win32event, win32process


async def run_command(argv, cwd, timeout=600):
    with tempfile.TemporaryFile() as output:
        process, job, event, process_api = launch_windows(argv, cwd, output)
        try:
            try:
                async with asyncio.timeout(timeout):
                    while event.WaitForSingleObject(process, 0) == event.WAIT_TIMEOUT:
                        await asyncio.sleep(.15)
                code = process_api.GetExitCodeProcess(process)
            except TimeoutError:
                code = None
        finally:
            job.Close()
            process.Close()
        output.seek(0, os.SEEK_END)
        size = output.tell()
        output.seek(max(0, size - 24000))
        log = decode(output.read()).strip()
        if size > 24000:
            log = "［仅显示日志末尾］\n" + log
        if code is None:
            log = "命令超时，已停止进程。\n" + log
        return {"ok": code == 0, "exit_code": code, "output": log,
                "command": subprocess.list2cmdline(argv)}


def executable(name):
    found = shutil.which(name)
    if not found:
        raise ValueError(f"本机未找到 {name}，请检查工具是否已安装并加入 PATH。")
    return str(Path(found).resolve())


def visual_studio_tool(name):
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    installer = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = installer / "Microsoft Visual Studio/Installer/vswhere.exe"
    if vswhere.is_file():
        pattern = ("MSBuild/**/Bin/MSBuild.exe" if name == "MSBuild.exe"
                   else "Common7/IDE/**/vstest.console.exe")
        completed = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-utf8", "-find", pattern],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in completed.stdout.decode("utf-8-sig", errors="replace").splitlines():
            if Path(line).is_file():
                return line
    return executable(name)


def file_text(raw):
    for bom, encoding in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                          (codecs.BOM_UTF16_BE, "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(encoding), encoding, bom
    for encoding in ("utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\0" not in text:
            return text, encoding, b""
    raise ValueError("文件不是支持的文本编码。")


class ProjectExecution:
    def __init__(self, index, repository, *, local_directory=None):
        self.index = index
        self.local_directory = local_directory is not None
        if self.local_directory:
            directory = Path(local_directory)
            if not directory.is_absolute() or directory.drive.startswith(("\\\\?\\", "\\\\.\\")):
                raise ValueError("请输入本地文件夹的完整路径，例如 D:\\Work\\MyApp。")
            self.repository = directory.name or directory.anchor
        else:
            entry = index.repository(repository)
            if entry.get("scope") == "root_files":
                raise ValueError("请选择具体子工程，根目录入口仅用于代码检索。")
            directory = Path(entry["path"])
            self.repository = entry["id"]
        try:
            self.root = directory.resolve(strict=True)
            if not self.root.is_dir():
                raise ValueError("请选择文件夹。")
        except (OSError, RuntimeError):
            raise ValueError("文件夹不存在或当前账户无法访问。") from None
        self.changes = None
        self.known_directories = {"."}
        self.mcp = None

    def track_changes(self, directory):
        self.changes = ProjectChanges(directory, self)

    def instructions(self, directory):
        directory = self.path(directory)
        if not directory.is_dir():
            raise ValueError("请选择项目目录。")
        locations = [self.root]
        current = self.root
        for part in directory.relative_to(self.root).parts:
            current /= part
            locations.append(current)
        rules = []
        for folder in locations:
            path = folder / "AGENTS.md"
            if not path.exists():
                continue
            self.path(path.relative_to(self.root).as_posix(), source=True)
            if path.stat().st_size > 96_000:
                raise ValueError("AGENTS.md 超过可读取的大小，请检查项目规则文件。")
            rules.append({"path": path.relative_to(self.root).as_posix(), "content": file_text(path.read_bytes())[0]})
        self.known_directories.add(directory.relative_to(self.root).as_posix())
        return rules

    def path(self, value, *, source=False, build=False, exists=True):
        relative = PureWindowsPath(value)
        if self.local_directory and relative.is_absolute():
            try:
                relative = relative.relative_to(PureWindowsPath(self.root))
            except ValueError:
                raise ValueError("文件路径超出了本轮选中的文件夹。") from None
        if relative.anchor or ".." in relative.parts or any(
            any(char in part for char in ':*?<>|"') or any(ord(char) < 32 for char in part)
            or PureWindowsPath(part).is_reserved() or (not self.local_directory and part.startswith("."))
            or part.endswith((" ", "."))
            for part in relative.parts
        ):
            raise ValueError("请使用所选项目内的相对路径。")
        candidate = self.root
        for part in relative.parts:
            name = part.casefold()
            if name == ".git" or (not self.local_directory and name in EXCLUDED and not (
                build and name in {"build", "bin", "obj", "out", "x64", "win32", "debug", "rdebug", "release", "dist"}
            )):
                raise ValueError("该目录不在项目执行范围内。")
            candidate /= part
            if candidate.exists() or candidate.is_symlink():
                if getattr(candidate.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or candidate.is_symlink():
                    raise ValueError("项目工具不能通过目录联接或符号链接访问文件。")
        if not candidate.resolve().is_relative_to(self.root):
            raise ValueError("路径超出了所选项目。")
        if exists and not candidate.exists():
            raise ValueError("文件或目录不存在。")
        if source and ((exists and not candidate.is_file()) or
                       (not self.local_directory and not source_file(candidate))):
            raise ValueError("请选择可读取的普通文本文件。")
        return candidate

    def read(self, request):
        target = self.path(request.path, source=True)
        if target.stat().st_size > 4_000_000:
            raise ValueError("文件超过 4 MB，请先定位所需代码。")
        raw = target.read_bytes()
        text, _, _ = file_text(raw)
        lines = text.splitlines(keepends=True)
        start = request.start_line - 1
        chunk = "".join(lines[start:start + request.lines])
        if len(chunk) > 32000:
            raise ValueError("所选范围内容过多，请缩小读取行数。")
        return {"ok": True, "path": request.path, "version": hashlib.sha256(raw).hexdigest(),
                "instructions": self.instructions(target.parent.relative_to(self.root).as_posix()),
                "start_line": request.start_line, "total_lines": len(lines), "content": chunk}

    def write(self, name, request):
        editing = name == "project_edit"
        target = self.path(request.path, source=True, exists=editing)
        if target.name.casefold() == "agents.md":
            raise ValueError("项目执行工具不能修改 AGENTS.md。")
        if not target.parent.is_dir():
            raise ValueError("请指定已有的项目子目录。")
        if not editing:
            if target.exists():
                raise ValueError("目标文件已存在，请先读取再修改。")
            directory = target.parent.relative_to(self.root).as_posix()
            if directory not in self.known_directories:
                rules = self.instructions(directory)
                if any(row["path"] != "AGENTS.md" for row in rules):
                    return {"ok": False, "error": "请先核对新文件所在目录的项目规则，再重试创建。",
                            "instructions": rules}
            return self.save_edit(target, None, request.content.encode("utf-8"))
        if target.stat().st_size > 4_000_000:
            raise ValueError("文件超过 4 MB。")
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != request.version:
            raise ValueError("文件在读取后发生变化，请重新读取后再修改。")
        text, encoding, bom = file_text(raw)
        newline = "\r\n" if "\r\n" in text else "\n"
        original = request.old_text.replace("\r\n", "\n").replace("\n", newline)
        replacement = request.new_text.replace("\r\n", "\n").replace("\n", newline)
        if text.count(original) != 1:
            raise ValueError("要替换的原文必须在文件中恰好出现一次，请重新读取并补充上下文。")
        data = bom + text.replace(original, replacement, 1).encode(encoding)
        if data == raw:
            return {"ok": True, "path": request.path, "changed": False}
        return self.save_edit(target, raw, data)

    def save_edit(self, path, before, after):
        if len(after) > 4_000_000:
            raise ValueError("单个改动文件不能超过 4 MB。")
        if self.changes is None:
            replace_file(path, before, after)
            relative = path.relative_to(self.root).as_posix()
        else:
            relative = self.changes.write(path, before, after)
        return {"ok": True, "path": relative, "changed": True}

    def working_tree_search(self, request, query=None):
        directory = self.path(request.directory)
        if not directory.is_dir():
            raise ValueError("请选择项目目录。")
        results = []
        deadline = time.monotonic() + 8
        max_rows = 32 if query is not None else 180
        folded = query.casefold() if query is not None else ""
        skipped = False
        for folder, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [name for name in sorted(dirs) if not name.startswith(".") and name.casefold() not in EXCLUDED
                       and not self.is_link_or_junction(Path(folder) / name)]
            for name in sorted(files):
                if time.monotonic() > deadline or len(results) >= max_rows:
                    return {"ok": True, "truncated": True, "matches" if query is not None else "files": results}
                path = Path(folder) / name
                if name.startswith(".") or not fnmatch(name, request.glob) or self.is_link_or_junction(path):
                    continue
                relative = path.relative_to(self.root).as_posix()
                if query is None:
                    results.append(relative)
                    continue
                try:
                    if not source_file(path) or path.stat().st_size > 4_000_000:
                        continue
                    content = file_text(path.read_bytes())[0]
                except (OSError, ValueError):
                    skipped = True
                    continue
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if folded in line.casefold():
                        results.append({"path": relative, "line": line_no, "text": line[:1000]})
                        if len(results) >= max_rows:
                            return {"ok": True, "truncated": True, "matches": results}
        return {"ok": True, "truncated": skipped, "matches" if query is not None else "files": results}

    @staticmethod
    def is_link_or_junction(path):
        try:
            return bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT) or path.is_symlink()
        except OSError:
            return True

    def git(self, request):
        if not (self.root / ".git").exists():
            raise ValueError("所选目录不是 Git 仓库。")
        operations = {
            "status": ["status", "--short", "--branch", "--untracked-files=normal"],
            "diff": ["diff", "--no-ext-diff", "--no-textconv", "--unified=3"],
            "staged": ["diff", "--cached", "--no-ext-diff", "--no-textconv", "--unified=3"],
            "history": ["log", "-n20", "--no-show-signature", "--date=short", "--format=%h %ad %s"],
        }
        args = operations[request.action]
        if request.path:
            path = self.path(request.path, exists=False).relative_to(self.root).as_posix()
            args += ["--", path]
        code, output, error = git_command(self.root, ["--literal-pathspecs", *args])
        if code:
            raise ValueError(error[:500] or "Git 命令未成功。")
        return {"ok": True, "output": output[:32000], "truncated": len(output) > 32000}

    def diagnostics(self, request):
        if self.local_directory:
            raise ValueError("AOI 索引诊断适用于已接入的项目。")
        import aoi_tools

        action = request.action
        if action == "symbols":
            return self.index.find_symbols(self.repository, request.path)
        if action == "environment":
            return aoi_tools.tool_environment()
        if action == "static_check":
            for path in request.paths:
                self.path(path, source=True)
            return aoi_tools.check_sources(self.repository, request.paths)
        if action == "build_profile":
            self.path(request.path, source=True)
            return aoi_tools.build_profile(self.repository, request.path)
        if action == "compare_source":
            self.path(request.path, source=True)
            return aoi_tools.compare_sources(self.repository, request.path, request.repository, request.other_path)
        if action in {"image_stats", "compare_images"}:
            paths = [request.path] if action == "image_stats" else [request.path, request.other_path]
            if request.source == "代码":
                paths = [str(self.path(path)) for path in paths]
            if action == "image_stats":
                return aoi_tools.image_statistics(paths[0], request.source)
            return aoi_tools.compare_images(*paths, source=request.source)
        raise ValueError("请选择工具说明中的诊断操作。")

    @staticmethod
    def skill(request):
        import frontmatter
        from aoi_hubs import skill_paths

        skills = {}
        for path in skill_paths():
            file = Path(path) / "SKILL.md"
            if not file.is_file() or file.stat().st_size > 100_000:
                continue
            document = frontmatter.load(file, encoding="utf-8")
            skills[Path(path).name] = (file.parent, document)
        if not request.name:
            return {"ok": True, "skills": [{"name": name, "description": doc.get("description", "")}
                                            for name, (_, doc) in skills.items()]}
        selected = skills.get(request.name)
        if selected is None:
            raise ValueError("该技能尚未安装到本机工具库。")
        root, document = selected
        if not request.reference:
            return {"ok": True, "name": request.name, "instructions": document.content}
        reference = PureWindowsPath(request.reference)
        if reference.anchor or ".." in reference.parts or not reference.parts or reference.parts[0] != "references":
            raise ValueError("只允许读取该技能 references 目录内的文本资料。")
        file = (root / reference).resolve()
        if (not file.is_relative_to(root.resolve()) or file.suffix.lower() not in {".md", ".txt"}
                or not file.is_file() or file.stat().st_size > 100_000):
            raise ValueError("参考资料不存在、过大或不是受支持的文本。")
        return {"ok": True, "name": request.name, "reference": request.reference,
                "content": file_text(file.read_bytes())[0]}

    def powershell(self, request):
        name = request.command.casefold()
        argv = [executable("powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive"]
        if name == "run-script":
            script = self.path(request.path)
            if script.suffix.casefold() != ".ps1" or not script.is_file():
                raise ValueError("请选择所选项目中现有的 .ps1 脚本。")
            return [*argv, "-ExecutionPolicy", "RemoteSigned", "-File", str(script)]
        commands = {
            "get-location": "Get-Location",
            "get-childitem": "Get-ChildItem -LiteralPath {path} | Select-Object -First 100 Name, Mode, Length",
            "get-item": "Get-Item -LiteralPath {path} | Select-Object Name, Mode, Length, LastWriteTime",
            "get-content": "Get-Content -LiteralPath {path} -TotalCount 200",
            "select-string": "Select-String -LiteralPath {path} -Pattern {pattern} -SimpleMatch | Select-Object -First 50 LineNumber, Line",
            "get-filehash": "Get-FileHash -LiteralPath {path}",
            "test-path": "Test-Path -LiteralPath {path}",
        }
        if name not in commands:
            raise ValueError("请选择支持的 PowerShell 命令，编译和测试请使用对应工具。")
        target = self.path(request.path, source=name in {"get-content", "select-string", "get-filehash"},
                           exists=name != "test-path")
        path_data = base64.b64encode(str(target).encode("utf-8")).decode("ascii")
        pattern_data = base64.b64encode(request.pattern.encode("utf-8")).decode("ascii")
        script = (
            "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; [Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            f"$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{path_data}')); "
            f"$pattern = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{pattern_data}')); "
            + commands[name].format(path="$path", pattern="$pattern")
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return [*argv, "-EncodedCommand", encoded]

    async def terminal(self, request):
        directory = self.path(request.directory, build=True)
        if not directory.is_dir():
            raise ValueError("终端工作目录必须是所选项目中的目录。")
        script = (
            "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; [Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            + request.command + "\nif (-not $?) { exit 1 }; if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }"
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = await run_command(
            [executable("powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            directory, request.timeout,
        )
        result["command"] = request.command
        return result

    def build(self, request):
        target = self.path(request.path, build=True)
        if target.is_dir():
            if not (target / "CMakeCache.txt").is_file():
                raise ValueError("请选择已配置的 CMake 构建目录。")
            argv = [executable("cmake.exe"), "--build", str(target)]
            return argv + (["--config", request.configuration] if request.configuration else [])
        if target.suffix.casefold() == ".py":
            return [sys.executable, "-I", "-m", "py_compile", str(target)]
        if target.suffix.casefold() not in {".sln", ".slnx", ".vcxproj", ".csproj"}:
            raise ValueError("请选择受支持的解决方案、工程、Python 文件或 CMake 构建目录。")
        argv = [visual_studio_tool("MSBuild.exe"), str(target), "/nologo", "/m", "/nr:false", "/t:Build"]
        if request.configuration:
            argv.append("/p:Configuration=" + request.configuration)
        if request.platform:
            argv.append("/p:Platform=" + request.platform)
        return argv

    def test(self, request):
        target = self.path(request.path, build=True)
        runner = request.runner.casefold()
        if runner == "unittest":
            directory = target if target.is_dir() else target.parent
            pattern = request.pattern if target.is_dir() else target.name
            if not target.is_dir() and target.suffix.casefold() != ".py":
                raise ValueError("unittest 需要测试目录或 .py 文件。")
            if not any(directory.rglob(pattern)):
                raise ValueError("没有找到匹配的 unittest 测试文件。")
            return [sys.executable, "-m", "unittest", "discover", "-s", str(directory), "-p", pattern, "-v"]
        if runner == "pytest" and (target.is_dir() or target.suffix.casefold() == ".py"):
            return [sys.executable, "-m", "pytest", str(target)]
        if runner == "ctest" and target.is_dir():
            return [executable("ctest.exe"), "--test-dir", str(target), "--output-on-failure", "--no-tests=error"]
        if runner == "dotnet" and target.suffix.casefold() in {".sln", ".slnx", ".csproj", ".fsproj"}:
            return [executable("dotnet.exe"), "test", str(target)]
        if runner == "vstest" and target.suffix.casefold() == ".dll":
            return [visual_studio_tool("vstest.console.exe"), str(target), "/TreatNoTestsAsError"]
        raise ValueError("测试运行器或测试目标不匹配，请选择项目实际使用的测试框架。")

    async def execute(self, name, raw):
        if name not in TOOLS:
            raise ValueError("未知项目工具。")
        try:
            request = TOOLS[name][0].model_validate_json(raw)
        except ValidationError:
            raise ValueError("工具参数不符合要求，请按参数说明重试。") from None
        if name == "project_search":
            return await asyncio.to_thread(self.working_tree_search, request, request.query)
        if name == "project_files":
            return await asyncio.to_thread(self.working_tree_search, request)
        if name == "project_rules":
            return {"ok": True, "instructions": self.instructions(request.directory)}
        if name == "project_git":
            return await asyncio.to_thread(self.git, request)
        if name == "project_skill":
            return await asyncio.to_thread(self.skill, request)
        if name == "project_diagnostics":
            return {"ok": True, "data": await asyncio.to_thread(self.diagnostics, request)}
        if name == "project_read":
            return self.read(request)
        if name in {"project_edit", "project_create"}:
            return self.write(name, request)
        if name == "project_powershell":
            timeout = 600 if request.command.casefold() == "run-script" else 30
            result = await run_command(self.powershell(request), self.root, timeout)
            result["command"] = request.command + " " + request.path
            return result
        if name == "project_build":
            return await run_command(self.build(request), self.root)
        argv = self.test(request)
        runner = request.runner.casefold()
        if runner in {"unittest", "pytest"}:
            # 复验从源码加载，避免同一秒内等长修改时命中旧字节码。
            with tempfile.TemporaryDirectory(prefix="agentscope-test-cache-") as cache:
                command = [argv[0], "-B", "-X", f"pycache_prefix={cache}", *argv[1:]]
                result = await run_command(command, self.root)
            result["verification_target"] = subprocess.list2cmdline(argv)
        else:
            result = await run_command(argv, self.root)
        if runner == "unittest" and result["ok"] and re.search(r"(?m)^Ran 0 tests? in ", result["output"]):
            result["ok"] = False
            result["output"] += "\n未发现可运行的测试。"
        return result
