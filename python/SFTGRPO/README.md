# SFTGRPO：Qwen3.5-2B仓库问答后训练

复用SWE-QA-Pro教师轨迹、三类只读工具和TRL训练循环，支持两种监督范围的LoRA SFT、在线多轮GRPO，以及独立答案采集和复核。源码位于独立包[`src/sftgrpo/`](../src/sftgrpo/)，与RAG的`src/repomentor/`分开；本目录保留配置、依赖、测试、数据与训练产物。

## 配置与数据

配置集中在[run-config.json](run-config.json)和[data-policy.json](data-policy.json)：

| 项目 | 默认设置 |
| --- | --- |
| SFT | 963训练/32开发；32K；每种监督2 epochs；micro1、累积8 |
| 监督条件 | all_assistant：规划、调用和回答；last_assistant：仅末条助手消息，含EOS |
| LoRA | BF16基座、r16/alpha32、all-linear、lr1e-4、5%预热、cosine |
| GRPO | 128题×4候选、1 epoch；micro1、累积4；lr1e-6、KL0.02 |
| 优化目标 | TRL的token级GRPO、组内标准差缩放、DAPO式词元聚合、每批更新一次 |
| 在线预算 | 后续30720词元（含观察与控制），32工具轮，单次生成2048，回答预留2048 |
| 开发与评价 | RL开发8题；Bench六库各5题；temperature0.2、top_p1、top_k0 |

两个SFT条件从相同基座独立训练，使用相同完整input_ids、数据顺序和更新预算，只改变labels。系统、用户、工具观察和padding不计监督损失；末条模式保留最终助手整条消息及EOS。

准备过程先按仓库basename隔离来源，排除全部Bench和RAG保留来源，再保留32K内调用闭合的完整轨迹。重复调用、外部催停、工具轮次、错误观察与观察占比作为逐条审核标记，保持两个监督条件的训练成员一致。basename提供保守隔离，语义正确性仍需要证据复核。

新版准备结果位于`data/current/`和`reports/current/`。原始公开输入仍在`data/raw/`，旧数据、训练输出和报告保持原位；恢复检查会拒绝数据、源码或奖励配置改变后的身份混用。

## 奖励与reward hacking约束

`--reward-mode grounded`为默认模式；`quality`保留原五维终点奖励，供受控消融使用。

1. **证据身份。** 仅从该候选最终保留的tool消息收集模型可见观察。原始环境日志中的超长尾部、回滚观察和助手自述均不作为已读源码。
2. **引用与断言。** 核对`path.py:L10-L20`、`path.py:10-20`等引用行是否可见。同一次judge请求沿用五维rubric，并补充grounding、critical_error和简短事实依据；引用存在与结论被支持分别判断。
3. **正确性优先。** 默认正确性低于6、存在关键事实错误、证据不足或引用未核验时，将奖励限制到至多0.2。
4. **质量门控成本。** 正确性和证据通过、基础质量分至少0.6时，才按精确重复请求每次扣0.01，总扣分上限0.05。必要调用和正常错误恢复不因调用总数多而扣分。
5. **评分故障。** grounded模式任何候选缺少有效质量/事实核验，保存整组原文并停止该组更新。quality模式保留官方组均值插补策略，记录插补标记。

阈值在配置中显式保存，属于可校准起点。引用检查覆盖原工具文件查看格式，语义支持由judge判断；其误判仍需用开发题反例和独立复核监控。每条记录保留基础奖励、门控原因、扣分、可见证据哈希、源码身份和组更新资格。

## 本地离线准备与检查

沿用现有环境，不安装另一套本地依赖：

```powershell
& 'D:\anaconda\envs\repomentor\python.exe' -s -X utf8 -m sftgrpo.prepare data
& 'D:\anaconda\envs\repomentor\python.exe' -s -X utf8 -m sftgrpo.train preflight
& 'D:\anaconda\envs\repomentor\python.exe' -s -X utf8 -m pytest SFTGRPO/tests -q -p no:cacheprovider
& 'D:\anaconda\envs\repomentor\python.exe' -s -X utf8 -m ruff check --no-cache src/sftgrpo SFTGRPO/tests
```

`data`使用已锁定的本地公开输入；缺少输入时先运行`python -s -m sftgrpo.prepare fetch`。CPU预检检查模板、目标、配置与准备身份，并列出缺少的在线归档；离线SFT不要求先下载所有RL仓库。

## 云端训练

从Python项目根目录（含`src/`和`SFTGRPO/`）运行；使用云镜像明确选定的Python，安装`SFTGRPO/requirements.txt`与匹配CUDA/PyTorch的`SFTGRPO/requirements-cuda.txt`。本地已安装的Python项目自动暴露`src/`；尚未安装时同样先将`src/`加入`PYTHONPATH`。GPU预检检查BF16、FlashAttention或高效SDPA、DeltaNet/conv内核；保留梯度检查点、分块融合LM head及数值检查。

```bash
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
python -s -m sftgrpo.train preflight --gpu
python -s -m sftgrpo.train sft --supervision last_assistant --output outputs/sft-last
python -s -m sftgrpo.train sft --supervision all_assistant --output outputs/sft-all
python -s -m sftgrpo.prepare snapshots
python -s -m sftgrpo.train preflight --gpu --tools
python -s -m sftgrpo.train grpo --adapter outputs/sft-all/adapter --reward-mode grounded --train-tasks 128 --output outputs/grpo-grounded128
```

在线工具需要Linux只读隔离：优先bubblewrap；容器禁用用户namespace时，显式配置并验证chroot、低权限用户和seccomp后备方案。只准备固定repo/commit归档，不安装或执行目标仓库代码。

GRPO使用独立的`REPOMENTOR_PY_SFTGRPO_JUDGE_API_KEY`、`REPOMENTOR_PY_SFTGRPO_JUDGE_MODEL`和可选BASE_URL。凭据只通过环境配置；脚本不读取RAG凭据。费用账本在请求前预留费用并跨续训持久化。奖励A/B要匹配训练任务、模型起点和更新预算，使用不同output目录。

需要先检查32K资源时使用`--smoke`：SFT两次更新，GRPO一个四候选组。正式SFT从基座开始。增加`--train-minutes N`可在完整GRPO更新后软停；同目录同参数加`--resume latest`恢复。恢复包含当前/参考adapter、optimizer、scheduler、RNG与数据身份；采样评分后发生OOM时复用绑定父策略的原生批次。

## 四组答案采集与评价

```bash
python -s -m sftgrpo.train evaluate --split eval --output outputs/eval-base
python -s -m sftgrpo.train evaluate --adapter outputs/sft-last/adapter --split eval --output outputs/eval-last
python -s -m sftgrpo.train evaluate --adapter outputs/sft-all/adapter --split eval --output outputs/eval-all
python -s -m sftgrpo.train evaluate --adapter outputs/grpo-grounded128/adapter --split eval --output outputs/eval-grpo
python -s -m sftgrpo.train summarize --split eval --output outputs/eval-base
```

评价只采集答案和工具轨迹，不调用付费judge。按同一参考、源码与五维标准复核后，在各目录提供`reviews.jsonl`并运行summarize。每条复核须包含task_id、answer_sha256、五个1—10整数、reviewer=`codex_session_assistant`及human_approved=false；明确未完成计0，待复核或评分故障保留未知。

汇总同时输出全题均分、有效答案均分、完成状态、模型工具请求、实际执行与精确重复；被跳过执行的请求仍计入行为成本。输入文件须恰好覆盖冻结题目，每题一次，复核哈希必须绑定具体答案。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| [core.py](../src/sftgrpo/core.py) / [prepare.py](../src/sftgrpo/prepare.py) | 消息协议、来源、行为标记、两种目标与准备产物 |
| [train.py](../src/sftgrpo/train.py) | TRL配置、SFT/GRPO入口、恢复身份及评价汇总 |
| [runtime.py](../src/sftgrpo/runtime.py) | 三工具环境、只读隔离、judge调用与费用账本 |
| [rewards.py](../src/sftgrpo/rewards.py) | 实际可见证据、引用核验、事实门控与有界重复惩罚 |
| [generation.py](../src/sftgrpo/generation.py) | 原生TRL工具循环的预算、缓存及持久化批次适配 |

`python -s -m sftgrpo.prepare bundle`按白名单打包`src/sftgrpo/`、`SFTGRPO/`配置、准备数据、固定归档和校验清单，解包后保持这两个目录并列；不会打包RAG源码。输入和输出参数仍以`SFTGRPO/`为相对根目录，与启动时的工作目录无关。模型权重、凭据、展开仓库、Windows依赖及运行数据库保持在本地忽略路径中。
