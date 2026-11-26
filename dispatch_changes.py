"""保存本轮文件改动，生成差异并按版本撤销。"""

import base64
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from dispatch_engine import save_json


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def replace_file(path, before, after):
    if after is None:
        if path.read_bytes() != before:
            raise ValueError("文件已发生变化，请刷新改动后再操作。")
        path.unlink()
        return
    if before is None:
        # 新文件只用独占方式创建，避免覆盖同时新增的文件。
        with path.open("xb") as stream:
            stream.write(after)
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(after)
    try:
        shutil.copymode(path, temporary)
        if path.read_bytes() != before:
            raise ValueError("文件已发生变化，请重新读取。")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ProjectChanges:
    def __init__(self, directory, execution):
        self.directory = directory
        self.execution = execution

    @staticmethod
    def _read(path):
        return path.read_bytes() if path.exists() else None

    @staticmethod
    def _encode(data):
        return base64.b64encode(data).decode("ascii") if data is not None else None

    @staticmethod
    def _decode(data):
        return base64.b64decode(data) if data is not None else None

    def _path(self, ident):
        if len(ident) != 64 or any(char not in "0123456789abcdef" for char in ident):
            raise ValueError("改动记录不存在。")
        return self.directory / (ident + ".json")

    def _record(self, ident):
        path = self._path(ident)
        if not path.is_file():
            raise ValueError("改动记录不存在。")
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, path, before, after):
        relative = path.relative_to(self.execution.root).as_posix()
        ident = digest(relative.casefold().encode("utf-8"))
        metadata = self._path(ident)
        previous = self._record(ident) if metadata.is_file() else None
        initial = self._encode(before)
        if previous and not previous["restored"]:
            if previous["after_hash"] != digest(before):
                raise ValueError("文件在本轮执行中发生了其他修改，请先核对差异。")
            initial = previous["before"]
        current = {"id": ident, "path": relative, "before": initial, "after": self._encode(after),
                   "after_hash": digest(after), "restored": False}
        # 先保存原内容。进程若在执行期间退出，撤销仍会校验磁盘版本。
        save_json(metadata, current)
        try:
            replace_file(path, before, after)
        except (ValueError, OSError):
            if previous is None:
                metadata.unlink(missing_ok=True)
            else:
                save_json(metadata, previous)
            raise
        return relative

    def list(self):
        entries = []
        for file in sorted(self.directory.glob("*.json")):
            row = self._record(file.stem)
            entries.append({key: row[key] for key in ("id", "path", "restored")})
        return entries

    def detail(self, ident):
        from dispatch_execution import file_text

        row = self._record(ident)
        before = self._decode(row["before"])
        after = self._decode(row["after"])
        left = file_text(before)[0].splitlines() if before is not None else []
        right = file_text(after)[0].splitlines()
        path = row["path"]
        lines = difflib.unified_diff(left, right, fromfile=("a/" + path) if before is not None else "/dev/null",
                                     tofile="b/" + path, lineterm="")
        output = []
        size = 0
        for line in lines:
            size += len(line) + 1
            if size > 100_000:
                output.append("［差异过长，此处仅显示前半部分］")
                break
            output.append(line)
        return {"id": ident, "path": path, "restored": row["restored"], "diff": "\n".join(output)}

    def restore(self, ident):
        row = self._record(ident)
        if row["restored"]:
            return row["path"]
        path = self.execution.path(row["path"], source=True, exists=False)
        current = self._read(path)
        initial = self._decode(row["before"])
        if current != initial:
            if digest(current) != row["after_hash"]:
                raise ValueError("此文件在本轮结束后又被修改。请查看差异并手动合并。")
            replace_file(path, current, initial)
        row["restored"] = True
        save_json(self._path(ident), row)
        return row["path"]
