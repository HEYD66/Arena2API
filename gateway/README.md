# 网关本体

这里是网关源码的唯一维护位置。

- `server.py`：HTTP / OpenAI 兼容接口。
- `source_identity.py`、`source_router.py`：来源身份、账号隔离和固定别名路由。
- `conversation_bridge.py`、`conversation_catalog.py`、`conversation_stream.py`：命令、历史、队列、取消和增量接收。
- `client_attachments.py`：客户端内联附件校验与分块数据定义。
- `openai_compat.py`、`send_content.py`：客户端协议及本轮内容选择。
- `tool_prompt.py`、`tool_audit.py`、`tool_reference.py`：工具协议、审计及可选文档参考。
- `gateway_log.py`：脱敏事件日志。

根目录同名 `.py` 文件仅为兼容转发。`import server` 和 `import gateway.server` 指向同一个模块对象，避免路由注册、全局队列和测试替身产生两份状态。

项目资源仍定位到项目根目录：不创建 `gateway/.console`，不迁移绑定、API Key、日志或工具清单。可从根目录运行 `python -m gateway`，旧 `python server.py` 和 `uvicorn server:app` 继续可用。
