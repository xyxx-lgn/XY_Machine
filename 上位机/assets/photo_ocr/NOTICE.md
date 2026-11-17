# 照片离线识字组件

- RapidOCR / rapidocr-onnxruntime 1.4.4，RapidAI项目，Apache License 2.0。项目及许可来源：https://github.com/RapidAI/RapidOCR/tree/v1.4.4 。
- 中文检测、方向分类和识别模型来自PaddleOCR项目的PP-OCR系列，经RapidOCR提供ONNX格式；PaddleOCR采用Apache License 2.0，项目：https://github.com/PaddlePaddle/PaddleOCR 。
- ONNX Runtime 1.23.2采用MIT许可，其LICENSE和第三方声明随安装包保留在`.venv/Lib/site-packages/onnxruntime/`。
- `LICENSE-APACHE-2.0.txt`保存Apache 2.0标准许可正文。作者和上游项目归属不因上位机调用而改变。

三个模型通过指定版本的Python安装包获取，不在首次识字时联网下载。`models.json`记录本机所用模型的文件名、长度及SHA256，供复现检查；模型权重没有复制到本应用源码目录。安装依赖见根目录`requirements-photo-ocr.txt`及`准备照片识字.ps1`。

本应用只增加照片去底、候选定位、核对及墨迹轨迹提取逻辑，没有修改RapidOCR/PaddleOCR模型或ONNX Runtime。模型识别结果是待核对的标签，不能保证手写识字、单字裁切或真实笔顺完全正确。
