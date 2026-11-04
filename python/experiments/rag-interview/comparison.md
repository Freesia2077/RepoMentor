# 逐题对照（评审完成后揭盲）

通过要求流程完成、事实正确、完整且引用支持。

| 任务 | none | grep_read | located_file | lexical | code_rag |
| --- | --- | --- | --- | --- | --- |
| history-dev-01 | 未解决 | 未解决 | 未解决 | 未解决 | 未解决 |
| history-dev-04 | 未解决 | 未解决 | 未解决 | 未解决 | 通过 |
| history-dev-05 | 未解决 | 未解决 | 未解决 | 通过 | 未解决 |
| interview-confirm-01 | 未解决 | 未解决 | 未解决 | 未解决 | 未解决 |
| interview-confirm-02 | 未解决 | 未解决 | 未解决 | 未解决 | truncated |
| interview-confirm-03 | 未解决 | 未解决 | 未解决 | 通过 | 通过 |
| interview-confirm-04 | 未解决 | 通过 | context_limit | 通过 | 通过 |
| interview-confirm-05 | 未解决 | 未解决 | context_limit | 通过 | 通过 |
| interview-confirm-06 | 未解决 | 未解决 | context_limit | 未解决 | 未解决 |
| known-first-true | 未解决 | 未解决 | context_limit | 未解决 | 未解决 |
| sweqa-flask-002 | 未解决 | 未解决 | context_limit | 通过 | 未解决 |
| sweqa-requests-001 | 未解决 | 未解决 | context_limit | 未解决 | 通过 |

## history-dev-01

Node nanoid 负数长度如何处理，test/index.test.js 如何验证？

- none：诚实说明空上下文不能判断，但没有完成实现与测试任务。
- grep_read：只回复不足，缺负数路径和实际测试解释，任务未完成。
- located_file：未取得负数路径和测试，没有完成任务。
- lexical：负数RangeError正确，但仓库实际存在nanoid(-10)的直接测试，不能以只看到后段测试声称没有。
- code_rag：抛RangeError方向正确；将实际nanoid(-10)测试说成customAlphabet(-1)，且test第1行不支持任何异常断言。

## history-dev-04

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

- none：不足标记没有完成大小写及测试任务。
- grep_read：仅给正确结论和测试行号，未说明实际大小写输入和断言。
- located_file：不转小写结论与测试引文正确，未解释具体输入及断言，未完成结合测试说明。
- lexical：不转小写正确且测试引文有效，但缺具体输入和断言解释。
- code_rag：实现按原type取设Map，说明FOO及foo断言并引用实现和测试，完成要求。

## history-dev-05

get_jobs() 不传 tag 返回内部列表本身吗？

- none：无法回答，未完成列表身份判断。
- grep_read：先否定返回原列表，随后将引用与副本混淆并称实现未明确，未给出self.jobs[:]的确定浅拷贝结论。
- located_file：只回答不足，没有列表身份结论。
- lexical：准确否定返回原列表并说明浅拷贝，引用self.jobs[:]，原问题无需进一步列举所有浅拷贝后果。
- code_rag：正文浅拷贝而非原列表正确且引文支持，但开头“是的”肯定了返回原列表，前后矛盾。

## interview-confirm-01

仅按当前源码静态推导：若手动 emit('*', payload)，Map 的 '*' 处理器数组会被遍历几次？两轮实参有什么不同？注释是否把这种调用列为支持用法？

- none：空上下文下拒答，未完成静态次数、参数与文档支持判断。
- grep_read：已见README不足以确认实现次数和具体参数，拒答未解决可由源码回答的问题。
- located_file：不支持手动调用的方向对，但实际遍历两次，参数分别payload与('*',payload)，不是同样两参数。
- lexical：误说只遍历一次，类型签名不是注释不支持的原因；两个分支参数方向正确但引用仅覆盖后半。
- code_rag：两种实参正确，但遍历一次与两轮解释矛盾；'*'不属于keyof Events不是通用类型事实，且未依据实际不支持注释解释。

## interview-confirm-02

在 non-secure 入口，customAlphabet('ab', 2.9) 返回的生成器分别不传 size、传 0、传 -1 时，长度是多少？解释位运算与循环条件，不要套用 Node 安全入口的异常规则。

- none：只有不足标记，未给出non-secure长度和机制。
- grep_read：只回复不足，没有完成源码可回答的任务；不将拒答视为解决。
- located_file：默认应沿传入defaultSize=2.9取整成2；负数循环不执行，不是无限字符串。README引文不支持这些运行机制。
- lexical：指出实际只给配置和README而非实现；未完成结果与机制，属于证据未取得。
- code_rag：研究条件固定单请求输出达到上限，原始截断保留，无可发布结构化答案，不重试。

## interview-confirm-03

Scheduler.run_all(delay_seconds=2) 执行某个任务时若该任务给 scheduler.jobs 追加新任务，新任务会在本轮也执行吗？延迟发生在每个任务之前还是之后，最后一个任务之后是否仍 sleep？

- none：空上下文无法判断任务追加和sleep，诚实拒答但业务未完成。
- grep_read：三个直接结论正确；所引docs/examples.rst276-277只说明立即运行，不能支持快照追加与末项sleep。
- located_file：延迟被写成任务之前；实现118-120是先_run_job再sleep。新任务不执行与末项仍sleep正确。
- lexical：新任务不在快照中，本轮不执行；每项运行后sleep，末项也sleep，所引118-120直接支持。
- code_rag：三个直接问题均准确，引用快照循环及执行后sleep，足以支持。

## interview-confirm-04

repeatfunc(function, times, *args) 的 times 为 0、2、None 时分别怎样返回？创建迭代器时会立刻调用 function 吗？请结合两个 starmap 分支解释次数与惰性。

- none：空上下文拒答，未回答次数与starmap惰性。
- grep_read：次数0/2/无限与starmap消费时才调用均正确，引用包含两个真实分支。
- located_file：整文件预检超限，无可发布答案。
- lexical：0/2/None和惰性机制完整；空迭代器的立即StopIteration按首次取值即结束理解，未按词面推成构造时抛错。
- code_rag：次数及repeat/starmap分支和消费时才调用完整，来源支持。

## interview-confirm-05

DefaultJSONProvider 的 ensure_ascii 和 sort_keys 属性都为 True 时，调用 dumps(obj, ensure_ascii=False, sort_keys=False) 会被属性值覆盖吗？未传这两个参数时如何处理，调用是否修改 provider 的属性？

- none：只答不足，没有完成参数覆盖和属性修改问题。
- grep_read：显式参数保留和属性不变正确；缺参时应使用provider属性True/True，不是json.dumps默认True/False，引文只到基类。
- located_file：整文件预检超预算，无模型输出。
- lexical：按本次调用覆盖默认值而非修改实例属性，后文明确澄清；三个问题与setdefault机制完整。
- code_rag：setdefault保留显式False，缺参使用属性，调用不修改provider属性，引用支持。

## interview-confirm-06

parse_dict_header('a=1, a=2, flag') 对重复键 a 和没有等号的 flag 分别如何处理？沿字典写入与 continue 分支解释，不要把它当作保留重复项的列表。

- none：空上下文拒答，未解决重复键和flag机制。
- grep_read：具体返回字典及覆盖/None正确，但没有解释无等号分支跳过后续拆分，未完成要求的continue机制。
- located_file：整文件超预算，未发出模型请求。
- lexical：重复覆盖和flag=None结论有依据，未说明continue跳过键值拆分的机制。
- code_rag：覆盖重复键与flag=None结论正确；题目要求沿continue分支解释，未交代该分支及按首个等号拆分。

## known-first-true

first_true([0, False, 2], default='missing') 返回什么？如果 pred=lambda x: x == 0，返回的 0 会因为是假值而被丢弃吗？结合实现解释。

- none：空上下文拒答，没有完成默认与显式谓词两问。
- grep_read：误将pred=None解释为不做真值过滤并返回0，实际返回2；显式谓词问题也未完成。
- located_file：完整文件超出窗口，未发送且没有答案。
- lexical：默认返回2正确；先说不会丢弃假值又说跳过0/False，且未回答显式谓词返回0的第二问。
- code_rag：把0当True导致默认答案0，实际bool(0)为False且返回2；没有回答显式谓词分支。

## sweqa-flask-002

What is the purpose of the registration tracking flag in the blueprint collection class that prevents setup methods from being called after the blueprint has been registered once?

- none：只有不足标记，未回答注册保护的目的。
- grep_read：注册后禁止setup以保持一致性的目的合理，CHANGES可支持错误策略，但跨worker/分布式一致性扩展未由所引两行直接支持。
- located_file：整文件超过预算，未发送请求，没有可发布答案。
- lexical：正确识别_got_registered_once，解释注册后setup抛AssertionError和一致性目的；原问题不要求额外展开first_registration或设置时序。
- code_rag：没有识别注册标志与setup保护，拒答未完成源码可回答的任务。

## sweqa-requests-001

What is the semantic significance of the `no_proxy` keyword argument in `get_environ_proxies` overriding environment variable precedence, and what does returning a non-empty dictionary when this override is applied indicate?

- none：仅返回insufficient_evidence，未回答参数优先级与非空字典含义。
- grep_read：显式参数优先的方向正确，但非空环境代理字典表示未绕过代理，不是成功排除代理；无引文。
- located_file：整文件超预算，无模型答案。
- lexical：显式优先方向正确；非空绕过与空字典混为一谈，后面自我否定且正文中断，没有最终清晰结论。
- code_rag：明确显式参数覆盖环境设置，非空结果意味着未绕过并返回代理配置，相关调用及条件引文充分。
