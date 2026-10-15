r"""从仓库中的合成案例结果生成 README 首图。

在仓库根目录运行：
python -B assets\readme-risk-core.py --font C:\Windows\Fonts\msyh.ttc --bold-font C:\Windows\Fonts\msyhbd.ttc

此脚本固定展示归档的示例结果（examples/results/analysis.json），不依赖业务包；
企业评分、链路风险值、投入产出系数与定量案例均从该文件读取，图中不引入新的数字。
核对新版本后可用 --source-sha256 显式更改校验值。
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import warnings

import matplotlib

matplotlib.use("Agg")
from matplotlib.font_manager import FontProperties
from matplotlib.ft2font import FT2Font
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "results" / "analysis.json"
SOURCE_SHA256 = "48af71180a283f404203181c6b7746fcd0d0875d0713b55138164afa83942dc4"
WIDTH, HEIGHT, DPI = 1800, 1000, 100
COLORS = {
    "background": "#F5F2EA",
    "paper": "#FFFFFF",
    "side": "#ECE9DF",
    "ink": "#1E3532",
    "secondary": "#5C6E68",
    "muted": "#8A9994",
    "copper": "#B4693A",
    "grid": "#E4E0D4",
    "high": "#B4443C",
    "high_bg": "#F7ECE6",
    "mid": "#B07C2A",
    "mid_bg": "#F6F1E3",
    "low": "#4E7A5F",
    "low_bg": "#EDF3E9",
    "node": "#FFFFFF",
    "node_edge": "#B9C4BC",
    "arrow": "#7C8F88",
}


def source_digest(raw: bytes) -> str:
    """仅统一 CRLF/LF 换行，保持其余字节不变。"""
    return sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def strongest_path(coefficients) -> float:
    """从首个节点到末个节点的所有路径中，取边系数连乘的最大值。"""
    last = len(coefficients) - 1
    best = 0.0

    def walk(node, product, visited):
        nonlocal best
        if node == last:
            best = max(best, product)
            return
        for nxt, value in enumerate(coefficients[node]):
            if value and nxt not in visited:
                walk(nxt, product * value, visited | {nxt})

    walk(0, 1.0, {0})
    return best


def read_snapshot(source: Path, expected_sha256: str):
    """校验示例结果版本，再提取绘图所需的全部字段。"""
    raw = source.read_bytes()
    digest = source_digest(raw)
    if digest != expected_sha256:
        raise ValueError("analysis.json 与指定版本不符；核对数据后可用 --source-sha256 显式指定校验值")
    data = json.loads(raw.decode("utf-8"))
    companies = tuple(
        {
            "name": company["name"],
            "score": company["risk_score"],
            "action": company["action"],
            "missing": len(company["missing_inputs"]),
            "review": bool(company["review_required"]),
        }
        for company in data["credit"]["companies"]
    )
    coefficients = data["input_output"]["direct_coefficients"]
    nodes = data["input_output"]["nodes"]
    leontief = data["input_output"]["leontief_inverse"]
    edge_values = {}
    for row, source_name in enumerate(nodes):
        for column, target_name in enumerate(nodes):
            if coefficients[row][column]:
                edge_values[(source_name, target_name)] = coefficients[row][column]
    return {
        "companies": companies,
        "nodes": tuple(nodes),
        "edge_values": edge_values,
        "strongest_path": strongest_path(coefficients),
        "total_effect": leontief[0][3],
    }, digest


def overview(snapshot, regular_font: Path, bold_font: Path):
    """布局用像素表达，使导出的文字和间距保持稳定。"""
    regular_glyphs = FT2Font(str(regular_font)).get_charmap()
    bold_glyphs = FT2Font(str(bold_font)).get_charmap()
    fonts = {
        False: FontProperties(fname=str(regular_font)),
        True: FontProperties(fname=str(bold_font)),
    }
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(COLORS["background"])

    def label(x, y, text, size=24, color="ink", bold=False, **kwargs):
        glyphs = bold_glyphs if bold else regular_glyphs
        missing = {char for char in text if not char.isspace() and ord(char) not in glyphs}
        if missing:
            raise ValueError(f"中文字体缺少字符：{''.join(sorted(missing))}")
        return fig.text(
            x / WIDTH, 1 - y / HEIGHT, text,
            fontsize=size * 72 / DPI, fontproperties=fonts[bold],
            color=COLORS.get(color, color), va="top", **kwargs,
        )

    def panel(x, y, width, height, color, radius=22, edge=None, linewidth=0):
        shape = FancyBboxPatch(
            (x / WIDTH, 1 - (y + height) / HEIGHT),
            width / WIDTH, height / HEIGHT,
            boxstyle=f"round,pad=0,rounding_size={radius / WIDTH}",
            transform=fig.transFigure, linewidth=linewidth,
            edgecolor=COLORS.get(edge, edge) if edge else "none",
            facecolor=COLORS.get(color, color), zorder=2,
        )
        fig.add_artist(shape)

    def arrow(start, end, color="arrow", width=2.2):
        patch = FancyArrowPatch(
            (start[0] / WIDTH, 1 - start[1] / HEIGHT),
            (end[0] / WIDTH, 1 - end[1] / HEIGHT),
            transform=fig.transFigure, arrowstyle="-|>",
            mutation_scale=22, linewidth=width, color=COLORS.get(color, color),
            shrinkA=0, shrinkB=0, zorder=3,
        )
        fig.add_artist(patch)

    label(64, 40, "SUPPLY CHAIN / RISK CORE", size=20, color="copper")
    label(1736, 42, "v0.3.0", size=20, color="muted", ha="right")
    label(60, 78, "产业链风险分析与授信研究", size=56, bold=True)
    label(64, 168, "企业输入 → 特征与评分 → 授信规则 → 传导情景 → 可核验报告", size=25, color="secondary")

    panel(48, 225, 470, 630, "paper")
    panel(546, 225, 640, 630, "paper")
    panel(1214, 225, 538, 630, "side")

    label(77, 255, "从输入到报告", size=30, bold=True)
    label(78, 300, "一条命令运行全部步骤", size=21, color="secondary")
    steps = (
        ("01", "输入契约", "金额、时间与来源显式填写"),
        ("02", "特征与评分", "四维子分，缺失保留为未知"),
        ("03", "授信规则", "放宽、收紧与维持三类结论"),
        ("04", "传导情景", "成本与需求两条链分别计算"),
        ("05", "报告与复现", "输入与源码指纹逐项核验"),
    )
    for index, (number, title, detail) in enumerate(steps):
        top = 358 + index * 96
        label(78, top, number, size=24, color="copper", bold=True)
        label(148, top - 2, title, size=25)
        label(148, top + 36, detail, size=20, color="secondary")

    label(575, 255, "同一评分，不同结论", size=30, bold=True)
    label(576, 300, "合成案例：三家企业，评分口径相同", size=21, color="secondary")
    company_tones = ("low", "high", "mid")
    for index, company in enumerate(snapshot["companies"]):
        top = 336 + index * 156
        tone = company_tones[index]
        panel(575, top, 582, 142, f"{tone}_bg", radius=18)
        label(603, top + 26, company["name"], size=26)
        if company["missing"]:
            detail = f"{company['missing']} 项输入缺失，标记待复核"
        elif company["action"] == "收紧":
            detail = "命中财务恶化规则"
        else:
            detail = "输入已齐"
        label(603, top + 72, detail, size=20, color="secondary")
        label(1129, top + 16, f"{company['score']:g}", size=44, bold=True, ha="right")
        action = company["action"] + (" · 需复核" if company["review"] else "")
        label(1129, top + 84, action, size=24, color=tone, bold=True, ha="right")
    label(576, 828, "评分相同：甲满足放宽条件；乙命中财务规则收紧；丙保留输出并标记复核。", size=19, color="secondary")

    label(1243, 255, "产业链传导", size=30, bold=True)
    label(1244, 300, "源头涨价沿采购关系逐跳传导", size=21, color="secondary")
    node_boxes = {
        "原料": (1244, 424, 110, 58),
        "加工": (1426, 366, 110, 58),
        "替代环节": (1426, 502, 110, 58),
        "终端": (1604, 424, 120, 58),
    }
    edges = (
        ("原料", "加工", (1362, 438), (1426, 404), (1370, 366)),
        ("原料", "替代环节", (1362, 470), (1426, 528), (1372, 508)),
        ("加工", "终端", (1536, 396), (1604, 438), (1548, 356)),
        ("替代环节", "终端", (1536, 532), (1604, 470), (1548, 492)),
    )
    for source_name, target_name, start, end, text_position in edges:
        arrow(start, end)
        label(*text_position, f"{snapshot['edge_values'][(source_name, target_name)]:g}", size=20, color="copper")
    for name, (x, y, width, height) in node_boxes.items():
        panel(x, y, width, height, "node", radius=14, edge="node_edge", linewidth=1.6)
        label(x + width / 2, y + height / 2 - 14, name, size=22, ha="center")

    label(1244, 640, "最强路径系数连乘", size=20, color="secondary")
    label(1244, 670, f"{snapshot['strongest_path']:g}", size=40, bold=True)
    label(1502, 640, "投入产出总效应", size=20, color="secondary")
    label(1502, 670, f"{snapshot['total_effect']:g}", size=40, bold=True)
    label(1244, 744, "路径连乘展示单链暴露；", size=19, color="secondary")
    label(1244, 772, "Leontief 逆矩阵累计直接与间接效应。", size=19, color="secondary")
    label(1244, 818, "结构参数来自合成案例输入。", size=19, color="muted")

    label(64, 884, "58 项测试", size=30, bold=True)
    label(64, 928, "单元与集成，含真实子进程回归", size=20, color="secondary")
    label(500, 884, "6 种输出格式", size=30, bold=True)
    label(500, 928, "JSON · Markdown · HTML · SVG · Word · Excel", size=20, color="secondary")
    label(1060, 884, "4 个定量案例", size=30, bold=True)
    label(1060, 928, "铜价 · 饲料 · 板卡 · 收入区间", size=20, color="secondary")
    label(1480, 884, "决赛 5 家企业", size=30, bold=True)
    label(1480, 928, "31 个工作表 · 13 张产业链图", size=20, color="secondary")

    label(64, 962, "数据：仓库示例结果 analysis.json（合成案例，不代表真实授信建议）", size=19, color="secondary")
    label(1736, 962, "示例结果可复现核验", size=19, color="muted", ha="right")
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--bold-font", required=True, type=Path)
    parser.add_argument("--source-sha256", default=SOURCE_SHA256, help="analysis.json 按 LF 归一化后的 SHA-256")
    args = parser.parse_args()
    output = Path(__file__).with_suffix(".png")
    snapshot, source_hash = read_snapshot(SOURCE, args.source_sha256)
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Glyph .* missing from.*")
        figure = overview(snapshot, args.font, args.bold_font)
        figure.savefig(output, dpi=DPI, facecolor=figure.get_facecolor(), metadata={
            "Title": "产业链风险分析与授信研究 — README 首图",
            "Description": f"Source: examples/results/analysis.json; SHA256 (LF normalized): {source_hash}; synthetic demo case",
        })
        plt.close(figure)
    if source_digest(SOURCE.read_bytes()) != source_hash:
        raise RuntimeError("输入数据在绘图过程中发生变化")
    print(json.dumps({
        "output": str(output), "size": [WIDTH, HEIGHT], "source_sha256_lf": source_hash,
        "companies": [company["name"] for company in snapshot["companies"]],
        "strongest_path": snapshot["strongest_path"], "total_effect": snapshot["total_effect"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
