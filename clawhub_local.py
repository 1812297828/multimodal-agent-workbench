"""为已核对的 ClawHub 技能提供本地适配包，同时保留原版目录浏览。"""

import hashlib
import io
import json
from pathlib import Path
import zipfile

import frontmatter

from agentscope.app.hub import ClawSkillHub, SkillCard, SkillHubPage
from agentscope.app.hub._skill._base import SkillArchive

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "local_data/skills/clawhub/registry.json"
SKILL_ROOT = ROOT / "clawhub_skills"


def installed_packages():
    if not REGISTRY.exists():
        return []
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["skills"]


def cached_skill_paths():
    return [str(SKILL_ROOT / item["name"]) for item in installed_packages()]


class LocalClawSkillHub(ClawSkillHub):
    """固定已安装技能的作者、版本和文件；其他技能继续访问 ClawHub。"""

    def _cached(self, card_id):
        return next((item for item in installed_packages()
                     if card_id in {item["card_id"], item["name"]}), None)

    async def get_skill(self, user_id, card_id):
        item = self._cached(card_id)
        if item is None:
            return await super().get_skill(user_id, card_id)
        parsed = frontmatter.loads((SKILL_ROOT / item["name"] / "SKILL.md").read_text(encoding="utf-8"))
        return SkillCard(hub_id=self.hub_id, id=item["card_id"], name=item["name"],
                         display_name=item["display_name"], description=item["description"],
                         version=item["version"], author=item["owner"], url=item["url"],
                         tags=["本地适配", "AOI", "ClawHub"], markdown=parsed.content,
                         metadata={"upstream_version": item["upstream_version"],
                                   "archive_sha256": item["archive_sha256"]})

    async def list_skills(self, user_id, q=None, cursor=None, limit=20):
        selected = [item for item in installed_packages() if not q or
                    q.casefold() in (item["name"] + item["display_name"] + item["description"]).casefold()]
        local = [await self.get_skill(user_id, item["card_id"]) for item in selected]
        try:
            upstream_limit = max(1, limit - len(local)) if not cursor and not q else limit
            page = await super().list_skills(user_id, q=q, cursor=cursor, limit=upstream_limit)
        except Exception:
            # 离线时仍能安装已审核的三个本地包；没有本地匹配则保留原始错误。
            if local and not cursor:
                return SkillHubPage(cards=local[:limit])
            raise
        cards = [] if cursor else local
        seen = {card.id for card in cards}
        for card in page.cards:
            replacement = self._cached(card.id)
            if replacement:
                card = await self.get_skill(user_id, replacement["card_id"])
            if card.id not in seen:
                seen.add(card.id)
                cards.append(card)
        return SkillHubPage(cards=cards[:limit], next_cursor=page.next_cursor)

    async def download(self, user_id, card_id, version=None, **kwargs):
        item = self._cached(card_id)
        if item is None:
            return await super().download(user_id, card_id, version, **kwargs)
        if version is not None and version != item["version"]:
            raise ValueError("本地适配包版本不匹配，请从技能中心查看已固定的版本")
        directory = SKILL_ROOT / item["name"]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for relative, expected in item["files"].items():
                path = directory / relative
                if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
                    raise ValueError("本地技能文件路径无效")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise ValueError("本地技能文件已改变，请核对后更新适配包清单：" + relative)
                archive.writestr(item["name"] + "/" + relative, raw)

        async def stream():
            yield buffer.getvalue()

        return SkillArchive(format="zip", stream=stream())
