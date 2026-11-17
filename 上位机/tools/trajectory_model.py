"""OLHWG兼容的推理网络；改写自OLHWG-PageSynth的MIT实现。

来源与许可见assets/strokes/OLHWG-PAGESYNTH-MIT.txt；仅用于独立扩字子进程，
主界面不导入PyTorch。参数名称保持兼容公开权重，不包含在线下载或训练操作。
"""

import math

import torch
from torch import nn
from torch.nn import functional as F


class Conv1d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, dilation=1, stride=1):
        super().__init__()
        self.pad = dilation * (kernel_size - 1)
        self.conv = nn.Conv1d(in_channels, out_channels, kernel_size, dilation=dilation, stride=stride)

    def forward(self, x):
        return self.conv(F.pad(x, [0, self.pad]))


class TransConv1d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, dilation=1, stride=1):
        super().__init__()
        self.pad = int(((2 * dilation - 1) * stride - dilation + 1) / 2)
        self.transconv = nn.ConvTranspose1d(in_channels, out_channels, kernel_size, stride=stride, padding=self.pad)

    def forward(self, x):
        return self.transconv(x)


class ResidualUnit(nn.Module):
    def __init__(self, in_channels, out_channels, dilation, kernel_size=3):
        super().__init__()
        self.layers = nn.Sequential(Conv1d(in_channels, out_channels, kernel_size, dilation=dilation), nn.ELU(), nn.Conv1d(in_channels, out_channels, 1))

    def forward(self, x):
        return x + self.layers(x)


class EncoderBlock(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1):
        super().__init__()
        kernel = stride * 2 if stride != 1 else 1
        self.layers = nn.Sequential(
            ResidualUnit(in_channels, in_channels, 1), nn.ELU(),
            ResidualUnit(in_channels, in_channels, 3), nn.ELU(),
            ResidualUnit(in_channels, in_channels, 5), nn.ELU(),
            Conv1d(in_channels, out_channels, kernel, stride=stride)
        )

    def forward(self, x):
        return self.layers(x)


class DecoderBlock(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1):
        super().__init__()
        kernel = stride * 2 if stride != 1 else 1
        self.layers = nn.Sequential(
            TransConv1d(in_channels, out_channels, kernel, stride=stride), nn.ELU(),
            ResidualUnit(out_channels, out_channels, 1), nn.ELU(),
            ResidualUnit(out_channels, out_channels, 3), nn.ELU(),
            ResidualUnit(out_channels, out_channels, 5)
        )

    def forward(self, x):
        return self.layers(x)


class CrossAttention(nn.Module):
    def __init__(self, dim1, dim2):
        super().__init__()
        self.q_func, self.k_func, self.v_func = nn.Linear(dim1, 256), nn.Linear(dim2, 256), nn.Linear(dim2, 256)
        self.out_func = nn.Linear(256, dim1)

    def forward(self, x, features):
        x, features = x.permute(0, 2, 1), features.permute(0, 2, 1)
        q, k, v = self.q_func(x), self.k_func(features), self.v_func(features)
        weights = torch.softmax(torch.bmm(q, k.transpose(1, 2)) / 16, dim=-1)
        return (self.out_func(torch.bmm(weights, v)) + x).permute(0, 2, 1)


class StyleEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = Conv1d(3, 128, 3)
        self.en_block1 = nn.Sequential(EncoderBlock(128, 256, 2))
        self.en_block2 = nn.Sequential(EncoderBlock(256, 512, 2))
        self.en_block3 = nn.Sequential(EncoderBlock(512, 1024, 2))
        self.elu = nn.ELU()
        self.func_c = nn.Linear(1024, 1020)

    def forward(self, x):
        x = self.linear(x)
        en1 = self.en_block1(self.elu(x))
        en2 = self.en_block2(self.elu(en1))
        en3 = self.en_block3(self.elu(en2))
        return x, en1, en2, en3


class Unet(nn.Module):
    def __init__(self):
        super().__init__()
        self.en_block1 = EncoderBlock(276, 128)
        self.en_block2 = EncoderBlock(256, 256)
        self.en_block3 = EncoderBlock(512, 512)
        self.en_block4 = EncoderBlock(1024, 1024)
        self.downblock1 = EncoderBlock(128, 256, 2)
        self.downblock2 = EncoderBlock(256, 512, 2)
        self.downblock3 = EncoderBlock(512, 1024, 2)
        self.de_block1 = EncoderBlock(512, 512)
        self.de_block2 = EncoderBlock(256, 256)
        self.de_block3 = EncoderBlock(128, 276)
        self.de_block4 = EncoderBlock(276, 3)
        self.upblock1 = DecoderBlock(1024, 512, 2)
        self.upblock2 = DecoderBlock(512, 256, 2)
        self.upblock3 = DecoderBlock(256, 128, 2)
        self.cross_attention1 = CrossAttention(276, 128)
        self.cross_attention2 = CrossAttention(256, 256)
        self.cross_attention3 = CrossAttention(512, 512)
        self.cross_attention4 = CrossAttention(1024, 1024)
        self.elu = nn.ELU()

    def forward(self, x, features):
        en1 = self.elu(self.en_block1(x))
        en2 = self.elu(self.downblock1(en1))
        en3 = self.elu(self.en_block2(en2))
        en4 = self.elu(self.downblock2(en3))
        en5 = self.elu(self.en_block3(en4))
        en6 = self.elu(self.downblock3(en5))
        de1 = self.cross_attention4(self.elu(self.en_block4(en6)), features[3])
        de2 = self.elu(self.upblock1(de1))
        de3 = self.cross_attention3(self.elu(self.de_block1(de2 + en5)), features[2])
        de4 = self.elu(self.upblock2(de3 + en4))
        de5 = self.cross_attention2(self.elu(self.de_block2(de4 + en3)), features[1])
        de6 = self.elu(self.upblock3(de5 + en2))
        de7 = self.cross_attention1(self.elu(self.de_block3(de6 + en1)), features[0])
        return self.de_block4(de7)


class DiffusionModel(nn.Module):
    def __init__(self):
        super().__init__()
        self.denoiser = Unet()
        self.char_embeddings = nn.Embedding(4055, 256)

    @torch.inference_mode()
    def generate(self, labels, durations, features, progress=None):
        """保持训练时的120点、1000步和分段位置编码，逐步生成XY增量与笔状态。

        内容及位置编码在步间不变，预计算后复用；回调只报告进度，不改采样。
        初始CPU随机数及后续GPU噪声沿用公开实现，编号控制主要随机源。
        """
        device = self.char_embeddings.weight.device
        batch, length = len(labels), len(labels[0])
        tags = torch.tensor(labels, device=device)
        content = self.char_embeddings(tags).permute(0, 2, 1)
        position = torch.stack([torch.cat([torch.linspace(0, 1, n) for n in parts]) for parts in durations]).unsqueeze(1).to(device)
        decay = [.5 * (1 + torch.cos(torch.tensor(math.pi * step / 1000))) for step in range(1000)]
        beta = torch.stack([.02 * (1 - value) + .0001 * value for value in decay]).to(device)
        alpha = 1 - beta
        cumulative = torch.cumprod(alpha, dim=0)
        frequencies = torch.exp(torch.arange(8, device=device) * (-math.log(10000) / 7))
        x = torch.randn(batch, 3, length).to(device)
        for t in range(999, -1, -1):
            phase = torch.tensor([t], device=device)[:, None] * frequencies[None, :]
            time = torch.cat([phase.sin(), phase.cos()], dim=-1).unsqueeze(2).repeat(batch, 1, length)
            inputs = torch.cat([x, position, content, time], dim=1)
            predicted = self.denoiser(inputs, features)
            x = (x - beta[t] / torch.sqrt(1 - cumulative[t]) * predicted) / torch.sqrt(alpha[t])
            if t > 15:
                sigma = torch.sqrt(.8 * beta[t] * (1 - cumulative[t - 1]) / (1 - cumulative[t]))
                x += sigma * torch.randn_like(x)
            if progress and t % 100 == 0:
                progress(1000 - t, 1000)
        return x.permute(0, 2, 1).cpu().numpy()
