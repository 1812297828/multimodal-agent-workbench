"""在原版技能中心和 MCP 中心提供本地 AOI 工具包。"""

import io
from pathlib import Path
import zipfile

from agentscope.app.hub import MCPHubBase, MCPCard, MCPHubPage, SkillHubBase, SkillCard, SkillHubPage
from agentscope.app.hub._skill._base import SkillArchive
from agentscope.mcp import MCPClient, StdioMCPConfig
from clawhub_local import cached_skill_paths

ROOT = Path(__file__).resolve().parent
MCP_PROFILES = {
    "code": ("aoi-code", "AOI 代码与 Git", "跨仓库代码搜索、函数与类型定位、代码行读取、Git 状态与差异、版本对照。"),
    "quality": ("aoi-quality", "AOI 代码检查与工程诊断", "Cppcheck、Ruff 和 Visual Studio 工程配置诊断，检查结果保存到独立目录。"),
    "vision": ("aoi-vision", "AOI 图像诊断", "图像尺寸、位深、强度统计、像素差异、整体平移估计与差异图。"),
}
SKILLS = {
    "aoi-project-navigation": ("AOI 工程定位与版本对照", "按当前工作树和历史版本查代码，保留用户现有修改。"),
    "aoi-algorithm-review": ("AOI 算法优化复核", "保险丝测宽、Dijkstra、坐标与单位、边界和并行正确性检查。"),
    "aoi-code-quality": ("项目代码规范与工程检查", "使用项目代码规范、Cppcheck、Ruff 和构建配置诊断。"),
    "aoi-image-regression": ("AOI 图像与回归比较", "区分位深、对齐、像素差异和真实算法回归，保留实验依据。"),
}


def mcp_config(profile):
    return StdioMCPConfig(command=str(ROOT / ".venv/Scripts/python.exe"),
                          args=[str(ROOT / "aoi_mcp.py"), "--profile", profile], cwd=str(ROOT),
                          env={"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})


def default_mcps():
    return [MCPClient(name=value[0], mcp_config=mcp_config(profile), is_stateful=True) for profile, value in MCP_PROFILES.items()]


def skill_paths():
    folders = [ROOT / "aoi_skills" / name for name in SKILLS]
    return [str(folder) for folder in folders if (folder / "SKILL.md").is_file()] + cached_skill_paths()


class AOIMCPHub(MCPHubBase):
    def __init__(self):
        super().__init__("aoi-local", "AOI 本地工具", "根据本机 AOI 资料与工程配置的代码、版本、质量和图像工具，无需 API key。")

    async def list_mcps(self, user_id, q=None, cursor=None, limit=20):
        cards = [await self.get_mcp(user_id, key) for key in MCP_PROFILES]
        if q:
            cards = [card for card in cards if q.casefold() in (card.display_name + card.description).casefold()]
        return MCPHubPage(cards=cards[:limit])

    async def get_mcp(self, user_id, card_id):
        if card_id not in MCP_PROFILES:
            raise KeyError(card_id)
        name, display_name, description = MCP_PROFILES[card_id]
        return MCPCard(hub_id=self.hub_id, id=card_id, name=name, display_name=display_name,
                       description=description, version="1.0.0", author="本地 AOI 工具包", auth="none",
                       tags=["本地", "AOI", "无需密钥"], config_template=mcp_config(card_id),
                       readme=description + "\n\n插件不提供修改原工程、提交或推送操作。静态分析和图像差异结果需要结合实际工程复核。")


class AOISkillHub(SkillHubBase):
    def __init__(self):
        super().__init__("aoi-local", "AOI 专用技能", "从资料、当前代码和项目代码规范整理的本地技能。")

    async def list_skills(self, user_id, q=None, cursor=None, limit=20):
        cards = [await self.get_skill(user_id, key) for key in SKILLS
                 if (ROOT / "aoi_skills" / key / "SKILL.md").is_file()]
        if q:
            cards = [card for card in cards if q.casefold() in (card.display_name + card.description).casefold()]
        return SkillHubPage(cards=cards[:limit])

    async def get_skill(self, user_id, card_id):
        if card_id not in SKILLS or not (ROOT / "aoi_skills" / card_id / "SKILL.md").is_file():
            raise KeyError(card_id)
        display_name, description = SKILLS[card_id]
        body = (ROOT / "aoi_skills" / card_id / "SKILL.md").read_text(encoding="utf-8")
        return SkillCard(hub_id=self.hub_id, id=card_id, name=card_id, display_name=display_name,
                         description=description, version="1.0.0", author="本地 AOI 工具包", markdown=body,
                         tags=["AOI", "本地", "代码与算法"])

    async def download(self, user_id, card_id, version=None):
        await self.get_skill(user_id, card_id)
        directory = ROOT / "aoi_skills" / card_id
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in directory.rglob("*"):
                if path.is_file():
                    archive.write(path, arcname=(Path(card_id) / path.relative_to(directory)).as_posix())
        async def stream():
            yield buffer.getvalue()
        return SkillArchive(format="zip", stream=stream())
