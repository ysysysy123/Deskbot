const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const asr = [
  {name:'中文',a:27.325,b:183.229,ra:.004861,rb:.032594},
  {name:'英文',a:34.469,b:292.506,ra:.004801,rb:.040738},
  {name:'日文',a:34.660,b:366.971,ra:.004794,rb:.050759},
  {name:'韩文',a:25.540,b:379.473,ra:.005491,rb:.081581},
  {name:'粤语',a:25.297,b:203.132,ra:.004858,rb:.039006}
];
const tts = [
  {name:'短句',a:1.100474,b:2.083379,ta:1.475119,tb:2.083379,ra:.682926,rb:.813820},
  {name:'桌宠对话',a:1.114228,b:5.147105,ta:1.514887,tb:5.147105,ra:.317187,rb:.814415},
  {name:'技术混合',a:1.129709,b:6.889711,ta:1.563802,tb:6.889711,ra:.238676,rb:.844327},
  {name:'数字',a:1.218230,b:6.695985,ta:1.645798,tb:6.695985,ra:.267871,rb:1.087011},
  {name:'长文本',a:1.220200,b:18.594846,ta:1.835227,tb:18.594846,ra:.098036,rb:.821327}
];
let bench = 'asr';
const configs = {
  asr:{names:['SenseVoiceSmall','Fun-ASR-Nano-2512'],metrics:[['latency','推理耗时'],['memory','峰值显存'],['load','加载时间'],['rtf','实时率 RTF']],conclusion:'更快的识别，\n更小的显存占用。',body:'在这批样本与 GPU 环境中，SenseVoiceSmall 更符合实时桌宠的性能约束。',value:'25–35 ms',label:'SenseVoice · 各样本推理中位数',note:'5 种语言样本，不是完整准确率评估。单模型推理时间不等于整机响应时间。'},
  tts:{names:['EdgeTTS · 云端','Qwen3-TTS 0.6B · GPU'],metrics:[['latency','首音频时间'],['total','总生成时间'],['rtf','实时率 RTF']],conclusion:'首音频等待，\n决定开口的时机。',body:'当前接入方式下，EdgeTTS 较快产生首音频；本地 Qwen3-TTS 保留为后续流式优化方向。',value:'1.10–1.22 s',label:'EdgeTTS · 各文本首音频中位数',note:'Edge 为流式；Qwen 本次为整段 waveform 接口，首音频等于总生成时间。不是同等接口下的模型能力排名。'}
};
function renderBench(reset=false){
  const cfg=configs[bench];
  if(reset) $('#metric').innerHTML=cfg.metrics.map(([v,t])=>`<option value="${v}">${t}</option>`).join('');
  const metric=$('#metric').value; let rows,unit,decimals,title;
  if(metric==='memory'){rows=[{name:'GPU',a:939.1611328125,b:3394.0048828125}];unit='MiB';decimals=1;title='峰值 GPU allocated';}
  else if(metric==='load'){rows=[{name:'加载',a:5.98992459800138,b:21.464337255998544}];unit='s';decimals=2;title='模型加载时间';}
  else {rows=(bench==='asr'?asr:tts).map(r=>({name:r.name,a:metric==='rtf'?r.ra:metric==='total'?r.ta:r.a,b:metric==='rtf'?r.rb:metric==='total'?r.tb:r.b}));unit=metric==='rtf'?'':bench==='asr'?'ms':'s';decimals=metric==='rtf'?4:bench==='asr'?1:2;title=metric==='rtf'?'实时率 RTF':bench==='asr'?'各语言识别耗时':metric==='total'?'各类文本总生成时间':'各类文本首音频时间';}
  const max=Math.max(...rows.flatMap(r=>[r.a,r.b]))*1.05;
  $('#chartTitle').textContent=title;
  $('#chartSubtitle').textContent=['memory','load'].includes(metric)?'同一正式批次的模型级记录；越低越好。':'每项预热后重复 5 次，显示中位数；越低越好。';
  $('#legend').innerHTML=cfg.names.map(n=>`<span><i></i>${n}</span>`).join('');
  const fmt=n=>`${n.toFixed(decimals)}${unit?' '+unit:''}`;
  $('#chart').innerHTML=rows.map(r=>`<div class="chart-row"><span class="chart-row-label">${r.name}</span><div class="bars">${[r.a,r.b].map((n,i)=>`<div class="bar-track"><div class="bar-area" style="--width:${(n/max*100).toFixed(3)}%"><div class="bar ${i?'secondary':''}"></div><span class="bar-value">${fmt(n)}</span></div></div>`).join('')}</div></div>`).join('');
  $('#chart').setAttribute('aria-label',`${title}，${rows.map(r=>`${r.name}：${cfg.names[0]} ${fmt(r.a)}，${cfg.names[1]} ${fmt(r.b)}`).join('；')}`);
  $('#axisNote').textContent=metric==='rtf'?'RTF = 处理时间 ÷ 音频时长。小于 1 表示处理快于音频时长。':`条形从 0 起，使用同一比例尺；单位 ${unit}。${bench==='tts'?'不同文本长度会影响总耗时。':''}`;
  $('#benchConclusion').textContent=cfg.conclusion;$('#benchBody').textContent=cfg.body;$('#highlightValue').textContent=cfg.value;$('#highlightLabel').textContent=cfg.label;$('#methodNote').textContent=cfg.note;
  $('#dataTable').innerHTML=`<table><caption class="muted">${title} · ${unit||'无量纲'}</caption><thead><tr><th scope="col">样本 / 记录</th>${cfg.names.map(n=>`<th scope="col">${n}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr><th scope="row">${r.name}</th><td>${fmt(r.a)}</td><td>${fmt(r.b)}</td></tr>`).join('')}</tbody></table>`;
  $$('[data-bench]').forEach(b=>{const active=b.dataset.bench===bench;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',active);});
}
$$('[data-bench]').forEach(b=>b.addEventListener('click',()=>{bench=b.dataset.bench;renderBench(true);}));$('#metric').addEventListener('change',()=>renderBench());renderBench(true);

const flow=[
  ['设备音频','WebSocket / Opus','设备上传压缩音频，服务端维护独立会话并解码。输入固定为 16 kHz 单声道，连接协议与业务后端分离。',['WebSocket v1','独立会话','Opus 解码']],
  ['分轮与识别','Silero / SenseVoice','VAD 判断说话结束，SenseVoice 将一轮音频转成文字。当前 ASR 在用户说完后识别，不是逐词流式识别。',['Silero VAD','SenseVoiceSmall','整轮识别']],
  ['组织上下文','SQLite / Memory','按 Device-Id 读取历史摘要、近期消息和简单相关历史，与角色提示及本轮输入一起组织为对话上下文。',['设备隔离','持久化','摘要']],
  ['对话与工具','LLM / MCP','对话后端生成可朗读文本；需要设备能力时发现并调用当前会话工具。明确观察请求可直接拍照，视觉结果进入回复。',['DialogueBackend','工具调用','视觉接入']],
  ['语音合成','TTS / FFmpeg','文字按句进入合成队列；Edge 音频边到达边转码为 PCM。支持本地命令 TTS 接口，方便替换合成方案。',['分句队列','流式转码','24 kHz PCM']],
  ['返回设备','Opus / Playback','将 PCM 编码为 Opus，按 60 ms 帧节奏发给实机。回复完成后，auto/realtime 模式重新进入服务端监听。',['定速下发','恢复监听','取消旧轮']]
];
let flowIndex=0,flowTimer=null;
$('#flowPath').innerHTML=flow.map((f,i)=>`<button class="flow-node" data-flow="${i}" aria-pressed="false"><small>0${i+1}</small><strong>${f[0]}</strong><span>${f[1]}</span></button>`).join('');
function setFlow(i){flowIndex=i;const f=flow[i];$('#flowIndex').textContent=`STAGE 0${i+1} / 06`;$('#flowTitle').textContent=f[0];$('#flowText').textContent=f[2];$('#flowTags').innerHTML=f[3].map(t=>`<span>${t}</span>`).join('');$$('[data-flow]').forEach(b=>{const a=Number(b.dataset.flow)===i;b.classList.toggle('active',a);b.setAttribute('aria-pressed',a);});}
function stopFlow(){clearInterval(flowTimer);flowTimer=null;$('#playFlow').textContent='逐步播放链路 ▷';}
$$('[data-flow]').forEach(b=>b.addEventListener('click',()=>{stopFlow();setFlow(Number(b.dataset.flow));}));
$('#playFlow').addEventListener('click',()=>{if(flowTimer){stopFlow();return;}setFlow(0);$('#playFlow').textContent='暂停链路 Ⅱ';flowTimer=setInterval(()=>{if(flowIndex===flow.length-1){stopFlow();return;}setFlow(flowIndex+1);},1800);});setFlow(0);

const milestones=[
  {date:'08.07',short:'语音服务初版',title:'建立语音服务基础',text:'形成兼容设备协议的本地 Python 服务，将识别、对话、语音合成和记忆连接起来。',items:['WebSocket 与 Opus 音频链路','ASR / LLM / TTS 模块接口','设备记忆与测试基础'],source:'依据：8 月 7 日语音服务提交'},
  {date:'09 上旬',short:'模型对比实验',title:'用实验支撑模型选择',text:'完成 Linux 环境复刻，从 CPU 验证推进到 CUDA 正式批次，并解决云端合成的网络访问问题。',items:['两种 ASR、两种 TTS 对比','预热与每项五次重复测试','JSON / CSV 统计、50 个试听音频'],source:'依据：历史模型对比聊天及正式结果读取输出'},
  {date:'09.22–24',short:'真实语音联调',title:'从模型连通到持续对话',text:'真实模型链路跑通，浏览器测试端加入自动分轮、说话打断和摄像头视觉问答。',items:['真实 ASR → GLM → EdgeTTS','持续麦克风与自动打断','浏览器摄像头、拍照与视觉回复'],source:'依据：本地测试与验收记录'},
  {date:'10.02',short:'设备工具接入',title:'把语言请求接到设备',text:'完善实机 MCP 工具循环、照片上传分析、多设备状态和启动入口，支持跨模块联调。',items:['MCP 初始化、分页发现与工具返回','在线设备、历史与阶段耗时','音频定速下发与统一启停入口'],source:'依据：服务端工作记录与对应提交'},
  {date:'10.11',short:'稳定性完善',title:'补齐持续运行的细节',text:'围绕服务就绪、会话重连、空回复、记忆管理和取消控制完善服务行为。',items:['就绪检查、旧会话替换与超时','本地命令 TTS、记忆保留与检索','342 Python + 16 浏览器 + 7 启动器测试'],source:'依据：10 月 11 日回归记录'},
  {date:'10.13',short:'语音与相机恢复',title:'解决影响现场交互的问题',text:'修复 EdgeTTS 旧端点握手 403，协同完成实机摄像头工具注册问题的定位与更新后联调。',items:['在线 LLM/TTS 回复恢复并获用户确认','设备拍照工具出现并调用成功','实机画面识别与语音输出获现场确认'],source:'依据：10 月 13 日工作与现场验收记录'}
];
$('#timelineTabs').innerHTML=milestones.map((m,i)=>`<button data-milestone="${i}" aria-pressed="false"><b>${m.date}</b><span>${m.short}</span></button>`).join('');
function setMilestone(i){const m=milestones[i];$('#milestoneDate').textContent=`2026 / ${m.date}`;$('#milestoneTitle').textContent=m.title;$('#milestoneText').textContent=m.text;$('#milestoneList').innerHTML=m.items.map(x=>`<li>${x}</li>`).join('');$('#milestoneSource').textContent=m.source;$$('[data-milestone]').forEach(b=>{const a=Number(b.dataset.milestone)===i;b.classList.toggle('active',a);b.setAttribute('aria-pressed',a);});}
$$('[data-milestone]').forEach(b=>b.addEventListener('click',()=>setMilestone(Number(b.dataset.milestone))));setMilestone(0);

const demo=[['设备在线','打开在线设备列表。','展示真实 Device-Id、会话和工具状态，说明设备已经接入服务端。'],['普通问答','“请用一句话介绍你自己。”','观察识别文本、模型回复和设备语音播放，展示基础交互链路。'],['上下文追问','“我今天打算学习 Python。”\n“我刚才说要学什么？”','展示同一会话的近期上下文。此项不能单独证明跨重启持久化。'],['拍照回答','“看看前面有什么。”','在相机前放置清晰物体，观察拍照工具、视觉描述与语音回答。'],['运行诊断','展示历史记录与阶段耗时。','解释识别、首段文字、首段语音和工具调用如何帮助定位问题。']];
let demoIndex=0;
$('#demoSteps').innerHTML=demo.map((d,i)=>`<button class="demo-step" data-demo="${i}" aria-pressed="false"><span>0${i+1}</span>${d[0]}</button>`).join('');
function setDemo(i){demoIndex=Math.max(0,Math.min(demo.length-1,i));const d=demo[demoIndex];$('#demoCount').textContent=`DEMO 0${demoIndex+1} / 05`;$('#demoQuote').textContent=d[1];$('#demoObserve').textContent=d[2];$('#demoPrev').disabled=demoIndex===0;$('#demoNext').textContent=demoIndex===4?'从头演示 ↺':'下一步 →';$$('[data-demo]').forEach(b=>{const a=Number(b.dataset.demo)===demoIndex;b.classList.toggle('active',a);b.setAttribute('aria-pressed',a);});}
$$('[data-demo]').forEach(b=>b.addEventListener('click',()=>setDemo(Number(b.dataset.demo))));$('#demoPrev').addEventListener('click',()=>setDemo(demoIndex-1));$('#demoNext').addEventListener('click',()=>setDemo((demoIndex+1)%demo.length));setDemo(0);

const chapters=$$('.chapter');let presenting=false,slide=0,notes=false;
function nearestSlide(){let min=Infinity,index=0;chapters.forEach((c,i)=>{const dist=Math.abs(c.getBoundingClientRect().top-78);if(dist<min){min=dist;index=i;}});return index;}
function setSlide(i){slide=Math.max(0,Math.min(chapters.length-1,i));chapters.forEach((c,j)=>c.classList.toggle('current-slide',j===slide));$('#slideCounter').textContent=`${String(slide+1).padStart(2,'0')} / ${String(chapters.length).padStart(2,'0')}`;$('#slideTitle').textContent=chapters[slide].dataset.title;$('#noteText').textContent=chapters[slide].dataset.note;$('#slidePrev').disabled=slide===0;$('#slideNext').disabled=slide===chapters.length-1;if(presenting){window.scrollTo({top:0,behavior:'instant'});stopFlow();}}
function setPresentation(active){if(active)slide=nearestSlide();presenting=active;document.body.classList.toggle('presenting',active);$('#presentationControls').hidden=!active;$('#speakerNotes').hidden=!active||!notes;$('#presentToggle').innerHTML=active?'退出演讲 <span>×</span>':'演讲模式 <span>↗</span>';setSlide(slide);if(!active){if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});chapters[slide].scrollIntoView({behavior:'instant'});}}
$('#presentToggle').addEventListener('click',()=>setPresentation(!presenting));$('#slidePrev').addEventListener('click',()=>setSlide(slide-1));$('#slideNext').addEventListener('click',()=>setSlide(slide+1));$('#notesToggle').addEventListener('click',()=>{notes=!notes;$('#speakerNotes').hidden=!notes;$('#notesToggle').setAttribute('aria-pressed',notes);});
function toast(msg){$('#toast').textContent=msg;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,3500);}
$('#fullscreenToggle').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();else toast('此浏览器不支持全屏，可使用演讲模式浏览。');}catch{toast('当前浏览器限制全屏，可继续使用演讲模式。');}});
document.addEventListener('fullscreenchange',()=>{$('#fullscreenToggle').textContent=document.fullscreenElement?'退出全屏':'全屏';});
document.addEventListener('keydown',e=>{if(!presenting||$('#sourcesDialog').open||/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;if(e.key==='ArrowRight'||e.key==='PageDown'){e.preventDefault();setSlide(slide+1);}if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();setSlide(slide-1);}if(e.key==='Escape'){setPresentation(false);}});
$$('a[href^="#"]').forEach(a=>a.addEventListener('click',e=>{const i=chapters.findIndex(c=>'#'+c.id===a.getAttribute('href'));if(presenting&&i>=0){e.preventDefault();setSlide(i);}}));
const observer=new IntersectionObserver(entries=>{if(presenting)return;entries.forEach(e=>{if(e.isIntersecting)$$('nav a').forEach(a=>a.classList.toggle('active',a.getAttribute('href')==='#'+e.target.id));});},{rootMargin:'-15% 0px -65% 0px',threshold:0});chapters.forEach(c=>observer.observe(c));
$$('[data-open-sources]').forEach(b=>b.addEventListener('click',()=>$('#sourcesDialog').showModal()));$('#closeSources').addEventListener('click',()=>$('#sourcesDialog').close());$('#sourcesDialog').addEventListener('click',e=>{if(e.target===$('#sourcesDialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
$('.wave').innerHTML=[5,9,16,11,24,18,8,15,26,21,12,7,20,15,10,25,18,9,16,12,23,15,7,18,11,5].map(h=>`<i style="height:${h}px"></i>`).join('');
setSlide(0);
