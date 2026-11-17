"""使用指定真实录字核对扩字结构；对照字形不等于个人风格已验收。"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from app.personal_learning import component_index, fit_personal_model, generate_personal_glyph
from app.personal_strokes import apply_affine, reference_strokes, resample


def hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "config").rglob("*") if p.is_file()}


def distance(paths, reference):
    a = np.concatenate([resample(path) for path in paths])
    b = np.concatenate([resample(path) for path in reference])
    matrix = np.linalg.norm(a[:, None] - b[None, :], axis=2)
    return float((matrix.min(axis=1).mean() + matrix.min(axis=0).mean()) / 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    args = parser.parse_args()
    before = hashes()
    folder = ROOT / ".runtime/personal-learning/structure-v2"
    folder.mkdir(parents=True, exist_ok=True)
    raw = json.loads(args.samples.read_text(encoding="utf8"))
    previous = json.loads(args.previous.read_text(encoding="utf8"))
    model = fit_personal_model(raw["glyphs"])
    palette = ["#ad3434", "#33794b", "#345ba4", "#94702e", "#863b9f", "#198e98"]
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 21)
    picture = Image.new("RGB", (1160, 940), "white")
    draw = ImageDraw.Draw(picture)
    draw.text((20, 12), "同一份真实录字：核对调、林、展、其的结构变化", font=font, fill="#233344")
    draw.text((20, 48), "颜色仅区分笔画；结构对照不代表已经复刻本人的全部书写习惯。", font=font, fill="#566574")
    generated = {}
    for column, char in enumerate("调林展其"):
        result, details = generate_personal_glyph(char, model)
        generated[char] = {"paths": result, "details": details}
        for row in range(3):
            x, y = 200 + column * 235, 100 + row * 270
            label = ("目标结构示意", "上一版个人扩字", "本次结构修复")[row]
            if column == 0:
                draw.text((15, y + 90), label, font=font, fill="#233344")
            paths = [apply_affine(path, np.asarray(model["matrix"])).tolist() for path in reference_strokes(char)] if row == 0 else previous["glyphs"][char]["variants"][0]["strokes"] if row == 1 else result
            draw.rectangle((x, y, x + 215, y + 215), outline="#dce4ec")
            for index, path in enumerate(paths):
                draw.line([(x + px * 215, y + py * 215) for px, py in path], fill=palette[index % len(palette)], width=3)
            draw.text((x + 50, y + 222), f"{char} · {len(paths)}笔", font=font, fill="#233344")
    image = ROOT / "exports/个人扩字_结构修复对照.png"
    picture.save(image)

    # 留出字从训练和部件库中一起排除，不拿重新生成训练字充当扩字效果证据。
    held_out = []
    old_file = ROOT / ".runtime/personal-learning/verified/output.json"
    old_holdouts = json.loads(old_file.read_text(encoding="utf8")) if old_file.is_file() else {}
    for char in "天地女书中口木月":
        if char not in raw["glyphs"]:
            continue
        train = {key: value for key, value in raw["glyphs"].items() if key != char}
        paths, details = generate_personal_glyph(char, fit_personal_model(train))
        reference = raw["glyphs"][char]["variants"][0]["strokes"]
        record = {"character": char, "excluded_from_training": True, "current_distance": distance(paths, reference)}
        if char in old_holdouts:
            record["previous_distance"] = distance(old_holdouts[char], reference)
        held_out.append(record)
    # 扩字窗口单次上限200种，检查多种结构、笔数及未匹配样本的输出边界。
    characters = random.Random(2026).sample(sorted(set(component_index()) - set(raw["glyphs"])), 200)
    started = time.monotonic()
    for index, char in enumerate(characters):
        paths, details = generate_personal_glyph(char, model)
        points = np.concatenate(paths)
        assert np.isfinite(points).all() and points.min() >= 0 and points.max() <= 1, char
        assert all(np.linalg.norm(np.diff(path, axis=0), axis=1).sum() > 0 for path in paths), char
        if index % 50 == 49:
            print(f"Structure/bounds checked {index + 1}/200", flush=True)
    report = {"algorithm": model["algorithm"], "review_image": str(image), "held_out": held_out, "checked_characters": characters, "batch_seconds": round(time.monotonic() - started, 3), "details": generated, "configuration_unchanged": before == hashes(), "hardware_tested": False}
    assert report["configuration_unchanged"]
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("details", "checked_characters")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
