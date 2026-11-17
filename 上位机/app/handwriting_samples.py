"""个人字库采样表与AI参考图导出，均不连接机器。"""

import json
from pathlib import Path
from xml.sax.saxutils import escape

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter, QPen

from app.generation import GenerationError
from app.stroke_fonts import package_data, validate_package


# 前80格覆盖常见结构，后20格重复常用字，用来采集同字的不同写法。
SAMPLE_PLAN = tuple("永天地人大小上下左右中日月山水火木金土田口国目心手足言女子学文书写生活年春风雨花草竹石门车马鸟鱼龙云一二三四五六七八九十来去有无我是的了和在不这那好明林休想问围回家安")[:80] + tuple("的一是在不了有和人这中大为上个国我以要他")


def gb2312_characters():
    """按GB2312区位顺序生成可打印的6763个简体及常用汉字。"""
    result = []
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                result.append(bytes((high, low)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return tuple(result)


_COMMON_EXTRA = tuple("阿啊哎爱安按白百班半包保报北被本比边便表别病并不才菜参草层差长常场车成吃出初除处传春次从村错答打达带待代单但当到道得的等地第点电调定东冬懂动都读度对多而儿二发法反方放非分风服福父复该改干刚高告哥歌各给根更工公功共古故关光广规国果过海还孩汉好号合黑很红后候花画话化欢环回会婚活火或机鸡几己记家价间见件建将讲交教接节姐解界今金进近经九久就旧举具决军开看可科空口苦快块况拉来老乐了里力立连两量六路乱落吗买卖满慢忙没每美妹门米面明命母目拿那南难能你年娘鸟您牛女怕跑朋友片品平破期起气千前钱青轻清请秋全然人认任日容如若三色山少谁深生声时识十使市事世是手首受书熟数水睡说思四送素他太谈汤堂天条听同头图土外完玩晚万王往网望为位文我无五午西希喜系下先现相想小写些新心信行姓休修学雪呀言眼阳要也夜一已以义意因应用有又鱼雨语遇元园远月越云再在早造怎增站张这真正知直中重州住主专转装准子自总走足作座做")
SAMPLE_PLANS = {
    "100格基础": SAMPLE_PLAN,
    "300字常用": tuple(dict.fromkeys(SAMPLE_PLAN + _COMMON_EXTRA))[:300],
    "1000字扩展": tuple(dict.fromkeys(SAMPLE_PLAN + _COMMON_EXTRA + tuple("春夏秋冬东西南北上下左右天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏") + gb2312_characters()))[:1000],
    "GB2312一级3755字": gb2312_characters()[:3755],
    "GB2312 6763字": gb2312_characters(),
}


def sample_plan(name="100格基础"):
    return SAMPLE_PLANS.get(name, SAMPLE_PLAN)


def export_sample_sheet(path, plan=None, title="个人手写采样"):
    """每100格输出一张A4；大计划另建分页目录，附每页字符和格子坐标。

    参考字放在方格上方，格内留白，避免拍照时把底稿误提取为用户笔迹。
    照片导入的“本软件采样表”预设与这里的17毫米格、19/25毫米间距一致。
    """
    plan = tuple(plan or SAMPLE_PLAN)
    target = Path(path)
    if len(plan) > 100:
        folder = target.with_name(target.stem + "_分页")
        if folder.exists():
            raise GenerationError("分页目录已存在，请换一个导出名称：" + str(folder))
        folder.mkdir(parents=True)
        pages = []
        for start in range(0, len(plan), 100):
            name = f"第{start // 100 + 1:03d}页.svg"
            export_sample_sheet(folder / name, plan[start:start + 100], f"{title} · 第{start // 100 + 1}页")
            pages.append({"file": name, "characters": "".join(plan[start:start + 100]), "first": start + 1})
        (folder / "录字清单.json").write_text(json.dumps({"schema": 1, "pages": pages}, ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / "使用说明.txt").write_text("每张SVG都是A4，按100%实际大小打印。每格只写上方提示的字。\n拍摄时正对纸面；照片录字选择“本软件采样表”，框选第一格左上角到最后一格右下角，勿选标题。\n不足100字的最后一页请把照片行数改为实际行数；字符可从录字清单.json复制。", encoding="utf-8")
        return folder
    columns, cell, gap = 10, 17, 2
    height = 297
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="{height}mm" viewBox="0 0 210 {height}">', f'<rect width="210" height="{height}" fill="white"/>', '<g font-family="Microsoft YaHei, sans-serif" fill="#333">', f'<text x="10" y="10" font-size="4">{escape(title)} · {len(plan)}格（按100%实际大小打印）</text>', '<text x="10" y="16" font-size="2.6">每格只写上方指定的一个字；黑色笔书写，不描画格线。照片导入选择本软件采样表。</text>']
    for index in range(((len(plan) + columns - 1) // columns) * columns):
        char = plan[index] if index < len(plan) else ""
        x, y = 10 + index % columns * 19, 25 + index // columns * 25
        if char:
            lines.append(f'<text x="{x}" y="{y - 2}" font-size="2.6">{index + 1:03d} <tspan font-family="KaiTi, STKaiti, serif" font-size="4">{escape(char)}</tspan></text>')
        lines.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="none" stroke="#aab5c0" stroke-width="0.15"/>')
        lines.append(f'<path d="M{x + 8.5},{y}v17 M{x},{y + 8.5}h17" fill="none" stroke="#ffcccc" stroke-width="0.1" stroke-dasharray="1 1"/>')
    lines.extend([f'<text x="10" y="{height - 10:g}" font-size="2.6">建议保持每格大小一致；{len(plan)}格是采样计划，照片样本仍需核对，不能保证AI重建全部个人字形。</text>', '</g></svg>'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def export_recorded_samples(package, folder):
    """将真实录制轨迹渲染成单字白底PNG，同时保留字库及字符清单。

    每个版本独立导出，不从轮廓或扫描图猜测笔顺；清单可用于人工核查AI输入。
    """
    package = validate_package(json.loads(json.dumps(package_data(package), ensure_ascii=False)))
    if not package.ordered:
        raise GenerationError("照片中线没有真实笔顺，不能导出为真实手录轨迹。请在照片录字窗口导出原始单字照片样本。")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for char, glyph in package.glyphs.items():
        for index, version in enumerate(glyph["variants"]):
            image = QImage(256, 256, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(Qt.GlobalColor.black, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            scale = 216 / max(1, glyph["advance"])
            dx, dy = (256 - glyph["advance"] * scale) / 2, (256 - scale) / 2
            for stroke in version["strokes"]:
                points = [QPointF(dx + x * scale, dy + y * scale) for x, y in stroke]
                for a, b in zip(points, points[1:]):
                    painter.drawLine(a, b)
            painter.end()
            name = f"u{ord(char):06x}_v{index + 1:02d}.png"
            if not image.save(str(folder / name)):
                raise GenerationError("保存参考图失败：" + name)
            records.append({"character": char, "variant": index + 1, "file": name})
    (folder / "samples.json").write_text(json.dumps({"schema": 1, "font": package.name, "count": len(records), "samples": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "recorded-font.json").write_text(json.dumps(package_data(package), ensure_ascii=False), encoding="utf-8")
    return len(records)
