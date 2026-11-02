"""Render the reviewed historical Deskbot benchmark observations. No model calls."""
from pathlib import Path
import json
import zipfile
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent
FONT = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
fm.fontManager.addfont(FONT)
plt.rcParams.update({
    'font.family': fm.FontProperties(fname=FONT).get_name(),
    'font.size': 12, 'axes.unicode_minus': False,
    'svg.fonttype': 'none', 'figure.dpi': 180, 'savefig.dpi': 180,
})
BG='#F5F4EC'; INK='#193537'; MUTED='#647472'; TEAL='#246D68'
PALE='#9AACA6'; GRID='#DCE2D6'; GOLD='#B58B3C'; LIME='#D1ED91'
DATA={
 'provenance':{
  'asr_tts':'Historical thread 01a082e5-7ba8-75b0-9232-130f86cbcd8c: saved result JSON readout and handoff; original JSON/CSV not recovered in current directories.',
  'llm':'Historical thread 01a0da00-334d-7b92-a482-539528237576: benchmark progress and final result; current ninfer-chat-start-text confirms MTP5 configuration.',
  'status':'Historical observations, not a new benchmark. CPU and GPU runs are separate batches.',
 },
 'asr_gpu':{
  'languages':['中文','英文','日文','韩文','粤语'],
  'models':['SenseVoiceSmall','Fun-ASR-Nano-2512'],
  'latency_median_ms':[[27.325,34.469,34.660,25.540,25.297],[183.229,292.506,366.971,379.473,203.132]],
  'rtf_median':[[.004861,.004801,.004794,.005491,.004858],[.032594,.040738,.050759,.081581,.039006]],
  'load_s':[5.98992459800138,21.464337255998544],
  'peak_gpu_allocated_mib':[939.1611328125,3394.0048828125],
  'loaded_process_rss_mib':[1140.63671875,3310.921875],
  'repeat':5,'warmup_excluded':True,
 },
 'asr_cpu':{'models':['SenseVoiceSmall','Fun-ASR-Nano-2512'],
  'load_s':[5.82,21.71],'process_rss_gib':[1.87,3.31],
  'rtf_median_range':[[.030,.031],[.153,.357]],
  'note':'Earlier CPU batch summary; per-sample values not recovered. Ranges are sample-median ranges, not confidence intervals.'},
 'tts':{
  'samples':['短句','桌宠对话','技术混合','数字','长文本'],
  'models':['EdgeTTS · 云端流式','Qwen3-TTS 0.6B · GPU整段'],
  'first_audio_median_s':[[1.100474,1.114228,1.129709,1.218230,1.220200],[2.083379,5.147105,6.889711,6.695985,18.594846]],
  'total_median_s':[[1.475119,1.514887,1.563802,1.645798,1.835227],[2.083379,5.147105,6.889711,6.695985,18.594846]],
  'rtf_median':[[.682926,.317187,.238676,.267871,.098036],[.813820,.814415,.844327,1.087011,.821327]],
  'audio_median_s':[[2.16,4.776,6.552,6.144,18.72],[2.56,6.32,8.16,6.16,22.64]],
  'qwen_gpu_load_s':1.508656821999466,'qwen_gpu_peak_allocated_mib':2769.10302734375,
  'qwen_cpu':{'load_s':2.62,'process_rss_gib':4.60,'rtf_range':[2.42,3.08]},
  'repeat':5,'warmup_excluded':True,'audio_files':50,
  'note':'Qwen adapter generates a full waveform: first audio equals total time. Cloud vs local and streaming vs whole-waveform differ.'},
 'llm':{
  'service_model_id':'qwen3.8-27b','artifact':'bonsai2_27b_ternary_v2.ninfer',
  'hardware':'RTX 4080 Laptop 12GB','engine':'NInfer',
  'config':{'context':8192,'kv_dtype':'fp8','concurrency':1,'no_prefix_reuse':True,'greedy':True,'no_thinking':True},
  'scan':{'configs':['无 MTP','MTP3','MTP4','MTP5'],'median_tok_s':[29.07,89.59,100.10,108.62]},
  'final':{'baseline_median_tok_s':29.07,'mtp5_median_tok_s':134.42,'mtp5_runs_range_tok_s':[132.19,136.06],
   'output_tokens':256,'repeat':3,'warmup':True,'workload':'规则数字序列'},
  'mtp5_workloads':{'labels':['普通中文长文','Python 代码','规则数字序列'],'tok_s':[51.3,84.9,134.42],
   'note':'Chinese/code are scenario observations. Rule sequence is final repeated-run median. No matched non-MTP baselines for Chinese/code in recovered summary.'},
  'prefill':{'prompt_tokens':2583,'chunks':[1024,2048],'observed_gain_pct':.45,'adopted':False},
  'baseline_short_prompt_ttft_ms':110,
 },
}

def text(fig,x,y,s,size=12,color=INK,weight='normal',**kw):
 return fig.text(x,y,s,fontsize=size,color=color,weight=weight,va='top',**kw)

def base(number,title,subtitle):
 fig=plt.figure(figsize=(16,9),facecolor=BG)
 text(fig,.045,.958,'DESKBOT  /  MODEL BENCHMARK',10,TEAL,'bold')
 text(fig,.045,.912,title,27,INK,'bold')
 text(fig,.045,.849,subtitle,11,MUTED)
 text(fig,.955,.953,number,11,MUTED,ha='right')
 fig.add_artist(plt.Line2D([.045,.955],[.814,.814],color=GRID,linewidth=1))
 return fig

def foot(fig,line1,line2):
 fig.add_artist(plt.Line2D([.045,.955],[.095,.095],color=GRID,linewidth=1))
 text(fig,.045,.078,line1,9,MUTED)
 text(fig,.045,.048,line2,9,MUTED)

def axes(fig,rect,title,subtitle=None):
 ax=fig.add_axes(rect,facecolor=BG)
 ax.set_title(title,loc='left',fontsize=15,weight='bold',color=INK,pad=28 if subtitle else 15)
 if subtitle:ax.text(0,1.035,subtitle,transform=ax.transAxes,color=MUTED,fontsize=9,va='bottom')
 for side in ['top','right','left']:ax.spines[side].set_visible(False)
 ax.spines['bottom'].set_color(GRID)
 ax.tick_params(axis='both',colors=MUTED,labelsize=10,length=0,pad=7)
 ax.grid(axis='x',color=GRID,linewidth=.65,zorder=0)
 ax.set_axisbelow(True)
 return ax

def paired(ax,labels,a,b,limit,unit,fmt='.1f',ticks=None):
 y=np.arange(len(labels));height=.29
 ax.barh(y-.17,a,height,color=TEAL,zorder=3)
 ax.barh(y+.17,b,height,color=PALE,zorder=3)
 ax.set_yticks(y,labels);ax.invert_yaxis();ax.set_xlim(0,limit)
 ax.set_xlabel(unit,loc='right',fontsize=9,color=MUTED,labelpad=6)
 if ticks is not None:ax.set_xticks(ticks)
 for ys,values in [(y-.17,a),(y+.17,b)]:
  for yy,v in zip(ys,values):ax.text(v+limit*.012,yy,format(v,fmt),va='center',fontsize=9.5,color=INK)
 ax.margins(y=.13)

def single(ax,labels,vals,limit,unit,colors=None,fmt='.2f',ticks=None):
 y=np.arange(len(vals));colors=colors or [TEAL]*len(vals)
 ax.barh(y,vals,.48,color=colors,zorder=3)
 ax.set_yticks(y,labels);ax.invert_yaxis();ax.set_xlim(0,limit)
 ax.set_xlabel(unit,loc='right',fontsize=9,color=MUTED)
 if ticks is not None:ax.set_xticks(ticks)
 for yy,v in zip(y,vals):ax.text(v+limit*.015,yy,format(v,fmt),va='center',fontsize=11,color=INK,weight='bold')
 ax.margins(y=.25)

def legend(fig,x,y,names):
 for i,name in enumerate(names):
  xx=x+i*.225
  fig.add_artist(Rectangle((xx,y-.010),.009,.013,transform=fig.transFigure,color=[TEAL,PALE][i]))
  text(fig,xx+.015,y+.002,name,10)

def table(fig,rect,headers,rows,widths=None,size=10):
 ax=fig.add_axes(rect);ax.axis('off')
 t=ax.table(cellText=rows,colLabels=headers,colWidths=widths,cellLoc='left',colLoc='left',bbox=[0,0,1,1])
 t.auto_set_font_size(False);t.set_fontsize(size)
 for (r,c),cell in t.get_celld().items():
  cell.set_edgecolor(GRID);cell.set_linewidth(.6);cell.PAD=.09
  cell.set_facecolor('#E7EBDD' if r==0 else BG)
  cell.set_text_props(color=INK,weight='bold' if r==0 else 'normal')
 return t

def save(fig,name):
 for ext in ['png','svg']:fig.savefig(ROOT/f'{name}.{ext}',facecolor=BG)
 plt.close(fig)

def overview():
 fig=base('01 / 04','模型选型与推理优化：完整实验总览','三类工作分别回答：识别是否够快、合成多久开口、本地模型如何提高生成速度。')
 cols=[.045,.36,.675];w=.28
 for x in [.335,.65]:fig.add_artist(plt.Line2D([x,x],[.15,.78],color=GRID,linewidth=1))
 titles=['ASR  /  语音识别','TTS  /  语音合成','LLM  /  本地 Qwen 优化']
 heads=['SenseVoiceSmall','EdgeTTS','NInfer + MTP5']
 values=['25–35 ms','1.10–1.22 s','29.07 → 134.42']
 captions=['GPU 各语言推理中位数范围','各类文本首音频中位数范围','规则序列生成速度 · tok/s']
 for x,t,h,v,c in zip(cols,titles,heads,values,captions):
  text(fig,x,.78,t,12,TEAL,'bold');text(fig,x,.724,h,21,INK,'bold')
  text(fig,x,.655,v,25,TEAL,'bold');text(fig,x,.603,c,10,MUTED)
 ax=axes(fig,[.13,.36,.17,.16],'峰值 GPU 显存','CUDA 正式批次；越低越好')
 single(ax,['SenseVoice','Fun-ASR'],DATA['asr_gpu']['peak_gpu_allocated_mib'],4200,'MiB',[TEAL,PALE],'.0f',[0,2000,4000])
 ax=axes(fig,[.43,.36,.17,.16],'短句首音频','不同接入方式；越低越好')
 single(ax,['EdgeTTS','Qwen3-TTS'],[1.100474,2.083379],2.8,'秒',[TEAL,PALE],'.2f',[0,1,2])
 ax=axes(fig,[.745,.36,.17,.16],'相同规则序列','最终预热后复测；越高越好')
 single(ax,['无 MTP','MTP5'],[29.07,134.42],170,'tok/s',[PALE,TEAL],'.2f',[0,50,100,150])
 lines=[
  ('完成：CPU 复刻 + GPU 正式评测','5 种语言，预热后每项重复 5 次。','支持当前采用 SenseVoice；','尚无系统性准确率对比。'),
  ('完成：云端 / 本地合成方案比较','5 类文本，统计延迟、RTF 并留音频。','Qwen3-TTS 本次为整段生成；','不能据此判断模型不支持流式。'),
  ('完成：部署、接入与 MTP 参数调优','中文约 51.3；代码约 84.9 tok/s。','规则序列约 4.62 倍；','不代表全部对话都提高 4.62 倍。')]
 for x,ls in zip(cols,lines):
  for j,line in enumerate(ls):text(fig,x,.292-j*.037,line,10.5,INK if j==0 else MUTED,'bold' if j==0 else 'normal')
 foot(fig,'记录范围：ASR/TTS 五次重复的历史结果；LLM 基线与最终规则序列各三次正式测量，另有场景观测。',
      '来源：历史实验读取输出与优化记录；非本次复测。ASR/TTS 原始 JSON/CSV 本次未找回。详细条件见后续三图。')
 save(fig,'01-overview')

def asr_figure():
 d=DATA['asr_gpu'];fig=base('02 / 04','ASR 全场景对比：五种语言与两类运行环境','正式 GPU 批次：每个样本预热后重复 5 次。条形显示各样本中位数，不代表准确率。')
 legend(fig,.045,.785,d['models'])
 ax=axes(fig,[.087,.405,.382,.29],'识别推理耗时','单位 ms；越低越好')
 paired(ax,d['languages'],*d['latency_median_ms'],460,'毫秒',ticks=[0,100,200,300,400])
 ax=axes(fig,[.575,.405,.37,.29],'识别实时率 RTF','处理时间 ÷ 输入音频时长；越低越好')
 paired(ax,d['languages'],*d['rtf_median'],.10,'RTF','.4f',[0,.025,.05,.075,.10])
 text(fig,.045,.305,'GPU 正式批次 · 加载与资源',13,INK,'bold')
 table(fig,[.045,.162,.455,.113],['模型','加载 / s','峰值显存 / MiB','进程 RSS / MiB'],[
  ['SenseVoiceSmall','5.990','939.2','1140.6'],['Fun-ASR-Nano','21.464','3394.0','3310.9']],widths=[.32,.17,.26,.25],size=9)
 text(fig,.545,.305,'CPU 早期批次 · 单独列示',13,INK,'bold')
 table(fig,[.545,.162,.41,.113],['模型','加载 / s','RSS / GiB','RTF 范围'],[
  ['SenseVoiceSmall','5.82','1.87','0.030–0.031'],['Fun-ASR-Nano','21.71','3.31','0.153–0.357']],widths=[.34,.16,.18,.32],size=9)
 text(fig,.045,.137,'显存为 peak allocated；RSS 为测量进程总占用，不是模型独占内存。CPU/GPU 为不同批次，不推导配对提速倍数。',9,MUTED)
 foot(fig,'结论：本批 GPU 样本下 SenseVoice 更快、显存更低。韩语等样本存在转写质量差异，尚未完成 WER/CER 评估。',
      '来源：历史 ASR 结果读取输出及 CPU 阶段总结。RTF 范围为不同样本中位数的范围，不是置信区间。')
 save(fig,'02-asr-all-scenarios')

def tts_figure():
 d=DATA['tts'];fig=base('03 / 04','TTS 全场景对比：首音频、总耗时与实时率','同一组五类文本，每项预热后重复 5 次；EdgeTTS 云端流式 vs Qwen3-TTS 0.6B GPU 整段生成。')
 legend(fig,.045,.785,d['models'])
 ax=axes(fig,[.10,.43,.37,.265],'首音频等待时间','收到第一段音频需要多久；越低越好')
 paired(ax,d['samples'],*d['first_audio_median_s'],23,'秒','.2f',[0,5,10,15,20])
 ax=axes(fig,[.585,.43,.36,.265],'总生成时间','完整合成耗时；与左图使用相同比例尺')
 paired(ax,d['samples'],*d['total_median_s'],23,'秒','.2f',[0,5,10,15,20])
 text(fig,.045,.332,'实时率与输出时长 · 不同音色语速会改变 RTF 的分母',12,INK,'bold')
 table(fig,[.045,.142,.525,.157],['样本','Edge RTF','Qwen RTF','音频时长 Edge / Qwen（s）'],[
  [n,f'{a:.3f}',f'{b:.3f}',f'{aa:.2f} / {bb:.2f}'] for n,a,b,aa,bb in zip(d['samples'],*d['rtf_median'],*d['audio_median_s'])],widths=[.20,.18,.18,.44],size=9)
 text(fig,.61,.332,'本地 Qwen3-TTS · 资源与 CPU 记录',12,INK,'bold')
 text(fig,.61,.287,'GPU：加载 1.509 s；峰值 allocated 2769.1 MiB',10)
 text(fig,.61,.248,'CPU：加载 2.62 s；进程 RSS 4.60 GiB',10)
 text(fig,.61,.209,'CPU 的 RTF 为 2.42–3.08，慢于实时。',10)
 text(fig,.61,.170,'产物：25 个 Edge MP3 + 25 个 Qwen WAV。',10,TEAL,'bold')
 foot(fig,'解释：Qwen 本次完整 waveform 接口使首音频 = 总耗时；流式/整段及云端/本地条件不同，只比较当时接入方案。',
      '来源：历史 TTS 结果读取输出与 CPU 总结。RTF = 生成时间 / 输出音频时长；未进行正式自然度或音色一致性评分。')
 save(fig,'03-tts-all-scenarios')

def llm_figure():
 d=DATA['llm'];fig=base('04 / 04','本地 Qwen 推理优化：参数扫描与最终复测分开看','服务名 qwen3.8-27b · NInfer / Bonsai 三元量化制品 · RTX 4080 Laptop 12GB · 8K 上下文 / FP8 KV / 单并发')
 ax=axes(fig,[.105,.465,.35,.245],'① 参数扫描：比较 MTP 草稿长度','规则数字序列，固定 256-token 输出；记录中位数')
 single(ax,d['scan']['configs'],d['scan']['median_tok_s'],165,'tok/s',[PALE,TEAL,TEAL,TEAL],'.2f',[0,50,100,150])
 ax=axes(fig,[.62,.465,.32,.245],'② 最终 MTP5：预热后再次复测','三次正式测量；规则序列，256-token 输出')
 single(ax,['原配置','最终 MTP5'],[29.07,134.42],165,'tok/s',[PALE,TEAL],'.2f',[0,50,100,150])
 text(fig,.62,.427,'最终范围 132.19–136.06 tok/s；相对基线约 4.62 倍。',10,TEAL,'bold')
 ax=axes(fig,[.135,.16,.32,.165],'③ MTP5 实际内容：收益随负载变化','中文 / 代码为场景观测；规则序列为最终中位数')
 single(ax,d['mtp5_workloads']['labels'],d['mtp5_workloads']['tok_s'],165,'tok/s',[TEAL,TEAL,GOLD],'.1f',[0,50,100,150])
 text(fig,.555,.36,'④ 最终采用的配置与工程取舍',14,INK,'bold')
 lines=[
  '启用 MTP5：--spec mtp --draft-tokens 5 --lm-head-draft',
  '保留 --no-prefix-reuse，修复多轮工具请求后的引擎失败。',
  '代价：每轮重算历史；长对话的首字等待可能增加。',
  'prefill 1024 → 2048：2583-token 提示仅提升约 0.45%，未采用。',
  '已接入 Deskbot 启动器，普通多轮及工具定义请求验证成功。'
 ]
 for i,line in enumerate(lines):text(fig,.555,.312-i*.039,line,9.5,INK if i in [0,4] else MUTED)
 foot(fig,'口径：tok/s 为生成阶段吞吐，不是首字延迟或语音端到端延迟。扫描 MTP5 的 108.62 与最终 134.42 属于不同轮次。',
      '来源：历史 NInfer 优化记录；当前文字启动脚本保留 MTP5。未推导中文/代码提速倍数；4.62 倍不适用于所有场景。')
 save(fig,'04-qwen-throughput')

if __name__=='__main__':
 (ROOT/'benchmark-data.json').write_text(json.dumps(DATA,ensure_ascii=False,indent=2)+'\n')
 for fn in [overview,asr_figure,tts_figure,llm_figure]:fn()
 with zipfile.ZipFile(ROOT/'deskbot-benchmark-figures.zip','w',zipfile.ZIP_DEFLATED) as z:
  for p in sorted(ROOT.iterdir()):
   if p.suffix in ['.png','.svg','.json','.py','.md']:z.write(p,p.name)
 print('Rendered 4 figures in PNG and SVG, plus data and source bundle.')
