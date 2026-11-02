# Deskbot 模型评测与推理优化汇报图

四张独立 16:9 图片，统一为米白底与深青色。PNG 为 2880 × 1620，SVG 为可缩放矢量版本。没有修改现有网页。

1. `01-overview`：ASR、TTS、本地 Qwen 三类成果总览，适合主汇报页。
2. `02-asr-all-scenarios`：五种语言推理耗时与 RTF；GPU 加载和资源；CPU 早期结果。
3. `03-tts-all-scenarios`：五类文本首音频、总生成时间、RTF、输出音频时长，以及本地 GPU/CPU 资源记录。
4. `04-qwen-throughput`：MTP3/4/5 扫描、最终预热后复测、中文/代码/规则序列场景，以及采用与未采用的配置。

`deskbot-benchmark-figures.zip` 包含全部图、`benchmark-data.json` 与绘图脚本。

## 口径与依据

- ASR/TTS 数据来自历史聊天《复刻 ASR 和 TTS 模型对比测试》（ID `01a082e5-7ba8-75b0-9232-130f86cbcd8c`）保存的结果 JSON 读取输出及 CPU 阶段总结。原始实验 JSON/CSV 本次未在当前目录中找到。
- ASR/TTS 正式批次每项重复 5 次，预热不计；图示为各样本中位数。CPU 汇总中的范围是不同样本中位数的范围，不是置信区间。
- NInfer 数据来自历史聊天 `01a0da00-334d-7b92-a482-539528237576` 的优化进度与最终记录，当前 `/home/yyy/.local/bin/ninfer-chat-start-text` 确认保留 MTP5 参数。
- MTP 扫描阶段的 MTP5 中位数为 108.62 tok/s；最终预热后复测为 134.42 tok/s，三次范围 132.19–136.06。两轮分别展示，不把不同轮次拼成一个严格控制变量实验。
- 约 4.62 倍由最终规则序列中位数 134.42 / 基线 29.07 得到，仅适用于该历史测试。中文约 51.3 和代码约 84.9 tok/s 为场景观测，没有恢复到匹配的无 MTP 基线，故不计算它们的提速倍数。
- Qwen3-TTS 与本地 `qwen3.8-27b` 是不同模型工作。前者为合成模型，本次适配器整段输出；后者以 NInfer/Bonsai 三元量化制品提供对话服务。
- RSS 是进程级记录，不解释为每个模型的独占内存；GPU 指标明确使用 peak allocated。
- 全部为历史记录，制作图表未调用模型、未修改服务、未重新运行基准。没有补造准确率、自然度评分、并发吞吐、置信区间或缺失样本。

## 复现绘图

使用已有绘图环境：

```bash
/home/yyy/Apps/miniconda3/envs/xiaozhi/bin/python docs/model-benchmark-figures/render_figures.py
```

该脚本只生成图表、整理数据和打包文件，不启动推理服务。
