# D：Codex 语义评审结果

非盲开发评审；原始结论、自复核修订与模型失败均保留。人工未签核，样本不可用于训练。

## 外部配对结果

| 指标 | 固定 BM25 | SDK Agent |
| --- | --- | --- |
| new_model_requests | 6 | 28 |
| prompt_tokens | 25746 | 68773 |
| completion_tokens | 1192 | 4034 |
| elapsed_median_seconds | 5.36 | 10.234 |
| reference_source_coverage_mean | 0.6111 | 0.4444 |
| delivered_key_points | 6 | 4 |
| requested_key_points | 15 | 15 |

来源覆盖不是答案正确率；关键点分母包括未交付答案，语义判定仍保留不可评状态。此小样本未支持 Agent 优于固定 RAG。

## candidate-01aabfc1d3de — external-fixed-bm25

What is the semantic significance of the `no_proxy` keyword argument in `get_environ_proxies` overriding environment variable precedence, and what does returning a non-empty dictionary when this override is applied indicate?

事实：correct；引用支持：partial；完整性：adequate；拒答：not_applicable；一致性：consistent

核心两点正确；非空结果的 else 正文在 824–825，引用 822–823 只有绕过分支，但测试提供旁证。不是证明真实请求已走代理。

- **supported**：非 None 的 no_proxy 参数优先于环境 no_proxy。仅当参数是 None 才从环境读取。 依据：src/requests/utils.py:764-771

- **supported**：非空代理字典表示未绕过代理且存在代理配置。绕过时返回 {}，否则返回 getproxies，测试配置了 http_proxy。 依据：src/requests/utils.py:816-825; tests/test_utils.py:267-272

## candidate-07221402bcaa — historical-qa

我们公司生产环境昨天用 nanoid 生成了多少个 ID，碰撞次数是多少？

事实：correct；引用支持：not_applicable；完整性：adequate；拒答：appropriate；一致性：consistent

正确性标记评价拒答决策，不代表核实了生产指标；给出的源码引用与未知生产数量无直接证明关系。

- **unverifiable**：不能从公开库源码回答公司昨日真实生产统计。任务需要未提供的公司遥测；不声称库的单元测试能证明生产没有事件。 依据：无可核实的外部事实来源

## candidate-1cbe62edde7d — historical-qa

那么它是返回空字符串还是抛错？请沿用刚才的入口，并说明实现依据。

事实：correct；引用支持：supported；完整性：adequate；拒答：not_applicable；一致性：consistent

修正组完整保留关键实现与测试引用，复用上一轮缓存输入，没有将失败覆盖。

- **supported**：Node 默认 nanoid(-10) 抛出 RangeError。默认导出绑定 customAlphabet；返回函数在 136–137 行转换并检查，测试明确断言 -10。 依据：index.js:133-138; index.js:177-177; test/index.test.js:87-93

## candidate-1d8c5bf124b7 — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

schema/路径通过并不保证依赖边或阅读指导正确。

- **supported**：Map 存储、on/off 注册注销、emit 触发。实现吻合；emit 正文在 103–121，引用 46–94 仅覆盖部分，不意味着所有文字得到其引用支持。 依据：src/index.ts:46-73; src/index.ts:82-121

- **contradicted**：纯函数式/无副作用。on 修改 handler 数组和 Map，off 执行 splice/set；functional 文档仅说不依赖 this。 依据：src/index.ts:66-90; README.md:14-16

- **contradicted**：实现静态导入自身。完整实现没有导入语句，自环无依据。 依据：src/index.ts:1-123

- **contradicted**：测试 82–90 验证 off。该区间是 on 的大小写测试。 依据：test/index_test.ts:79-90

## candidate-1e23b7261bea — external-sdk-agent

What assumptions about encoding representation does the test_pragmas method enforce when validating that get_encodings_from_content correctly extracts and normalizes character encoding declarations from HTTP content headers?

事实：incorrect；引用支持：unsupported；完整性：insufficient；拒答：not_applicable；一致性：consistent

读到 test_precedence 仍解释成文档顺序，是理解错误，不是缺失源码。

- **supported**：支持 HTML meta、pragma 与 XML 声明形式。参数化样例展示这些正文字符串，每例恰好一个 UTF-8 结果。 依据：tests/test_utils.py:368-384

- **contradicted**：多个声明按在正文中出现的顺序返回。测试 XML 出现在最前，返回却是 HTML5、HTML4、XML；实现按 regex 类别拼接结果。 依据：tests/test_utils.py:386-392; src/requests/utils.py:493-501

- **contradicted**：返回规范化后的 UTF-8 编码名。findall 直接返回匹配值，没有大小写或别名规范化步骤。 依据：src/requests/utils.py:493-501

## candidate-1eba673aa92e — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

正确承认没读实现，仍下纯函数架构结论；关于补文档的建议不等于已确认仓库缺文档。

- **supported**：npm 安装及模块/UMD 引入。README 给出对应示例。 依据：README.md:32-52

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **contradicted**：纯函数式/无副作用。on 修改 handler 数组和 Map，off 执行 splice/set；functional 文档仅说不依赖 this。 依据：src/index.ts:66-90; README.md:14-16

## candidate-24fa424bfccd — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

npm build/test 有配置依据；建议尚未核实，上游 issue 未验证。

- **supported**：npm 安装及模块/UMD 引入。README 给出对应示例。 依据：README.md:32-52

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **contradicted**：纯函数式/无副作用。on 修改 handler 数组和 Map，off 执行 splice/set；functional 文档仅说不依赖 this。 依据：src/index.ts:66-90; README.md:14-16

- **contradicted**：test/index_test.ts 是类型编译测试入口。专门的 test-types 脚本指向 test/test-types-compilation.ts；不能将两者混为一体。 依据：package.json:19-21; test/test-types-compilation.ts:9-24

## candidate-341100926784 — historical-qa

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：adequate；拒答：not_applicable；一致性：contradictory

模型看到完整相关源码仍答错，属于生成一致性失败。

- **supported**：on 不将事件名转成小写。Map 直接以 type 为键，测试断言 FOO 与 foo 不同。 依据：src/index.ts:66-73; test/index_test.ts:79-90

- **contradicted**：emit 精确匹配但不区分大小写。Map 查找保留不同字符串键，答案两种表述自相矛盾。 依据：src/index.ts:103-113; test/index_test.ts:79-90

## candidate-366c27106772 — external-fixed-bm25

What is the purpose of the registration tracking flag in the blueprint collection class that prevents setup methods from being called after the blueprint has been registered once?

事实：incorrect；引用支持：unsupported；完整性：insufficient；拒答：not_applicable；一致性：consistent

核心机制混淆，即使检索已提供正确检查函数。

- **contradicted**：标志用于让所有延迟函数仅执行一次。标志保护注册后的 setup 修改；record_once/first_registration 控制特定回调只执行一次，二者不同。 依据：src/flask/sansio/blueprints.py:213-244; src/flask/sansio/blueprints.py:319-335

## candidate-3c11540b86b6 — historical-qa

我们公司生产环境昨天用 nanoid 生成了多少个 ID，碰撞次数是多少？

事实：correct；引用支持：not_applicable；完整性：adequate；拒答：appropriate；一致性：consistent

正确性标记评价拒答决策，不代表核实了生产指标；给出的源码引用与未知生产数量无直接证明关系。

- **unverifiable**：不能从公开库源码回答公司昨日真实生产统计。任务需要未提供的公司遥测；不声称库的单元测试能证明生产没有事件。 依据：无可核实的外部事实来源

## candidate-3c5fceca6077 — external-sdk-agent

What is the relationship between the ASCII-only encoding control parameter in the framework's JSON serialization provider and Unicode character representation in serialized output?

事实：correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

已完成的事实正确，回答文本未写完且缺 False 分支；保留完整性 partial，不将截断片段当作虚构事实。

- **supported**：ensure_ascii 默认 True，非 ASCII 字符转义。provider 注释和设置一致。 依据：src/flask/json/provider.py:144-148

- **partial**：dumps 参数传递解释没有完成。原句先限定非 ASCII，不能额外添加所有 Unicode 再判错；实际缺陷是末句在 kwargs.setdefault( 处结束。 依据：src/flask/json/provider.py:166-179

## candidate-3e87aa35b977 — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

空依赖图符合当前无导入实现，但没有消除自由文本中的类型机制错误。

- **supported**：Map 存储、on/off 注册注销、emit 触发。实现吻合；emit 正文在 103–121，引用 46–94 仅覆盖部分，不意味着所有文字得到其引用支持。 依据：src/index.ts:46-73; src/index.ts:82-121

- **contradicted**：纯函数式/无副作用。on 修改 handler 数组和 Map，off 执行 splice/set；functional 文档仅说不依赖 this。 依据：src/index.ts:66-90; README.md:14-16

- **contradicted**：条件类型区分普通与通配符事件。条件类型在 emit 重载用于事件参数是否允许省略；通配符使用显式重载。 依据：src/index.ts:23-38

## candidate-42b567193b0b — historical-qa

get_jobs() 不传 tag 返回内部列表本身吗？

事实：partially_correct；引用支持：partial；完整性：adequate；拒答：not_applicable；一致性：contradictory

两种策略相同的生成矛盾；有源码、格式正确也不能判完全正确。

- **supported**：无 tag 时返回 self.jobs 的浅拷贝。self.jobs[:] 返回新列表。 依据：schedule/__init__.py:122-133

- **contradicted**：首句是的肯定了返回内部列表本身。与随后而非内部列表本身矛盾。 依据：schedule/__init__.py:130-131

## candidate-4d1163f19d42 — historical-qa

Node nanoid 负数长度如何处理，test/index.test.js 如何验证？

事实：correct；引用支持：supported；完整性：adequate；拒答：not_applicable；一致性：consistent

限定题中 Node 默认入口与 -10 整数；不外推为任意负小数/超范围整数的转换规律。

- **supported**：Node 默认 nanoid(-10) 抛出 RangeError。默认导出绑定 customAlphabet；返回函数在 136–137 行转换并检查，测试明确断言 -10。 依据：index.js:133-138; index.js:177-177; test/index.test.js:87-93

## candidate-5080a475d36e — external-sdk-agent

What is the semantic distinction between InvalidSchema's dual inheritance from RequestException and ValueError in handling URL scheme validation failures, and how do these two parent exception types differ in the context of schema validation?

事实：unverifiable；引用支持：unverifiable；完整性：unverifiable；拒答：unverifiable；一致性：unverifiable

生成失败单独计数；不以额外生成掩盖失败。

- **unverifiable**：没有可交付结构化答案。原始完成状态为 truncated，保留原响应，不从半成品补出成功答案。 依据：无可核实的外部事实来源

## candidate-5975f6854ec8 — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

原 guardrail 已拒绝，保留失败。未读 package 却引用；测试 51–100 主要是 on，不能证明 off；emit 正文未读。

- **supported**：Map 存储、on/off 注册注销、emit 触发。实现吻合；emit 正文在 103–121，引用 46–94 仅覆盖部分，不意味着所有文字得到其引用支持。 依据：src/index.ts:46-73; src/index.ts:82-121

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **contradicted**：纯函数式/无副作用。on 修改 handler 数组和 Map，off 执行 splice/set；functional 文档仅说不依赖 this。 依据：src/index.ts:66-90; README.md:14-16

- **contradicted**：src/index.ts 静态依赖 test/index_test.ts。实现文件没有导入；测试反向导入项目入口。 依据：src/index.ts:1-123; test/index_test.ts:1-5

## candidate-5fcefa05d96f — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

ESM 入口是 package 5/14 行，引用 6/9 不覆盖它；emit 正文未读。重复出现的 Explorer 同一尝试只评一次。

- **supported**：轻量无依赖事件发布订阅，支持通配符与浏览器。README 明确描述；functional 在这里指方法不依赖 this，不代表没有状态修改。 依据：README.md:11-19

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **supported**：Map 存储、on/off 注册注销、emit 触发。实现吻合；emit 正文在 103–121，引用 46–94 仅覆盖部分，不意味着所有文字得到其引用支持。 依据：src/index.ts:46-73; src/index.ts:82-121

- **unsupported**：浏览器原生事件发射器。README 说为浏览器设计的库，并非浏览器内建 API。 依据：README.md:11-19

## candidate-60c7431a05fe — external-fixed-bm25

What is the modular component registration architecture that separates static asset serving from the main application framework while preserving consistent caching behavior across both components?

事实：correct；引用支持：supported；完整性：partial；拒答：not_applicable；一致性：consistent

文档和 CHANGES 引用可支持高层结论，不能因没引用实现就判错；仍缺具体钩子与调用链解释。

- **supported**：Blueprint 将静态资源服务模块化。蓝图提供静态文件，独立 static_folder 与路由。 依据：docs/blueprints.rst:19-37; src/flask/blueprints.py:82-102

- **supported**：应用与蓝图保持默认缓存配置一致。两者读取 SEND_FILE_MAX_AGE_DEFAULT 并将 max_age 传入 send_from_directory。 依据：src/flask/blueprints.py:55-102; src/flask/app.py:281-328

## candidate-8026b9425b26 — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：correct；引用支持：supported；完整性：partial；拒答：not_applicable；一致性：consistent

合理承认未读实现；提示的额外 src 模块并不存在但以缺口提出。fileCount 为原阶段值，最终报告由 Harness 重算；不拿阶段计数当全仓计数。

- **supported**：轻量无依赖事件发布订阅，支持通配符与浏览器。README 明确描述；functional 在这里指方法不依赖 this，不代表没有状态修改。 依据：README.md:11-19

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **supported**：源码入口默认导出 mitt。入口声明明确；尚未分析整个实现。 依据：src/index.ts:46-50

## candidate-8cb2bcac3a41 — historical-qa

切换到 non-secure，它的负数长度行为有何不同？请读取该入口实现。

事实：partially_correct；引用支持：partial；完整性：adequate；拒答：not_applicable；一致性：consistent

入口切换正确，机制解释错误；引用也未包括初始化和 return。

- **supported**：non-secure nanoid(-10) 返回空串。i=-10，条件 i-- > 0 为假，返回初始空串。 依据：non-secure/index.js:19-29

- **contradicted**：size | 0 将负数变成 0。这里是 32 位整数转换，-10 不会变成 0；停止来自循环条件。 依据：non-secure/index.js:21-28

## candidate-919c81cdba20 — external-fixed-bm25

What is the relationship between the ASCII-only encoding control parameter in the framework's JSON serialization provider and Unicode character representation in serialized output?

事实：correct；引用支持：supported；完整性：partial；拒答：not_applicable；一致性：consistent

回答正确但未解释 False 保留 Unicode；可补充 dumps 的参数传递和测试。

- **supported**：ensure_ascii 默认 True，非 ASCII 字符转义。provider 注释和设置一致。 依据：src/flask/json/provider.py:144-148

## candidate-92c828a93f55 — external-fixed-bm25

What assumptions about encoding representation does the test_pragmas method enforce when validating that get_encodings_from_content correctly extracts and normalizes character encoding declarations from HTTP content headers?

事实：incorrect；引用支持：unsupported；完整性：insufficient；拒答：not_applicable；一致性：consistent

未说明保留原值；题目中的 HTTP headers/normalize 前提应纠正，不能顺着错误前提编结论。

- **supported**：支持 HTML meta、pragma 与 XML 声明形式。参数化样例展示这些正文字符串，每例恰好一个 UTF-8 结果。 依据：tests/test_utils.py:368-384

- **contradicted**：多个声明按在正文中出现的顺序返回。测试 XML 出现在最前，返回却是 HTML5、HTML4、XML；实现按 regex 类别拼接结果。 依据：tests/test_utils.py:386-392; src/requests/utils.py:493-501

- **unsupported**：test_pragmas 验证大小写不敏感。样例都使用 UTF-8，未混用大小写；XML regex 也未设 re.I。 依据：tests/test_utils.py:368-384; src/requests/utils.py:493-501

## candidate-951fff7fadfd — external-fixed-bm25

What is the semantic distinction between InvalidSchema's dual inheritance from RequestException and ValueError in handling URL scheme validation failures, and how do these two parent exception types differ in the context of schema validation?

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

偏题比较 MissingSchema；只粗略解释父类，缺 RequestException 的 IOError/请求元数据机制。

- **supported**：InvalidSchema 同属 RequestException 与 ValueError。双重继承声明明确，可按任一异常家族捕获。 依据：src/requests/exceptions.py:99-108

- **contradicted**：http:// 缺 host 属于 InvalidSchema。测试和 prepare_url 都给出 InvalidURL。 依据：tests/test_requests.py:97-111; src/requests/models.py:437-444

## candidate-986bd5764557 — external-sdk-agent

What is the modular component registration architecture that separates static asset serving from the main application framework while preserving consistent caching behavior across both components?

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

模块化方向正确，缓存依据缺失。

- **supported**：Blueprint 将静态资源服务模块化。蓝图提供静态文件，独立 static_folder 与路由。 依据：docs/blueprints.rst:19-37; src/flask/blueprints.py:82-102

- **unsupported**：static_url_path/static_folder 实现统一缓存。这两个属性控制地址和目录，不控制缓存；引用的 make_setup_state 也没有缓存逻辑。 依据：src/flask/sansio/scaffold.py:248-262; src/flask/blueprints.py:55-102

## candidate-bb9f1fdfa186 — historical-qa

Node nanoid 负数长度如何处理，test/index.test.js 如何验证？

事实：correct；引用支持：supported；完整性：adequate；拒答：not_applicable；一致性：consistent

限定题中 Node 默认入口与 -10 整数；不外推为任意负小数/超范围整数的转换规律。

- **supported**：Node 默认 nanoid(-10) 抛出 RangeError。默认导出绑定 customAlphabet；返回函数在 136–137 行转换并检查，测试明确断言 -10。 依据：index.js:133-138; index.js:177-177; test/index.test.js:87-93

## candidate-bcd24e25592b — external-sdk-agent

What is the semantic significance of the `no_proxy` keyword argument in `get_environ_proxies` overriding environment variable precedence, and what does returning a non-empty dictionary when this override is applied indicate?

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

只读包装函数，没追到 should_bypass_proxies；这是可定位的跨文件/跨函数补查缺口。

- **supported**：非空代理字典表示未绕过代理且存在代理配置。绕过时返回 {}，否则返回 getproxies，测试配置了 http_proxy。 依据：src/requests/utils.py:816-825; tests/test_utils.py:267-272

- **contradicted**：no_proxy 参数不覆盖环境变量优先级。混淆 no_proxy 与 HTTP_PROXY；子函数明确检查参数并临时覆写 no_proxy。 依据：src/requests/utils.py:769-771; src/requests/utils.py:803-813

## candidate-c34b3ccc78a2 — historical-qa

那么它是返回空字符串还是抛错？请沿用刚才的入口，并说明实现依据。

事实：partially_correct；引用支持：partial；完整性：adequate；拒答：not_applicable；一致性：consistent

结论得到测试支持；实现行号错误，不能只按结果对错判为完全正确。

- **supported**：Node 默认 nanoid(-10) 抛出 RangeError。默认导出绑定 customAlphabet；返回函数在 136–137 行转换并检查，测试明确断言 -10。 依据：index.js:133-138; index.js:177-177; test/index.test.js:87-93

- **contradicted**：负数判断位于引用的 133–135 行。133–135 只有函数开头和注释；真正判断在 136–137，模型最终上下文没有保留这两行。 依据：index.js:133-138

## candidate-c74fd63d0315 — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：consistent

来源共享补充了配置，未解决依赖边错误；应区分读到 emit 注释与读到其实现。

- **supported**：Map 存储、on/off 注册注销、emit 触发。实现吻合；emit 正文在 103–121，引用 46–94 仅覆盖部分，不意味着所有文字得到其引用支持。 依据：src/index.ts:46-73; src/index.ts:82-121

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **contradicted**：src/index.ts 静态导入 package.json。固定实现没有该导入。 依据：src/index.ts:1-123

- **supported**：通配符在类型匹配处理器后执行。注释 95 行和实现第二段吻合。 依据：src/index.ts:94-95; src/index.ts:103-121

## candidate-deefaac4dc5a — historical-report

mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。

事实：partially_correct；引用支持：partial；完整性：partial；拒答：not_applicable；一致性：contradictory

保留 report_grounding；examinedFiles 写了未观察的 src 且缺口称没读。goodFirstIssues 是建议，不是验证过的上游 issue。

- **supported**：npm 安装及模块/UMD 引入。README 给出对应示例。 依据：README.md:32-52

- **supported**：TypeScript 源码、microbundle 构建、Mocha/tsc/lint 脚本。package 明确列出入口及脚本；只确认声明，未实际安装或执行。 依据：package.json:5-27

- **unsupported**：需手动配置 TypeScript 编译器。作为必须完成的开发前置条件缺乏检查；现有脚本已提供检查流程。 依据：package.json:18-27

## candidate-e1893f9abd77 — external-sdk-agent

What is the purpose of the registration tracking flag in the blueprint collection class that prevents setup methods from being called after the blueprint has been registered once?

事实：unverifiable；引用支持：not_applicable；完整性：insufficient；拒答：appropriate；一致性：consistent

拒答与已见上下文相称，但任务未解决，属于检索失败，不可算与正确答案等价。

- **unverifiable**：当前工具上下文不足以解释注册标志。实际只读到示例 app 初始化，未检索目标源码；仓库本身有答案。 依据：无可核实的外部事实来源

## candidate-f907a71c0822 — historical-qa

get_jobs() 不传 tag 返回内部列表本身吗？

事实：partially_correct；引用支持：partial；完整性：adequate；拒答：not_applicable；一致性：contradictory

两种策略相同的生成矛盾；有源码、格式正确也不能判完全正确。

- **supported**：无 tag 时返回 self.jobs 的浅拷贝。self.jobs[:] 返回新列表。 依据：schedule/__init__.py:122-133

- **contradicted**：首句是的肯定了返回内部列表本身。与随后而非内部列表本身矛盾。 依据：schedule/__init__.py:130-131
