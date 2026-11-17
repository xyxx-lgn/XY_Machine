"""SDT输出的dx/dy及落笔状态转为有序单线，独立于Qt和机器通信。"""

import math


def tokens_to_strokes(tokens):
    """在抬笔点之后分段，不连接空移；遇EOS结束，拒绝被长度上限截断的结果。

    调用方与SDT官方脚本一样先加入[0,0,1,0,0]起始点。dx/dy累计为坐标，
    全字等比居中到0～1字框，不从生成的PNG反推骨架或笔顺。
    """
    strokes, current = [], []
    x = y = 0.0
    ended = False
    for row in tokens:
        if len(row) != 5 or any(type(v) not in (int, float) or not math.isfinite(v) for v in row):
            raise ValueError("AI轨迹含无效坐标或状态。")
        if any(v not in (0, 1) for v in row[2:]) or sum(row[2:]) != 1:
            raise ValueError("AI轨迹的笔状态不是有效独热编码。")
        if row[4] == 1:
            ended = True
            break
        x, y = x + row[0], y + row[1]
        if not math.isfinite(x + y) or max(abs(x), abs(y)) > 1e8:
            raise ValueError("AI轨迹坐标异常。")
        if not current or current[-1] != (x, y):
            current.append((x, y))
        if row[3] == 1:
            strokes.append(current)
            current = []
    if not ended:
        raise ValueError("AI轨迹没有结束标记，可能已截断；本次不保存。")
    if current:
        strokes.append(current)
    if not strokes:
        raise ValueError("AI未生成有效笔迹。")
    points = [point for path in strokes for point in path]
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    size = max(x1 - x0, y1 - y0)
    if size < 1e-8 or len(strokes) > 128:
        raise ValueError("AI字形为空或笔画数量异常。")
    scale = .84 / size
    dx, dy = .5 - (x0 + x1) * scale / 2, .5 - (y0 + y1) * scale / 2
    result = []
    for stroke in strokes:
        path = [[x * scale + dx, y * scale + dy] for x, y in stroke]
        if len(path) == 1:
            # 真实的单点笔画仍保存为一个很短的落笔段。
            path.append([path[0][0] + .002, path[0][1]])
        result.append(path)
    return result
