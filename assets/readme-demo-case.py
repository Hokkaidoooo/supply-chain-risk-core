r"""从仓库中的合成案例结果生成 README 案例图。

在仓库根目录运行：
python -B assets\readme-demo-case.py --font C:\Windows\Fonts\msyh.ttc --bold-font C:\Windows\Fonts\msyhbd.ttc

此脚本固定展示归档的示例结果（examples/results/analysis.json），不依赖业务包；
链路风险值、两条链的情景结果与四个定量案例均从该文件读取，图中不引入新的数字。
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
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "results" / "analysis.json"
SOURCE_SHA256 = "48af71180a283f404203181c6b7746fcd0d0875d0713b55138164afa83942dc4"
WIDTH, HEIGHT, DPI = 1600, 900, 100
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


def case_display(case):
    """把每个定量案例映射为图上的数字与说明。"""
    result = case["result"]
    if case["id"] == "copper":
        return f"{result['retained_cost_per_100_revenue']:g}", ("元/百元基期收入",)
    if case["id"] == "feed":
        return f"{result['cost_change_pct']:g}%", ("采购组合成本变化",)
    if case["id"] == "board":
        return (
            f"{result['brand_retained_cost']:g} / {result['board_retained_cost']:g}",
            ("品牌 / 板卡吸收额", result["comparison"]),
        )
    if case["id"] == "overlap":
        return f"{result['lower']:g}–{result['upper']:g}", ("与输入收入同单位",)
    raise ValueError(f"未处理的定量案例：{case['id']}")


def read_snapshot(source: Path, expected_sha256: str):
    """校验示例结果版本，再提取绘图所需的全部字段。"""
    raw = source.read_bytes()
    digest = source_digest(raw)
    if digest != expected_sha256:
        raise ValueError("analysis.json 与指定版本不符；核对数据后可用 --source-sha256 显式指定校验值")
    data = json.loads(raw.decode("utf-8"))
    chains = {chain["id"]: chain for chain in data["chains"]}
    cost = chains["cost-chain"]
    demand = chains["demand-chain"]
    nodes = tuple(
        (row["name"], row["risk_value"], row["risk"])
        for row in cost["comparison_rows"]
    )
    cases = tuple((case["title"],) + case_display(case) for case in data["quantitative_cases"])
    return {
        "nodes": nodes,
        "cost_parameters": cost["scenario"]["parameters"],
        "cost_result": cost["scenario"]["result"],
        "demand_parameters": demand["scenario"]["parameters"],
        "demand_result": demand["scenario"]["result"],
        "cases": cases,
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

    label(56, 38, "DEMO CASE", size=18, color="copper")
    label(52, 70, "合成案例：一次运行的实际输出", size=44, bold=True)
    label(56, 146, "三家企业评分 · 两条传导链 · 四个定量案例；输入与源码指纹随结果核验", size=22, color="secondary")

    panel(48, 192, 760, 615, "paper")
    label(77, 222, "两条传导链", size=28, bold=True)
    label(78, 265, "同一组节点，正向与逆向分别计算", size=20, color="secondary")
    tone_of = {"high": "high", "low": "low", "plain": "ink"}
    edge_of = {"high": "high", "low": "low", "plain": "node_edge"}
    background_of = {"high": "high_bg", "low": "low_bg", "plain": "node"}
    mark_of = {"high": "▲ 最高", "low": "▼ 最低"}
    for index, (name, risk_value, risk) in enumerate(snapshot["nodes"]):
        x = 78 + index * 243
        panel(x, 330, 165, 66, background_of[risk], radius=14, edge=edge_of[risk], linewidth=1.6)
        label(x + 16, 342, name, size=20)
        label(x + 16, 368, f"风险值 {risk_value}", size=22, color=tone_of[risk], bold=True)
        if risk in mark_of:
            label(x + 82, 404, mark_of[risk], size=17, color=tone_of[risk], ha="center")
        if index < 2:
            arrow((x + 165, 363), (x + 243, 363))

    fig.add_artist(Line2D(
        [78 / WIDTH, 778 / WIDTH], [1 - 448 / HEIGHT] * 2,
        transform=fig.transFigure, color=COLORS["grid"], linewidth=1.4,
    ))

    cost_parameters = snapshot["cost_parameters"]
    cost_result = snapshot["cost_result"]
    label(78, 470, "成本链", size=23, bold=True)
    label(180, 475, f"源头价格 +{cost_parameters['shock'] * 100:g}%、转嫁 {cost_parameters['pass_through'] * 100:g}%", size=20, color="secondary")
    label(78, 510, f"新增成本 {cost_result['added_cost_yi']:g} 亿元 · 利息保障 {cost_result['interest_cover_before_x']:.2f} → {cost_result['interest_cover_after_x']:.2f} 倍 · 临界涨幅 {cost_result['critical_shock_pct']:g}%", size=21)

    demand_parameters = snapshot["demand_parameters"]
    demand_result = snapshot["demand_result"]
    label(78, 578, "需求链", size=23, bold=True)
    label(180, 583, f"需求 −{demand_parameters['shock'] * 100:g}%、销售份额 {demand_parameters['sales_share'] * 100:g}%", size=20, color="secondary")
    label(78, 618, f"收入下降 {demand_result['revenue_loss_yi']:g} 亿元 · 贡献率未知，EBIT 损失未量化", size=21)
    label(78, 700, "节点风险值用于链内比较，不是违约概率。", size=19, color="muted")

    panel(832, 192, 720, 615, "side")
    label(860, 222, "四个定量案例", size=28, bold=True)
    label(861, 265, "可手算核对的小案例", size=20, color="secondary")
    for index, (title, value, notes) in enumerate(snapshot["cases"]):
        x = 860 + (index % 2) * 350
        y = 310 + (index // 2) * 240
        panel(x, y, 320, 205, "paper", radius=18)
        label(x + 26, y + 20, title, size=21, color="secondary")
        label(x + 24, y + 62, value, size=40, bold=True)
        for line_index, note in enumerate(notes):
            label(x + 26, y + 124 + line_index * 28, note, size=19, color="secondary")

    label(56, 845, "输出：JSON · Markdown · HTML · SVG×2 · Word · Excel，附输入与源码指纹清单", size=20, color="secondary")
    label(1544, 845, "合成数据，不代表真实授信建议", size=19, color="muted", ha="right")
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
            "Title": "合成案例：一次运行的实际输出 — README 案例图",
            "Description": f"Source: examples/results/analysis.json; SHA256 (LF normalized): {source_hash}; synthetic demo case",
        })
        plt.close(figure)
    if source_digest(SOURCE.read_bytes()) != source_hash:
        raise RuntimeError("输入数据在绘图过程中发生变化")
    print(json.dumps({
        "output": str(output), "size": [WIDTH, HEIGHT], "source_sha256_lf": source_hash,
        "nodes": [name for name, _, _ in snapshot["nodes"]],
        "cases": [title for title, _, _ in snapshot["cases"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
