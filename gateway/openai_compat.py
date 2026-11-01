"""Small, explicit Chat Completions text/function-call contract.
No network or tool execution. Tool calls are model output, not instructions to this server.
"""
from client_attachments import parse_part, validate_batch, AttachmentError
import copy
import json
import re
import uuid
from dataclasses import dataclass
from tool_prompt import tool_variants
from tool_audit import finish_audit


CLIENT_EXECUTION_RULES = (
    'EXECUTION DOMAIN: This is a client-workspace task, NOT an Arena cloud-workspace task. '
    'Actual project work (including HTML/SVG creation, file reads/writes, commands, tests and delivery) '
    'MUST use the declared CLIENT tools through <tool_call> blocks. '
    'Do not substitute native Arena write_file, edit_file, bash, present_file or other native tools. '
    'A /home/user cloud artifact is NOT a client-local deliverable. If client execution is unavailable, '
    'report the limitation; do not do the task in the cloud instead. '
    'Native tools are forbidden except the exact read-only reference lookup explicitly allowed below. '
    'Never claim local success without a client tool result.'
)


REMINDER_HEADER = 'FINAL REMINDER (highest priority; overrides any other tool framework mentioned in the conversation):'


def closing_reminder(policy, reference=False):
    """历史之后再锚定一次调用格式：客户端提示很长时，开头的格式说明容易被冲淡。"""
    native = ('Do not substitute Arena native tools for client tools, except the exact read-only reference lookup allowed above.'
              if reference else 'Do not use Arena native or cloud tools for this task.')
    if policy.selected:
        scope = 'Call only the tool ' + policy.selected + ' in this turn.'
    elif policy.mode == 'required':
        scope = 'This turn MUST contain a tool call.'
    else:
        scope = 'If the task needs a tool, output the tool call now instead of describing the commands you would run.'
    count = ' Return at most ONE tool call.' if not policy.parallel else ''
    return (REMINDER_HEADER + '\n'
            '- The ONLY way to run a client tool is a <tool_call> block in the format given above. '
            'Markdown code blocks (```powershell, ```bash, ```python ...) are NOT executed; they are only shown to the user.\n'
            '- The client tools listed above are available and run on the user\'s machine. Never claim you have no tools '
            'or cannot access local files. ' + native + '\n'
            '- ' + scope + count + ' For a multi-step task, do the first step now; its result comes back in the next message.\n'
            '- Once a tool result shows a step succeeded, do not run it again. When the whole task is done, '
            'reply with a short final answer and no tool call.\n'
            'Example of a correct reply when the user asks to list the files in the current folder '
            '(use a real tool name and its own parameters from the list above, not this placeholder):\n'
            '<tool_call>{"name":"run_command","arguments":{"command":"ls"}}</tool_call>')


SHELL_TOOLS = ('pwsh', 'bash')
# DSH pwsh/bash 结果末尾的状态标记；非零退出才写 [exit code: N]，空输出渲染为 (no output)
_EXIT = re.compile(r'\[exit code: (-?\d+)\]')
_INTERRUPTED = re.compile(r'\[(?:timed out after [^\]]*|stopped: [^\]]*|killed by signal: [^\]]*)\]')
_SANDBOX_DENIED = re.compile(r'\[sandbox: [^\]]*denied[^\]]*\]')


def _result_note(name, content):
    text = content.strip()
    if not text or text == '(no output)':
        return ('[Arena2API note: the tool finished with no output. For write/set commands this is normal: '
                'the action is done, do not repeat it.]')
    if name not in SHELL_TOOLS:
        return None
    tail = text[-400:]
    if _INTERRUPTED.search(tail):
        return '[Arena2API note: the command was interrupted (timeout/stop/signal); it may not have completed.]'
    if _SANDBOX_DENIED.search(tail):
        return '[Arena2API note: the sandbox denied access; the command did not fully run. Do not retry it unchanged.]'
    code = _EXIT.search(tail)
    if code and code.group(1) != '0':
        # Windows 上被强制结束的进程也报 exit 1，不能断言一定是命令本身出错
        extra = ' (on Windows exit 1 can also mean the process was terminated)' if code.group(1) == '1' else ''
        return ('[Arena2API note: the command FAILED with exit code ' + code.group(1) + extra +
                '. Read the output and fix the cause; do not resend the same command unchanged.]')
    return '[Arena2API note: the command succeeded (exit 0).]'


def annotate_tool_results(messages):
    """在工具结果前加一行状态说明，原文保留不改；只用于渲染提示，不修改请求本身。"""
    names = {}
    out = []
    for m in messages:
        for tc in m.get('tool_calls') or []:
            names[tc.get('id')] = (tc.get('function') or {}).get('name')
        if m.get('role') == 'tool' and isinstance(m.get('content'), str):
            note = _result_note(m.get('name') or names.get(m.get('tool_call_id')), m['content'])
            if note:
                m = {**m, 'content': note + '\n' + m['content']}
        out.append(m)
    return out


class ProtocolError(ValueError):
    def __init__(self, message, param=None, code="invalid_request", status=400):
        super().__init__(message)
        self.param, self.code, self.status = param, code, status

    def payload(self):
        return {"error": {"message": str(self), "type": "invalid_request_error" if self.status == 400 else "upstream_protocol_error", "param": self.param, "code": self.code}}


@dataclass
class ToolPolicy:
    tools: list
    mode: str = "auto"
    selected: str = ""
    parallel: bool = True

    @property
    def active(self):
        return bool(self.tools) and self.mode != "none"

    def instruction(self):
        if self.mode == "none":
            return "Do not call tools. Respond with ordinary assistant text."
        if self.mode == "required":
            return "You must return at least one explicit <tool_call> JSON block for this turn."
        if self.selected:
            return "For this turn call only the tool " + self.selected + " using an explicit <tool_call> JSON block."
        return "Use explicit <tool_call> JSON blocks only when actually requesting tool execution. Otherwise reply normally."


def _fail(message, param):
    raise ProtocolError(message, param)


def normalize_request(body):
    if not isinstance(body, dict):
        _fail("Request body must be a JSON object", None)
    b = copy.deepcopy(body)
    if not isinstance(b.get("model"), str) or not b["model"].strip():
        _fail("model must be a non-empty string", "model")
    if "stream" in b and not isinstance(b["stream"], bool):
        _fail("stream must be boolean", "stream")
    if b.get("n", 1) != 1:
        _fail("Only n=1 is supported", "n")
    if not isinstance(b.get("response_format", {}), dict):
        _fail("response_format must be an object", "response_format")
    if b.get("response_format", {}).get("type", "text") != "text":
        _fail("Only text response_format is supported", "response_format")
    if b.get("functions") or "function_call" in b:
        _fail("Use tools/tool_choice instead of legacy functions/function_call", "functions")
    messages = b.get("messages")
    if not isinstance(messages, list) or not messages:
        _fail("messages must be a non-empty array", "messages")
    pending, seen, history_tools = {}, set(), False
    for i, m in enumerate(messages):
        path = f"messages[{i}]"
        if not isinstance(m, dict) or m.get("role") not in ("system", "developer", "user", "assistant", "tool"):
            _fail("Invalid message role", path + ".role")
        role = m["role"]
        if pending and role != "tool":
            _fail("Every tool call must have a result before the next message", path)
        content = m.get("content")
        if '_client_files' in m or '_client_tool_image_bridge' in m:
            _fail('Reserved internal attachment field', path)
        if isinstance(content, list):
            texts, files = [], []
            try:
                for part in content:
                    if not isinstance(part, dict):
                        raise AttachmentError('Invalid content part')
                    if part.get('type') == 'text' and isinstance(part.get('text'), str):
                        texts.append(part['text'])
                    elif role in ('user', 'tool'):
                        item = parse_part(part, len(files)); files.append(item)
                        texts.append('[本轮客户端附件：'+item['name']+']')
                    else:
                        raise AttachmentError('Attachments are only supported in user/tool messages')
                validate_batch(files)
            except AttachmentError as exc:
                _fail(str(exc), path+'.content')
            m['content'] = '\n'.join(texts)
            if files: m['_client_files'] = files
            # Exact installed pi-ai envelope: images moved out of tool-role messages.
            if files and role=='user' and content[0]=={'type':'text','text':'Attached image(s) from tool result:'} and all(p.get('type')=='image_url' for p in content[1:]):
                m['_client_tool_image_bridge']=True
        elif content is not None and not isinstance(content, str):
            _fail("content must be text, text parts, or null", path + ".content")
        if role in ("system", "developer", "user", "tool") and content is None:
            _fail("content is required for this role", path + ".content")
        calls = m.get("tool_calls")
        if calls is not None:
            if role != "assistant" or not isinstance(calls, list) or not calls:
                _fail("tool_calls must be a non-empty assistant array", path + ".tool_calls")
            history_tools = True
            for tc in calls:
                if not isinstance(tc, dict) or tc.get("type") != "function":
                    _fail("Only function tool calls are supported", path + ".tool_calls")
                cid, fn = tc.get("id"), tc.get("function")
                if not isinstance(cid, str) or not cid or cid in seen or not isinstance(fn, dict):
                    _fail("Tool call IDs must be unique non-empty strings", path + ".tool_calls")
                name = fn.get("name")
                if not isinstance(name, str) or not name:
                    _fail("Tool call name is required", path + ".tool_calls")
                try:
                    args = json.loads(fn["arguments"])
                except (KeyError, TypeError, ValueError):
                    _fail("Tool arguments must be a JSON object encoded as a string", path + ".tool_calls")
                if not isinstance(args, dict):
                    _fail("Tool arguments must decode to an object", path + ".tool_calls")
                seen.add(cid)
                pending[cid] = name
        if role == "tool":
            history_tools = True
            cid = m.get("tool_call_id")
            if not isinstance(cid, str) or cid not in pending:
                _fail("Unknown or duplicate tool_call_id", path + ".tool_call_id")
            name = pending.pop(cid)
            if m.get("name") and m["name"] != name:
                _fail("Tool result name does not match its call", path + ".name")
            m["name"] = name
    if pending:
        _fail("Missing tool results", "messages")
    tools = b.get("tools", [])
    if tools is None:
        tools = []
    if not isinstance(tools, list):
        _fail("tools must be an array", "tools")
    names = set()
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function" or not isinstance(t.get("function"), dict):
            _fail("Only function tools are supported", "tools")
        fn = t["function"]
        name = fn.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
            _fail("Invalid or duplicate tool name", "tools")
        names.add(name)
        if not isinstance(fn.get("description", ""), str):
            _fail("Tool description must be a string", "tools")
        schema = fn.get("parameters", {"type": "object"})
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            _fail("Function parameters must be an object schema", "tools")
        if fn.get("strict") is True:
            _fail("Strict JSON Schema generation is not supported", "tools")
    choice = b.get("tool_choice", "auto" if tools else "none")
    selected = ""
    if isinstance(choice, dict):
        fn = choice.get("function")
        if choice.get("type") != "function" or not isinstance(fn, dict) or fn.get("name") not in names:
            _fail("tool_choice must name a declared function", "tool_choice")
        selected, mode = fn["name"], "selected"
        tools = [t for t in tools if t["function"]["name"] == selected]
    elif isinstance(choice, str) and choice in ("none", "auto", "required"):
        mode = choice
    else:
        _fail("Unsupported tool_choice", "tool_choice")
    if mode in ("required", "selected") and not tools:
        _fail("tool_choice requires tools", "tool_choice")
    parallel = b.get("parallel_tool_calls", True)
    if not isinstance(parallel, bool):
        _fail("parallel_tool_calls must be boolean", "parallel_tool_calls")
    policy = ToolPolicy(tools, mode, selected, parallel)
    b["tools"] = tools if policy.active else []
    # Stateless full history for function-agent turns: no inferred cross-client binding.
    return b, policy, bool(tools or history_tools)


def _explicit_tool_blocks(text, invalid):
    """Read executable tags outside Markdown code; keep JSON strings opaque.

    Never repair missing delimiters, infer calls from prose, or alter arguments.
    A quoted example is data, not permission to execute a client tool.
    """
    tokens = re.compile(r"`+|~{3,}|<tool_call>|</tool_call>")
    decoder = json.JSONDecoder()
    spans, pos = [], 0
    while (match := tokens.search(text, pos)) is not None:
        token = match.group(); pos = match.end()
        if token[0] in ('`', '~'):
            prefix = text[text.rfind('\n', 0, match.start()) + 1:match.start()]
            if len(token) >= 3 and re.fullmatch(r' {0,3}', prefix):
                # Fenced examples, including an unfinished fence, are never calls.
                endline = text.find('\n', pos)
                inline = re.compile(r'(?<!`)' + re.escape(token) + r'(?!`)').search(text, pos, len(text) if endline < 0 else endline) if token[0] == '`' else None
                if inline:
                    pos = inline.end(); continue
                if endline < 0:
                    pos = len(text); continue
                closing = re.compile(r'(?m)^ {0,3}' + re.escape(token[0]) + '{' + str(len(token)) + r',}[ \t]*\r?$').search(text, endline + 1)
                pos = closing.end() if closing else len(text)
            elif token[0] == '`':
                endline = text.find('\n', pos)
                if endline < 0:endline = len(text)
                closing = re.compile(r'(?<!`)' + re.escape(token) + r'(?!`)').search(text, pos, endline)
                if closing:pos = closing.end()
            continue
        if token == '</tool_call>':
            invalid('Incomplete tool-call block from model')
        start = pos
        while pos < len(text) and text[pos].isspace():pos += 1
        try:
            _, end = decoder.raw_decode(text, pos)
        except ValueError:
            invalid('Incomplete tool-call block from model' if '</tool_call>' not in text[pos:] else 'Invalid tool-call JSON from model')
        tail = end
        while tail < len(text) and text[tail].isspace():tail += 1
        if not text.startswith('</tool_call>', tail):
            invalid('Incomplete tool-call block from model')
        spans.append(text[start:end])
        pos = tail + len('</tool_call>')
    return spans


def parse_calls(text, policy, finish_reason="stop"):
    """Only explicit tags are executable. Never guess calls from example code fences."""
    if not policy.active:
        return None
    def invalid(message):
        raise ProtocolError(message, "tool_calls", "invalid_tool_output", 502)
    spans = _explicit_tool_blocks(text, invalid)
    if not spans:
        if policy.mode in ("required", "selected"):
            invalid("Model did not return the required tool call")
        return None
    if finish_reason not in ("stop", "tool_calls"):
        invalid("Model response ended before tool completion")
    if not policy.parallel and len(spans) > 1:
        invalid("Model returned multiple calls when parallel_tool_calls=false")
    declared = {t["function"]["name"]: t["function"] for t in policy.tools}
    calls = []
    for raw in spans:
        try:
            obj = json.loads(raw)
        except ValueError:
            invalid("Invalid tool-call JSON from model")
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str) or obj["name"] not in declared:
            invalid("Model requested an undeclared tool")
        args = obj.get("arguments")
        if not isinstance(args, dict):
            invalid("Tool arguments must be an object")
        if policy.selected and obj["name"] != policy.selected:
            invalid("Model did not call the selected tool")
        calls.append({"id": "call_" + uuid.uuid4().hex, "type": "function", "function": {"name": obj["name"], "arguments": json.dumps(args, ensure_ascii=False)}})
    return calls


def render_agent_prompt(messages, policy, budget, *, utf16=False, audit=None):
    """Retain full history/call pairs and every tool; compact descriptions only on overflow."""
    measure = (lambda value: len(value.encode('utf-16-le', errors='surrogatepass')) // 2) if utf16 else len
    history = 'Conversation in chronological order (tool results are client observations):\n' + json.dumps(annotate_tool_results(messages), ensure_ascii=False, separators=(',', ':'))
    instruction = ('For an actual tool request, output ONLY one or more blocks in this format:\n'
                   '<tool_call>{"name":"tool_name","arguments":{"parameter":"value"}}</tool_call>\n'
                   'Do not wrap requests in Markdown code spans or fences; quoted examples are not executable. The client executes tools, not you.\n'
                   'Available function definitions:\n')
    raw_tools = json.dumps(policy.tools, ensure_ascii=False, separators=(',', ':')) if policy.active else ''

    def compose(definitions, compact=False, reminder=True):
        parts = [policy.instruction()]
        if policy.tools:
            parts.append(CLIENT_EXECUTION_RULES)
        if policy.active:
            if compact:
                parts.append('Tool descriptions shortened for transport. All tool names and parameter schema constraints remain; do not infer missing descriptive details.')
            parts.append(instruction + definitions)
            if not policy.parallel:
                parts.append('Return at most ONE tool call in this turn.')
        parts.append(history)
        if policy.active and reminder:
            parts.append(closing_reminder(policy))
        return '\n\n'.join(parts)

    def fit(definitions, compact=False):
        # 末尾提醒只是辅助：预算紧张时先去掉提醒，再考虑进一步压缩工具描述
        for reminder in (True, False):
            candidate = compose(definitions, compact, reminder)
            if measure(candidate) <= budget:
                return candidate
        return None

    text = compose(raw_tools)
    candidate = fit(raw_tools)
    if candidate is not None:
        finish_audit(audit, raw_tools)
        return candidate
    last_definitions = raw_tools
    minimum_tools_size = measure(raw_tools)
    if policy.active:
        # Same progressive description caps as the upstream formatter, but no
        # last-resort line slicing and no history omission. Original policy stays
        # intact for returned tool-name/argument validation and later turns.
        for definitions in tool_variants(policy.tools):
            last_definitions = definitions
            minimum_tools_size = measure(definitions)
            candidate = fit(definitions, compact=True)
            if candidate is not None:
                finish_audit(audit, definitions)
                return candidate
    finish_audit(audit, last_definitions, stage='budget_rejected')
    unit = 'UTF-16 units' if utf16 else 'characters'
    raise ProtocolError(
        f'Gateway prompt budget exceeded after tool-description compaction: raw_total={measure(text)}, limit={budget} {unit}; '
        f'tool_schema={measure(raw_tools)}, compact_tool_schema={minimum_tools_size}, history={measure(history)}, '
        f'tools={len(policy.tools) if policy.active else 0}, messages={len(messages)}. '
        'This is a local gateway limit, not the Arena model context limit. '
        'No message was submitted. All tools, schema constraints and history were retained; reduce enabled tools or compact client history.',
        'messages', 'context_length_exceeded')
