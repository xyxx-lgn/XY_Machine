"""本地字库扩展：录字校准无需模型，SDT实验使用独立推理环境。

    python -B tools/sdt_adapter.py --request request.json [--probe]
    标准输出为逐行JSON进度。只在全部字符成功后原子保存字库，不控制硬件。
"""

import argparse
import json
import os
from pathlib import Path
import random
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ["TORCH_HOME"] = str(ROOT / ".runtime" / "sdt" / "torch")


def report(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def existing_glyphs(request):
    # 参考目录保留手录轨迹；当前选中字库可再包含前几批已核对的AI字，便于累积扩展。
    paths = [Path(request.get("samples", "")) / "recorded-font.json"]
    if request.get("base_font"):
        paths.append(Path(request["base_font"]))
    glyphs = {}
    for path in paths:
        if not path.is_file():
            continue
        if path.stat().st_size > 64_000_000:
            raise ValueError("已有字库超过64MB。")
        source = json.loads(path.read_text(encoding="utf-8"))
        if source.get("schema") != 1 or not isinstance(source.get("glyphs"), dict):
            raise ValueError("已有字库格式无效：" + path.name)
        glyphs.update(source["glyphs"])
    return glyphs


def read_inputs(request):
    import numpy as np
    from PIL import Image
    import pickle

    repo, checkpoint, content, folder = [Path(request.get(key, "")) for key in ("repo", "checkpoint", "content", "samples")]
    if not (repo / "models" / "model.py").is_file():
        raise ValueError("未找到SDT源码models/model.py，请选择官方SDT目录。")
    if not checkpoint.is_file():
        raise ValueError("未找到SDT中文预训练模型权重。")
    if not content.is_file():
        raise ValueError("未找到官方Chinese_content.pkl内容字形数据。")
    manifest = folder / "samples.json"
    if manifest.is_file():
        entries = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError("样本清单应含1～1000张单字参考图。")
        paths = []
        for entry in entries:
            name = entry["file"]
            if not isinstance(name, str) or Path(name).name != name:
                raise ValueError("样本清单的文件名无效。")
            paths.append(folder / name)
    else:
        paths = sorted(folder.glob("*"))
    images, seen = [], set()
    for path in paths:
        if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
            identity = str(path.resolve()).casefold()
            if identity in seen:
                raise ValueError("参考图清单重复列出同一文件：" + path.name)
            seen.add(identity)
            with Image.open(path) as image:
                data = np.asarray(image.convert("L").resize((64, 64), Image.Resampling.BILINEAR), dtype=np.float32) / 255
            if np.min(data) > .9:
                raise ValueError("样本近乎空白，请先检查：" + path.name)
            images.append(data)
            if len(images) > 1000:
                raise ValueError("参考图超过1000张，请选择一组少量、清晰且风格一致的单字样本。")
    if len(images) < 15:
        raise ValueError(f"至少需要15张单字白底黑字参考图，目前{len(images)}张。")
    with content.open("rb") as stream:
        content_data = pickle.load(stream)
    if not isinstance(content_data, dict):
        raise ValueError("内容字形数据不是官方字符字典。")
    characters = list(dict.fromkeys(char for char in request.get("characters", "") if not char.isspace()))
    if not characters or len(characters) > 200:
        raise ValueError("每次AI试样请输入1～200种中文字符。")
    recorded = existing_glyphs(request)
    missing = [char for char in characters if char not in content_data and char not in recorded]
    if missing:
        raise ValueError("SDT内容字形数据缺字：" + "".join(missing[:30]))
    for char in characters:
        if char in recorded:
            continue
        data = np.asarray(content_data[char])
        if data.shape != (64, 64) or not np.isfinite(data).all() or data.min() < 0 or data.max() > 255:
            raise ValueError("官方内容字形应为64×64的0～255灰度图：" + char)
    return repo, checkpoint, content_data, images, characters


def run_recorded(request, probe=False):
    from app.personal_strokes import calibrated_glyph, fit_recorded_style, reference_strokes

    samples = Path(request.get("samples", "")) / "recorded-font.json"
    if not samples.is_file():
        raise ValueError("清晰分笔方式需要原始录字：请选择含recorded-font.json的参考目录；只有单字照片时可使用SDT实验方式。")
    source = json.loads(samples.read_text(encoding="utf-8"))
    if source.get("schema") != 1 or not isinstance(source.get("glyphs"), dict) or len(source["glyphs"]) < 15:
        raise ValueError("请提供至少15种真实录字的recorded-font.json；不能用生成字代替真实样本。")
    if str(source.get("source", "")).startswith(("SDT本地模型生成", "个人轨迹校准", "个人笔迹学习", "OLHWG离线轨迹模型生成", "照片中线导入")):
        raise ValueError("当前参考目录包含生成字，请选择最初真实录字导出的参考目录。")
    characters = list(dict.fromkeys(char for char in request.get("characters", "") if not char.isspace()))
    if not characters or len(characters) > 200:
        raise ValueError("每次试样请输入1～200种中文字符。")
    glyphs = existing_glyphs(request)
    missing = [char for char in characters if char not in glyphs and reference_strokes(char) is None]
    if missing:
        raise ValueError("基础笔顺库缺字：" + "".join(missing[:30]))
    report(stage="loading", message="正在用真实录字校准字框、倾斜及同类笔画曲度；新字保留基础分笔和笔顺。")
    style = fit_recorded_style(source["glyphs"])
    report(stage="ready", message=f"{len(source['glyphs'])}个真实录字中，{len(style['characters'])}字、{style['paired_strokes']}笔参与校准；其余录字仍原样保留。此方式不训练AI模型，不保证完整复制个人特殊写法。")
    if probe:
        report(stage="checked", message="原始录字、可配对笔画及目标字结构检查通过；无需CUDA或联网。")
        return
    seed, variants = int(request.get("seed", 2026)), int(request.get("variants", 1))
    if not 1 <= seed <= 2147483647 or not 1 <= variants <= 4:
        raise ValueError("样式编号或版本数无效。")
    package = {"schema": 1, "name": request.get("name", "个人笔迹清晰分笔试样"), "source": "个人轨迹校准，基础楷体分笔与笔顺；迁移录字比例、倾斜及同类笔画曲度，并非AI完整复原个人字迹", "glyphs": glyphs}
    generated, preserved, started = [], [], time.monotonic()
    for index, char in enumerate(characters):
        if char in glyphs:
            preserved.append(char)
        else:
            versions = [{"strokes": calibrated_glyph(char, style, random.Random(f"{seed}:{char}:{version}") if version else None)} for version in range(variants)]
            glyphs[char] = {"advance": 1., "variants": versions}
            generated.append(char)
        report(stage="progress", done=index + 1, total=len(characters), character=char)
    output = Path(request["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(package, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
    summary = {"method": "recorded", "generated": generated, "preserved_requested": preserved, "total_glyphs": len(glyphs), "recorded_characters": len(source["glyphs"]), "calibration_characters": style["characters"], "unmatched_recorded_characters": style["skipped"], "paired_strokes": style["paired_strokes"], "seed": seed, "variants": variants, "generation_seconds": round(time.monotonic() - started, 3), "stroke_counts": {char: len(glyphs[char]["variants"][0]["strokes"]) for char in generated}}
    output.with_name("summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report(stage="complete", output=str(output), message=f"已生成{len(generated)}个清晰分笔字，保留{len(preserved)}个请求中的已有字；新字库共{len(glyphs)}字。请核对个人风格和纸面效果后再使用。")


def run_personal(request, probe=False):
    from app.personal_learning import fit_personal_model, generate_personal_glyph, model_fingerprint, reference_strokes

    original = Path(request.get("samples", "")) / "recorded-font.json"
    if not original.is_file() or original.stat().st_size > 64_000_000:
        raise ValueError("个人笔迹学习需要含recorded-font.json的真实录字目录，请从原始手写字库打开扩展。")
    source = json.loads(original.read_text(encoding="utf-8"))
    if source.get("schema") != 1 or not isinstance(source.get("glyphs"), dict):
        raise ValueError("原始录字格式无效。")
    if str(source.get("source", "")).startswith(("SDT本地模型生成", "个人轨迹校准", "个人笔迹学习", "OLHWG离线轨迹模型生成", "照片中线导入")):
        raise ValueError("学习样本含生成字，请选择最初真实录字的参考目录。")
    characters = list(dict.fromkeys(char for char in request.get("characters", "") if not char.isspace()))
    if not characters or len(characters) > 200:
        raise ValueError("每次扩展请输入1～200种字符。")
    glyphs = existing_glyphs(request)
    missing = [char for char in characters if char not in source["glyphs"] and reference_strokes(char) is None]
    if missing:
        raise ValueError("结构字库缺字：" + "".join(missing[:30]))
    seed, variants = int(request.get("seed", 2026)), int(request.get("variants", 1))
    if not 1 <= seed <= 2147483647 or not 1 <= variants <= 4:
        raise ValueError("样式编号或版本数无效。")
    started = time.monotonic()
    report(stage="learning", message="正在学习真实XY笔形，按目标字逐笔适配部件，并约束整字接点；不上传数据，不加载SDT。")
    model = fit_personal_model(source["glyphs"])
    fingerprint = model_fingerprint(source["glyphs"])
    model["recordings_sha256"] = fingerprint
    report(stage="ready", message=f"学习完成：{len(model['training_characters'])}字、{model['training_strokes']}笔，{len(model['components'])}种个人部件。已录字原样保留，未录字由个人部件与学到的笔形合成；结构信息限定字的组成，不等于已学会全部特殊写法。")
    details, generated, preserved, warnings, regenerated = {}, [], [], [], []
    for index, char in enumerate(characters):
        if char in source["glyphs"]:
            preserved.append(char)
            message = f"{index + 1}/{len(characters)} {char}：保留真实录字。"
        else:
            if char in glyphs:
                regenerated.append(char)
            versions = []
            for version in range(variants):
                choice = 0 if version == 0 else random.Random(f"{seed}:{char}:{version}").randrange(1, 1000000)
                strokes, trace = generate_personal_glyph(char, model, choice)
                versions.append({"strokes": strokes})
                if version == 0:
                    details[char] = trace
            if details[char]["minimum_similarity"] < .04:
                warnings.append(char)
            if not probe:
                glyphs[char] = {"advance": 1., "variants": versions}
            generated.append(char)
            trace = details[char]
            reused = "、".join(dict.fromkeys(item["component"] + "←" + item["recorded_character"] for item in trace["components"]))
            message = f"{index + 1}/{len(characters)} {char}：" + (f"复用{reused}；" if reused else "") + f"{trace['learned_strokes']}笔由个人笔形模型推算。"
        report(stage="progress", done=index + 1, total=len(characters), character=char, message=message)
    if warnings:
        report(stage="warning", message="这些字有参考不足的笔形：" + "".join(warnings) + "。结果只作候选，请先回放核对；补录这些字或对应折钩/偏旁后重新学习，不能保证当前就像本人。")
    if probe:
        report(stage="checked", message="原始录字、个人笔形学习与目标结构检查通过；参考不足字已在上方列出。无需模型权重、CUDA或联网。")
        return
    output = Path(request["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    package = {"schema": 1, "name": request.get("name", "个人笔迹学习扩展"), "source": "个人笔迹学习v2，真实笔形逐笔上下文适配与整字接点约束；几何迁移候选，非已验收的个人AI字体", "glyphs": glyphs}
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(package, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
    output.with_name("personal-model.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    summary = {"method": "personal", "algorithm": model["algorithm"], "generated": generated, "regenerated_existing": regenerated, "preserved_requested": preserved, "total_glyphs": len(glyphs), "recorded_characters": len(source["glyphs"]), "training_characters": model["training_characters"], "training_strokes": model["training_strokes"], "personal_components": len(model["components"]), "recordings_sha256": fingerprint, "reference_limited": warnings, "details": details, "seed": seed, "variants": variants, "learning_and_generation_seconds": round(time.monotonic() - started, 3)}
    output.with_name("summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report(stage="complete", output=str(output), message=f"已学习并生成{len(generated)}个未录字；新库共{len(glyphs)}字，原始轨迹保留。参考不足字{len(warnings)}个，请先回放和短句试写；本页不会发送任务。")


def run_trajectory(request, probe=False):
    """用OLHWG预训练轨迹模型按真实录字风格生成未录字符。"""
    import copy
    import numpy as np
    import torch

    from app.trajectory_learning import (
        MODEL_ROOT, content_labels, decode_trajectory, load_resources,
        read_recordings, recorded_extent, reference_batch, structure_distance,
    )
    from tools.trajectory_model import DiffusionModel, StyleEncoder

    model_root = Path(request.get("model_root") or MODEL_ROOT)
    content, manifest = load_resources(model_root)
    recordings = read_recordings(request.get("samples", ""))
    extent = recorded_extent(recordings["glyphs"])
    requested = list(dict.fromkeys(char for char in request.get("characters", "") if not char.isspace()))
    if not requested or len(requested) > 200:
        raise ValueError("每次轨迹扩字请输入1～200种字符。")
    seed, variants = int(request.get("seed", 2026)), int(request.get("variants", 1))
    if not 1 <= seed <= 2147483647 or not 1 <= variants <= 4:
        raise ValueError("AI样式编号或版本数无效。")

    # 真实录字优先于旧字库，避免旧的生成字覆盖原始个人笔迹。
    glyphs = existing_glyphs(request)
    glyphs.update(copy.deepcopy(recordings["glyphs"]))
    previous_characters = set(glyphs)
    targets = [char for char in requested if char not in recordings["glyphs"]]
    unsupported = [char for char in targets if char not in content["lengths"] or char not in content["dictionary"]]
    if unsupported:
        raise ValueError("轨迹模型暂不支持这些字符（当前覆盖约{}种）：{}。请先换字或使用原有基础字库方式。".format(len(content["lengths"]), "".join(unsupported[:40])))

    if probe and not targets:
        report(stage="checked", message="真实录字与已有字库检查通过；本次没有需要模型生成的新字。")
        return

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise ValueError("OLHWG轨迹扩字需要CUDA版PyTorch；当前未检测到NVIDIA CUDA。模型不会退回几何拼接。")
    torch.set_num_threads(min(4, torch.get_num_threads()))
    # 扩散采样包含1000次卷积；允许CuDNN选择最快算法，种子仍控制主要随机源。
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False
    report(stage="loading", message="正在加载OLHWG离线轨迹模型与个人参考轨迹…")
    model = DiffusionModel().to(device).eval()
    encoder = StyleEncoder().to(device).eval()
    try:
        model.load_state_dict(torch.load(model_root / "unetddpm450000.pth", map_location="cpu", weights_only=True), strict=True)
        encoder.load_state_dict(torch.load(model_root / "style_encoder450000.pth", map_location="cpu", weights_only=True), strict=True)
    except (OSError, RuntimeError, TypeError) as error:
        raise ValueError("OLHWG模型权重与网络版本不匹配：" + str(error)) from None
    report(stage="ready", message=f"已载入离线轨迹模型：CUDA；个人真实参考{len(recordings['glyphs'])}字，模型字典{len(content['lengths'])}字。")
    if probe:
        reference_batch(recordings["glyphs"], content, seed)
        report(stage="checked", message="模型权重、资源校验、真实参考轨迹和目标字符检查通过；未执行推理。生成阶段无需联网。")
        return

    output = Path(request["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    generated = [char for char in targets if char not in previous_characters]
    regenerated = [char for char in targets if char in previous_characters]
    preserved = [char for char in requested if char in recordings["glyphs"]]
    for char in targets:
        glyphs[char] = {"advance": 1., "variants": []}
    diagnostics, started = {}, time.monotonic()
    work = [(char, version) for char in targets for version in range(variants)]
    for batch_index in range(0, len(work), 8):
        batch_work = work[batch_index:batch_index + 8]
        batch_chars = [char for char, version in batch_work]
        labels, durations = content_labels(batch_chars, content)
        ref, selected = reference_batch(recordings["glyphs"], content, seed + batch_index)
        torch.manual_seed(seed + batch_index)
        torch.cuda.manual_seed_all(seed + batch_index)
        with torch.inference_mode():
            features = encoder(torch.tensor(ref, dtype=torch.float32, device=device).permute(0, 2, 1))
            features = [values.repeat(len(batch_chars), 1, 1) for values in features]
            generated_sequences = model.generate(labels, durations, features, progress=lambda done, total: report(stage="model_progress", done=batch_index + len(batch_work) * done / total, total=len(work), message=f"轨迹模型推理 {batch_index + 1}～{min(batch_index + 8, len(work))}/{len(work)}：{done}/{total}步"))
        for (char, version), sequence, duration in zip(batch_work, generated_sequences, durations):
            paths = decode_trajectory(sequence[:duration[0]], duration[0], extent=extent)
            distance = structure_distance(char, paths)
            key = f"{char}:{version + 1}"
            diagnostics[key] = {"strokes": len(paths), "points": sum(len(path) for path in paths), "duration": duration[0], "reference_characters": selected, "canonical_structure_distance": distance}
            glyphs[char]["variants"].append({"strokes": paths})
            report(stage="progress", done=batch_index + len(batch_work), total=len(work), character=char, message=f"{char} 版本{version + 1}：生成{len(paths)}笔，保留模型自然连笔结构。")
        del features, generated_sequences

    package = {"schema": 1, "name": request.get("name", "个人笔迹离线AI轨迹扩展"), "source": "OLHWG离线轨迹模型生成；以真实录字作为风格参考，生成XY增量与抬落笔轨迹；不是联网服务", "glyphs": glyphs}
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(package, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
    output.with_name("diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False), encoding="utf-8")
    summary = {"method": "trajectory", "algorithm": manifest.get("algorithm", "OLHWG-DDPM-450000"), "generated": generated, "regenerated_existing": regenerated, "preserved_requested": preserved, "unsupported": unsupported, "total_glyphs": len(glyphs), "recorded_characters": len(recordings["glyphs"]), "model_characters": len(content["lengths"]), "variants": variants, "seed": seed, "reference_count": 15, "output_extent": extent, "inference_seconds": round(time.monotonic() - started, 3), "diagnostics": diagnostics}
    output.with_name("summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report(stage="complete", output=str(output), message=f"已生成{len(generated) + len(regenerated)}个未录字符；保留{len(preserved)}个真实录字。结果使用真实XY轨迹模型生成，请先回放和小范围试写，不会自动发送任务。")


def run(request, probe=False):
    method = request.get("method", "sdt")
    if method == "personal":
        return run_personal(request, probe)
    if method == "recorded":
        return run_recorded(request, probe)
    if method == "trajectory":
        return run_trajectory(request, probe)
    if method != "sdt":
        raise ValueError("扩字方式无效。")
    # 先检查依赖，避免只有显卡便被误报为AI已可用。
    import numpy as np
    import torch
    import torchvision.models as vision_models
    import einops
    from app.sdt_paths import tokens_to_strokes
    from app.personal_strokes import check_generated_strokes

    if not torch.cuda.is_available():
        raise ValueError("此SDT适配器需要支持CUDA的PyTorch及NVIDIA显卡；当前环境不可用。")
    repo, checkpoint, content, images, characters = read_inputs(request)
    sys.path.insert(0, str(repo))
    from models.model import SDT_Generator
    report(stage="loading", message="正在加载SDT中文模型并验证权重…")
    original = vision_models.resnet18

    def without_download(*args, **kwargs):
        # 完整权重马上覆盖全部网络参数，无需再次联网下载ImageNet权重。
        kwargs.pop("pretrained", None)
        kwargs["weights"] = None
        return original(*args, **kwargs)

    vision_models.resnet18 = without_download
    try:
        model = SDT_Generator(num_encoder_layers=2, num_head_layers=1, wri_dec_layers=2, gly_dec_layers=2).cuda()
    finally:
        vision_models.resnet18 = original
    state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval()
    del state
    report(stage="ready", message=f"模型已载入：{torch.cuda.get_device_name(0)}；{len(content)}种内容字形，{len(images)}张本地参考图。")
    if probe:
        report(stage="checked", message=f"依赖、CUDA、模型参数、{len(images)}张样本和内容字形检查通过；尚未执行推理。", gpu=torch.cuda.get_device_name(0))
        return
    seed = int(request.get("seed", 1))
    variants = int(request.get("variants", 1))
    if not 1 <= seed <= 2147483647 or not 1 <= variants <= 4:
        raise ValueError("AI样式编号或版本数无效。")
    package = {"schema": 1, "name": request.get("name", "个人笔画AI试样"), "source": "SDT本地模型生成，需人工核对字形与笔顺；" + checkpoint.name, "glyphs": existing_glyphs(request)}
    rng = random.Random(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    output = Path(request["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    generated, preserved, started = [], [], time.monotonic()
    diagnostics = {}
    with torch.inference_mode():
        for index, char in enumerate(characters):
            if char not in package["glyphs"]:
                versions = []
                char_img = torch.tensor(np.asarray(content[char], dtype=np.float32) / 255).unsqueeze(0).unsqueeze(0).cuda()
                for version in range(variants):
                    chosen = rng.sample(images, 15)
                    style_imgs = torch.tensor(np.asarray(chosen)).unsqueeze(1).unsqueeze(0).cuda()
                    preds = model.inference(style_imgs, char_img, 120)
                    tokens = [[0, 0, 1, 0, 0]] + preds[0].detach().cpu().tolist()
                    strokes = tokens_to_strokes(tokens)
                    diagnostics[f"{char}:{version + 1}"] = {"strokes": len(strokes), "tokens": tokens}
                    output.with_name("diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False), encoding="utf-8")
                    check_generated_strokes(char, strokes)
                    versions.append({"strokes": strokes})
                package["glyphs"][char] = {"advance": 1.0, "variants": versions}
                generated.append(char)
            else:
                preserved.append(char)
            report(stage="progress", done=index + 1, total=len(characters), character=char)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(package, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
    summary = {"generated": generated, "preserved_requested": preserved, "total_glyphs": len(package["glyphs"]), "references": len(images), "seed": seed, "variants": variants, "gpu": torch.cuda.get_device_name(0), "inference_seconds": round(time.monotonic() - started, 3)}
    output.with_name("summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report(stage="complete", output=str(output), message=f"已扩展{len(generated)}字，所请求字中{len(preserved)}字保留已有轨迹；新字库共{len(package['glyphs'])}字。请先核对回放和小样，不会自动发送。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        run(request, args.probe)
        return 0
    except Exception as error:
        report(stage="failed", message=type(error).__name__ + ": " + str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
