"""照片字迹专用的连贯单线；保留闭环，不改动图片任务和内置字体的算法。"""

import math
from collections import defaultdict

import cv2
import numpy as np

from app.generation import PlotOptions, centerline_paths, checkpoint, fill_paths, order_paths, path_length, simplify


def edge_tangent(path, at_start=True):
    points = path if at_start else list(reversed(path))
    origin = points[0]
    target = points[-1]
    for point in points[1:]:
        target = point
        if math.dist(origin, point) >= 5:
            break
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length = max(1e-9, math.hypot(dx, dy))
    return dx / length, dy / length


def refine_pen_edge(path, mask, degree):
    """在原墨迹内平滑像素阶梯，并延伸独立端头；固定交点和闭环接点。"""
    points = np.asarray(path, dtype=float)
    if len(points) > 3:
        smooth = points.copy()
        smooth[1:-1] = points[:-2] * .2 + points[1:-1] * .6 + points[2:] * .2
        indices = np.rint(smooth).astype(int)
        valid = mask[indices[:, 1], indices[:, 0]] > 0
        points[valid] = smooth[valid]
    result = [tuple(point) for point in points]
    for at_start in (True, False):
        oriented = result if at_start else result[::-1]
        if degree[path[0] if at_start else path[-1]] != 1:
            continue
        dx, dy = edge_tangent(oriented)
        origin, endpoint = oriented[0], oriented[0]
        # 只沿末端切线在黑色墨迹内延长，不用识别标签补造笔画。
        for distance in np.arange(.5, 9, .5):
            point = (origin[0] - dx * distance, origin[1] - dy * distance)
            x, y = map(round, point)
            if not 0 <= y < mask.shape[0] or not 0 <= x < mask.shape[1] or not mask[y, x]:
                break
            endpoint = point
        if math.dist(origin, endpoint) > .7:
            if at_start:
                result.insert(0, endpoint)
            else:
                result.append(endpoint)
    return result


def continuous_photo_paths(mask, cancel=None, pen_shape=False):
    """把墨迹骨架作为无向边图，每条有效边绘制一次，闭环显式闭合。

    各连通块用奇度端点构造欧拉线路。短于局部笔宽的末梢允许少量原线返回，
    用很短的同墨迹复走换掉一次抬落笔；其他边只走一次，所有有效骨架均保留。
    交叉处优先沿切线方向继续；只连接原来相接的边，绝不跨白纸拉线。
    这能恢复连贯的可绘制路径，但不能从静态照片确定人的真实落笔顺序。
    """
    raw = centerline_paths(mask, 1, 1, PlotOptions(tolerance=0, min_length=1), cancel, preserve_connections=True)
    degree = defaultdict(int)
    for path in raw:
        degree[path[0]] += 1
        degree[path[-1]] += 1
    edges = []
    for path in raw:
        checkpoint(cancel)
        a, b = path[0], path[-1]
        closed = a == b
        refined = refine_pen_edge(path, mask, degree) if pen_shape else path
        points = simplify(refined, .35 if pen_shape else .6, closed)
        # 有连接的自环保留原起点，不能让闭环简化换起点后与其他笔画断开。
        if closed:
            if points[0] != a:
                middle = max(range(1, len(path) - 1), key=lambda i: math.dist(path[i], a))
                points = simplify(path[:middle + 1], .6) + simplify(path[middle:], .6)[1:]
            if points[-1] != points[0]:
                points.append(points[0])
        edges.append((a, b, points))
    radius = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 5)
    for a, b, points in list(edges):
        # 只复走两个奇度端点之间的极短末梢；不删除点/钩，不长距离反复描字。
        if degree[a] % 2 and degree[b] % 2 and (degree[a] == 1 or degree[b] == 1):
            junction = b if degree[a] == 1 else a
            x, y = map(round, junction)
            if path_length(points) <= min(16, float(radius[y, x]) * 2):
                edges.append((a, b, points))
                degree[a] += 1
                degree[b] += 1
    adjacency = defaultdict(list)
    for index, (a, b, _) in enumerate(edges):
        adjacency[a].append(index)
        adjacency[b].append(index)
    unseen = set(adjacency)
    result = []
    while unseen:
        checkpoint(cancel)
        start = min(unseen, key=lambda p: (p[1], p[0]))
        stack, component = [start], set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            for index in adjacency[node]:
                a, b, _ = edges[index]
                stack.append(b if node == a else a)
        unseen.difference_update(component)
        local = {node: list(adjacency[node]) for node in component}
        odd = sorted((node for node in component if len(local[node]) % 2), key=lambda p: (p[1], p[0]))
        # 虚拟端点只用于计算最少线路，最终遇到虚拟边必须抬笔，不能输出为运动。
        virtual = []
        for node in odd:
            index = len(edges)
            edges.append((None, node, None))
            local[node].append(index)
            virtual.append(index)
        if virtual:
            local[None] = virtual
            start = None
        remaining = {index for values in local.values() for index in values}
        vertices, steps, circuit = [start], [], []
        while vertices:
            checkpoint(cancel)
            node = vertices[-1]
            candidates = [index for index in local[node] if index in remaining]
            if candidates:
                incoming = steps[-1] if steps else None
                direction = edge_tangent(incoming, False) if incoming else None

                def score(index):
                    a, _, points = edges[index]
                    if points is None:
                        return (1, 0, index)
                    oriented = points if node == a else list(reversed(points))
                    dx, dy = edge_tangent(oriented)
                    # incoming向量朝回指，点积越小代表继续得越直。
                    return (0, direction[0] * dx + direction[1] * dy if direction else 0, index)

                index = min(candidates, key=score)
                remaining.remove(index)
                a, b, points = edges[index]
                vertices.append(b if node == a else a)
                steps.append(points if points is None or node == a else list(reversed(points)))
            else:
                vertices.pop()
                if steps:
                    circuit.append(steps.pop())
        path = []
        for points in reversed(circuit):
            if points is None:
                if len(path) > 1:
                    result.append(path)
                path = []
            elif path:
                path.extend(points[1:])
            else:
                path = list(points)
        if len(path) > 1:
            result.append(path)
    return order_paths(result, cancel)


def ink_reproduction_paths(mask, pen_width, cancel=None):
    """按指定笔宽生成内缩轮廓和连续填线，保留孔洞，细笔画由单线补足。

    pen_width是样本像素中的实际笔迹宽度。该方式追求墨迹外观，会比单线慢；
    不能用更粗的笔还原小于笔尖的细节，也不声称从照片恢复了真实笔顺。
    """
    checkpoint(cancel)
    radius = cv2.distanceTransform(np.pad((mask > 0).astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    inner = (radius >= max(.75, pen_width / 2)).astype(np.uint8) * 255
    paths = []
    contours, _ = cv2.findContours(inner, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        checkpoint(cancel)
        if len(contour) < 3:
            continue
        points = [tuple(map(float, point)) for point in cv2.approxPolyDP(contour, .4, True)[:, 0]]
        if len(points) > 2:
            paths.append(points + points[:1])
    paths.extend(fill_paths(inner, 1, 1, PlotOptions(spacing=max(1, pen_width * .78), min_length=.6), cancel))
    painted = np.zeros(mask.shape, np.uint8)
    for path in paths:
        cv2.polylines(painted, [np.rint(path).astype(np.int32)], False, 255, max(1, round(pen_width)))
    # 被内缩吃掉的细笔画和笔锋仍按原单线保留，不把独立点/钩过滤掉。
    for path in continuous_photo_paths(mask, cancel, pen_shape=True):
        indices = np.rint(path).astype(int)
        if np.any(painted[indices[:, 1], indices[:, 0]] == 0):
            paths.append(path)
    return order_paths(paths, cancel)
