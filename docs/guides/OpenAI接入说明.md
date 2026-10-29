# OpenAI兼容接入：DSH优先测试版

日期：2026-10-17。范围：OpenAI **Chat Completions** 文本与function tools，不是完整OpenAI协议，不需要MCP或E2B。

## 1. 启动

工作目录：解压后的便携包根目录。

1. 已安装Python 3.11或更高版本。
2. 运行本目录 `run-local.cmd`。它仅在本项目`.venv`中创建Python环境、缺依赖时安装requirements，然后启动本地服务，不安装/修改DSH或VS Code。
3. 在Chrome扩展管理页开启开发者模式，加载本目录的 `extension\chrome`（解压的源码目录）。不要使用仓库里的旧zip代替当前目录。保持已登录的Arena标签可用。
4. 浏览器扩展的服务器地址指向 `http://127.0.0.1:9090`。等待扩展连接；首次可能需要在Arena页面正常操作以获取模型目录/会话环境。项目不保证所有账户/模型均可用。
5. 启动窗口按Ctrl+C停止。端口占用请先检查已有进程，不要盲目启动多份。

启动脚本仅设置本进程参数：loopback监听，API_KEY=arena-local（本地测试值），严格浏览器中继，关闭自动改名、会话推断复用、首条提示压缩、正文日志及UI采样。不更改settings.json或系统环境变量。鉴权加固按用户决定后置，本版不用于公网。

## 2. DSH或其他客户端填写

| 字段 | 值 |
| --- | --- |
| Provider/API类型 | OpenAI兼容，Chat Completions |
| Base URL | `http://127.0.0.1:9090/v1` |
| API Key | `arena-local`（使用本启动脚本时） |
| Model | 取 `/v1/models` 返回的具体模型ID，不填占位模型名 |
| Tools/function calling | 开启，使用客户端自带的文件/终端工具 |
| Strict schema | 关闭，本版不支持strict=true |
| 自动重试 | 测试时建议关闭，避免提交结果未知时客户端自动重发 |

有的客户端要求完整endpoint而不是base URL，那应填写`http://127.0.0.1:9090/v1/chat/completions`，不要拼出重复的`/v1`。

本轮未读取/修改DSH本机配置，未确定其界面的具体字段名称，也未做DSH真实联调。以上是标准接口参数，不是假定DSH某个版本的配置文件格式。若DSH只支持Responses API，需要额外适配；本版没有`/v1/responses`。

模型列表检查（PowerShell）：

```powershell
Invoke-RestMethod 'http://127.0.0.1:9090/v1/models' -Headers @{Authorization='Bearer arena-local'}
```

## 3. 已实现

- `/v1/models`沿用上游模型列表；`/v1/chat/completions`支持流式和非流式文本、标准function tools及多轮tool结果。
- 支持`tool_choice=auto/none/required`及指定function；模型未履行required/指定调用时明确报错，不假造工具调用。
- 支持多个调用、调用ID关联、乱序工具结果；`parallel_tool_calls=false`时拒绝多调用输出。
- 工具参数要求合法JSON对象；无效参数不静默替换为`{}`。本版不是完整JSON Schema验证器，客户端仍负责参数语义校验与执行授权。
- 工具只从显式`<tool_call>...</tool_call>`块转换，普通json示例代码块不自动执行。模型输出不是工具执行，执行发生在DSH/客户端。
- 工具轮保留完整schema、system/developer消息、历史调用ID与结果；超出100000字符默认预算时返回`context_length_exceeded`，不切断历史配对。
- 带tools或工具历史的请求使用完整历史、每次新建上游evaluation，不按首条用户文字猜测并复用会话。代价是Arena会话数量和上下文传输可能增加，不宣称复用了原生Agent会话。
- SSE工具调用含index/id/function及`finish_reason=tool_calls`；收到完整上游结束标志后才交付工具调用。断流/截断调用不交付执行。
- 请求错误采用OpenAI式error对象；流开始后的协议错误作为SSE `data: {"error": ...}`并以`[DONE]`结束。流开启后HTTP状态通常已是200，客户端需识别流内error。
- 浏览器POST等非幂等请求等待响应头超时不自动重投；取消等待时清理待处理请求。此调整不代表上游所有403/429历史重试分支都已重构。
- 启动脚本启用`ARENA_REQUIRE_BROWSER=1`，中继不可用不悄悄改为httpx直连。

## 4. 当前限制

- 有工具时先缓冲模型正文，再交付确定的工具调用或普通文字；不承诺工具参数逐token输出，首段可见输出可能较晚。
- 只接受文本内容和text parts；图像/音频/文件输入、legacy functions/function_call、n>1、结构化response_format、strict schema不在本版支持范围。
- 工具模拟依赖模型遵循约定；不遵循时会报错，不保证任意模型都能稳定编码。
- 模型未提供的usage不等于真实0成本；上游非流式可能以0占位，流式未新增include_usage末尾统计块。不要用本接口的占位值计算实际扣费。
- temperature、max_tokens等高级参数继承上游处理，不能承诺等同原生OpenAI语义；未实现Responses API。
- 无工具的旧会话推断路径仍保留在源码；推荐脚本关闭推断和首条压缩，直接运行`python server.py`与脚本的配置不同。
- 上游VS Code专用provider仍未修其纯工具判空问题；本轮面向DSH/通用OpenAI客户端，不使用该provider验收。
- 浏览器和登录态必须保持可用；403/429、上游协议变更、账户限制可能阻止调用，不能保证浏览器方式绕过这些限制。

## 5. 用户实测顺序

1. 获取模型列表，选择一个具体文本模型，先发一句短消息。
2. 在DSH中选择一个专用测试目录，让它读取你创建的无敏感测试文件。
3. 让它修改该目录内一个测试文件，并运行一个无副作用的小测试。
4. 检查是否能连续“工具调用→工具结果→继续回答”，再测试多工具和取消。
5. 若报错，提供脱敏错误码/报错文字及DSH版本，不提供Cookie、token或完整私密对话。超时显示结果未知时先确认实际状态，不重复发送。

## 6. 离线验证

本轮新增`tests/test_openai_compat.py`，使用ASGI传输和假的上游流，禁止真实socket连接；覆盖请求校验、模型列表、两轮工具闭环、流式/非流式、完整提示保留、预算、工具选择、并行策略、错误、EOF、超时和取消。

```powershell
.\.venv\Scripts\python.exe tests\test_openai_compat.py
```

沙箱验证：40/40新增测试通过；原有`test_sessions.py`和`test_e2e_mock_arena.py`退出码0；OpenAI Python SDK 3.31.0实际解析非流式工具调用并接续流式回复通过（ASGI模拟，网络禁止）。这些不是DSH/真实Arena账号验收。上游部分测试将临时文件写到系统临时目录，因此本机本轮只运行本项目新增隔离测试，不在其他目录运行旧回归。
