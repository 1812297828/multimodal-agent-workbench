"""AOI 工程辅助工具：代码读取、版本对照、静态检查和图像诊断。"""

import difflib
import json
from pathlib import Path
import shutil
import uuid
import xml.etree.ElementTree as ET

from library_import import decode
from project_index import APP, PROJECT_ROOT, ProjectIndex, command, git_command, git_state

INDEX = ProjectIndex()
REPORTS = APP / "local_data/aoi_tools/reports"


def repositories():
    catalog = INDEX.status()
    return {"root": catalog.get("root"), "default_repository": catalog.get("default_repository"),
            "source_files": catalog.get("source_files", 0), "symbols": catalog.get("symbols", 0),
            "repositories": [{key: entry.get(key) for key in ["id", "path", "branch", "head", "source_files"]}
                             for entry in catalog["repositories"]]}


def git_status(repository):
    directory = Path(INDEX.repository(repository)["path"])
    return {"repository": repository, **git_state(directory)}


def git_diff(repository, path="", staged=False):
    directory = Path(INDEX.repository(repository)["path"])
    if not (directory / ".git").exists():
        raise ValueError("该目录不是 Git 仓库")
    args = ["diff", "--no-ext-diff", "--no-textconv", "--unified=3"]
    if staged:
        args.append("--cached")
    if path:
        file = INDEX.resolve_file(repository, path)
        args += ["--", file.relative_to(directory).as_posix()]
    code, output, error = git_command(directory, args)
    if code:
        raise ValueError(error[:500])
    return {"repository": repository, "staged": staged, "truncated": len(output) > 60000, "diff": output[:60000]}


def git_history(repository, path="", count=10):
    directory = Path(INDEX.repository(repository)["path"])
    args = ["log", "--no-show-signature", f"-n{min(max(count, 1), 30)}", "--date=short", "--format=%h %ad %s"]
    if path:
        file = INDEX.resolve_file(repository, path)
        args += ["--", file.relative_to(directory).as_posix()]
    code, output, error = git_command(directory, args)
    if code:
        raise ValueError(error[:500])
    return {"repository": repository, "history": output[:12000]}


def compare_sources(repository_a, path_a, repository_b, path_b):
    left = INDEX.resolve_file(repository_a, path_a)
    right = INDEX.resolve_file(repository_b, path_b)
    lines = list(difflib.unified_diff(decode(left.read_bytes()).splitlines(), decode(right.read_bytes()).splitlines(),
                                     fromfile=f"{repository_a}/{path_a}", tofile=f"{repository_b}/{path_b}", n=3))
    return {"identical": not lines, "truncated": len(lines) > 700, "diff": "\n".join(lines[:700])[:65000]}


def tool_environment():
    executables = {"git": shutil.which("git"), "ruff": str(APP / ".venv/Scripts/ruff.exe"),
                   "cppcheck": str(APP / ".venv/Scripts/cppcheck.exe"), "MSBuild": shutil.which("MSBuild"),
                   "CMake": shutil.which("cmake"), "clang-tidy": shutil.which("clang-tidy")}
    result = {}
    for name, path in executables.items():
        available = bool(path and Path(path).is_file())
        result[name] = {"available": available, "path": path if available else None}
        if available and name in {"git", "ruff", "cppcheck"}:
            _, output, error = command([path, "--version"])
            result[name]["version"] = (output or error).strip()[:300]
    import cv2
    result["OpenCV"] = {"available": True, "version": cv2.__version__, "purpose": "独立的图像诊断环境，不替换工程使用的 OpenCV 3.4.13 SDK"}
    return result


def build_profile(repository, path):
    file = INDEX.resolve_file(repository, path)
    if file.suffix.lower() != ".vcxproj":
        raise ValueError("请选择 .vcxproj 工程文件")
    tree = ET.fromstring(decode(file.read_bytes()))
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    groups = []
    for group in tree:
        condition = group.attrib.get("Condition", "通用配置")
        values = {}
        for node in group.iter():
            name = node.tag.rsplit("}", 1)[-1]
            if name in {"PlatformToolset", "PreprocessorDefinitions", "AdditionalIncludeDirectories", "AdditionalLibraryDirectories", "AdditionalDependencies", "Optimization", "LanguageStandard"} and node.text:
                values.setdefault(name, []).append(node.text.strip())
        if values:
            groups.append({"condition": condition, "settings": values})
    platforms = [node.attrib.get("Include") for node in tree.findall("m:ItemGroup/m:ProjectConfiguration", namespace)]
    return {"repository": repository, "project": path, "configurations": platforms, "groups": groups,
            "environment": tool_environment(), "note": "按工程文件读取；继承属性、现场 SDK 和最终编译命令仍需另行核对，本次没有运行编译。"}


def check_sources(repository, paths):
    if not 1 <= len(paths) <= 8:
        raise ValueError("每次请选择 1 至 8 个代码文件")
    files = [INDEX.resolve_file(repository, path) for path in paths]
    python = [file for file in files if file.suffix.lower() == ".py"]
    cpp = [file for file in files if file.suffix.lower() in {".c", ".cpp", ".cc", ".h", ".hpp", ".cxx"}]
    if len(python) + len(cpp) != len(files):
        raise ValueError("静态检查仅支持 Python、C、C++ 代码")
    REPORTS.mkdir(parents=True, exist_ok=True)
    findings = []
    reports = []
    directory = INDEX.repository(repository)["path"]
    if python:
        executable = APP / ".venv/Scripts/ruff.exe"
        code, output, error = command([str(executable), "check", "--no-cache", "--no-fix", "--target-version", "py38",
                                       "--select", "E9,F821,F823", "--output-format", "json", *map(str, python)], cwd=directory, timeout=60)
        if code not in {0, 1}:
            raise ValueError("Python 静态检查未完成：" + error[:400])
        values = json.loads(output)
        findings.extend({"tool": "Ruff", "file": item["filename"], "line": item["location"]["row"],
                         "rule": item["code"], "message": item["message"]} for item in values[:100])
        report = REPORTS / (uuid.uuid4().hex + "-python.json")
        report.write_text(output, encoding="utf-8")
        reports.append(str(report))
    if cpp:
        executable = APP / ".venv/Scripts/cppcheck.exe"
        code, output, error = command([str(executable), "--enable=warning,performance,portability", "--std=c++17", "--platform=win64",
                                       "--max-configs=4", "--check-level=reduced", "--suppress=missingIncludeSystem", "--xml", "--xml-version=2",
                                       *map(str, cpp)], cwd=directory, timeout=90)
        start = error.find("<?xml")
        if start < 0:
            raise ValueError("C++ 静态检查未完成：" + error[:500])
        xml = error[start:]
        tree = ET.fromstring(xml)
        for item in tree.findall("./errors/error")[:100]:
            location = item.find("location")
            findings.append({"tool": "Cppcheck", "file": location.attrib.get("file") if location is not None else None,
                             "line": int(location.attrib.get("line", 0)) if location is not None else 0,
                             "rule": item.attrib.get("id"), "severity": item.attrib.get("severity"), "message": item.attrib.get("msg")})
        report = REPORTS / (uuid.uuid4().hex + "-cppcheck.xml")
        report.write_text(xml, encoding="utf-8")
        reports.append(str(report))
    return {"repository": repository, "findings": findings, "reports": reports,
            "note": "检查没有改写或格式化源文件。动态 Python 导入和专有 SDK 宏可能产生误报；静态结果需要复核，不能代替编译和运行验证。"}


def _image(path, source="资料"):
    import cv2
    import numpy as np
    roots = {"资料": PROJECT_ROOT.parent / "资料", "代码": PROJECT_ROOT}
    if source not in roots:
        raise ValueError("图像来源请选择资料或代码")
    root = roots[source].resolve(strict=True)
    file = (root / path).resolve(strict=True)
    if not file.is_relative_to(root) or file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        raise ValueError("只能读取已接入目录中的图像文件")
    if any(part.startswith(".") for part in file.relative_to(root).parts) or file.stat().st_size > 30_000_000:
        raise ValueError("不读取隐藏目录或超过 30 MB 的单张图像")
    from PIL import Image
    with Image.open(file) as header:
        if header.width * header.height > 25_000_000:
            raise ValueError("单次最多处理 2500 万像素，请先选择局部图像")
    value = cv2.imdecode(np.frombuffer(file.read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if value is None:
        raise ValueError("图像解码失败")
    return file, value


def image_statistics(path, source="资料"):
    import cv2
    import numpy as np
    file, image = _image(path, source)
    return {"file": str(file), "shape": list(image.shape), "dtype": str(image.dtype),
            "min": float(np.min(image)), "max": float(np.max(image)), "mean": float(np.mean(image)),
            "percentiles": np.percentile(image, [1, 50, 99]).tolist(), "opencv": cv2.__version__}


def compare_images(path_a, path_b, source="资料"):
    import cv2
    import numpy as np
    left, a = _image(path_a, source)
    right, b = _image(path_b, source)
    if a.shape != b.shape or a.dtype != b.dtype:
        return {"compatible": False, "left_shape": list(a.shape), "right_shape": list(b.shape),
                "left_dtype": str(a.dtype), "right_dtype": str(b.dtype), "note": "尺寸或位深不同，未自动缩放、对齐或比较。"}
    difference = np.abs(a.astype(np.float64) - b.astype(np.float64))
    nonzero = np.any(difference > 0, axis=2) if difference.ndim == 3 else difference > 0
    result = {"compatible": True, "left": str(left), "right": str(right), "mean_absolute_error": float(np.mean(difference)),
              "maximum_absolute_error": float(np.max(difference)), "changed_pixel_ratio": float(np.mean(nonzero))}
    if a.ndim == 2:
        shift, response = cv2.phaseCorrelate(a.astype(np.float32), b.astype(np.float32))
        result.update(estimated_translation=list(shift), translation_response=float(response))
    REPORTS.mkdir(parents=True, exist_ok=True)
    intensity = difference.max(axis=2) if difference.ndim == 3 else difference
    normalized = (intensity * (255.0 / max(float(np.max(intensity)), 1))).astype(np.uint8)
    output = REPORTS / (uuid.uuid4().hex + "-difference.png")
    ok, encoded = cv2.imencode(".png", cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO))
    if not ok:
        raise ValueError("差异图生成失败")
    output.write_bytes(encoded.tobytes())
    result.update(difference_preview=str(output), note="仅为像素差异与位移估计，不代表缺陷判定或算法正确率；没有改动原图。")
    return result
