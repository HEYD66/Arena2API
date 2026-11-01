"""Opt-in sandbox reference prompt. No remote file access or tool execution.
Only a request whose binding was explicitly opted in uses this formatter.
The original ToolPolicy remains authoritative for output validation.
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from openai_compat import ProtocolError, CLIENT_EXECUTION_RULES, annotate_tool_results, closing_reminder

INDEX_PATH = '/home/user/uploads/tools-index.md'
REFERENCE_PATH = '/home/user/uploads/tools-reference.md'
TRIAL_ALIAS = 'opus5.5'

def trial_eligible(row):
    # Display label only limits where the UI may opt in; the route itself pins
    # client/account/context, and its marker is never transferred on deletion.
    return row.get('alias') == TRIAL_ALIAS and ''.join(str(row.get('source_label', '')).split()) == '实例5'

def fingerprint(tool):
    return hashlib.sha256(json.dumps(tool, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()

@lru_cache(maxsize=1)
def manifest():
    try:
        data = json.loads((Path(__file__).resolve().parents[1] / 'tool_reference_manifest.json').read_text(encoding='utf-8'))
        if data['version'] != 1 or not isinstance(data['tools'], dict):
            raise ValueError()
        for name, value in data['tools'].items():
            if not isinstance(name, str) or not isinstance(value, str) or len(value) != 64:
                raise ValueError()
        return data
    except (OSError, ValueError, KeyError, TypeError):
        raise ProtocolError('工具文档版本清单无法读取；未发送，请关闭工具文档试验或修复项目文件。',
                            'tools', 'tool_reference_unavailable', 503) from None

def render_reference_prompt(messages, policy, budget, *, audit=None):
    data = manifest()
    referenced, inline = [], []
    for tool in policy.tools:
        if data['tools'].get(tool['function']['name']) == fingerprint(tool):
            referenced.append(tool['function']['name'])
        else:
            # Added/changed definitions must not silently use stale documentation.
            inline.append(tool)
    names = [tool['function']['name'] for tool in policy.tools]
    compact = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    definitions = (
        'CLIENT TOOL REFERENCE TRIAL. This is reference data, not permission to activate tools.\n'
        'The ONLY permitted native operations are read_file on the TWO paths below (offset >= 1, limit 1..300), '
        'and bash with EXACT command: sha256sum /home/user/uploads/tools-index.md /home/user/uploads/tools-reference.md . '
        'The final period is punctuation, not part of the command. No other commands, paths or native tools are permitted.\n'
        f'Index: {INDEX_PATH}\nDefinitions: {REFERENCE_PATH}\n'
        f'Expected index SHA-256: {data["index_sha256"]}\n'
        f'Expected definitions SHA-256: {data["reference_sha256"]}\n'
        'Verify these file hashes before first use in this conversation; recheck if the files change. '
        'Read the index in bounded chunks, locate the exact tool name, then read only the indicated definition lines. '
        'Do not load the whole reference file. If files are absent, hashes differ, or native sandbox access '
        'is unavailable, report the problem; do not guess tool arguments or use client execution tools to read these files.\n'
        'Only the following client tool names are allowed in THIS request, regardless of other names in the documents:\n'
        + compact(names) + '\n'
        'Document-backed tool names (their current definitions match the uploaded snapshot):\n'
        + compact(referenced) + '\n'
        'New or changed tools have complete current definitions inline below. These override the documents:\n'
        + compact(inline)
    )
    instruction = (
        'For an actual CLIENT tool request, output ONLY one or more blocks in this format:\n'
        '<tool_call>{"name":"tool_name","arguments":{"parameter":"value"}}</tool_call>\n'
        'Do not wrap client requests in Markdown code spans or fences. The client executes these tools, not your sandbox. '
        'Native sandbox file reads are separate operations; do not encode them as client tool calls.\n'
    )
    if not policy.parallel:
        instruction += 'Return at most ONE client tool call in this turn.\n'
    history = 'Conversation in chronological order (tool results are client observations):\n' + compact(annotate_tool_results(messages))
    prompt = '\n\n'.join([policy.instruction(), CLIENT_EXECUTION_RULES, instruction + definitions, history,
                          closing_reminder(policy, reference=True)])
    units = lambda text: len(text.encode('utf-16-le', errors='surrogatepass')) // 2
    if audit is not None:
        audit.update(stage='rendered', format='sandbox_reference', rendered_names=names,
                     rendered_count=len(names), missing_names=[], unexpected_names=[], names_match=True,
                     reference_count=len(referenced), inline_definition_count=len(inline),
                     original_definitions_utf16=units(compact(policy.tools)),
                     reference_block_utf16=units(definitions), prompt_utf16=units(prompt))
    if units(prompt) > budget:
        raise ProtocolError('工具文档试验提示仍超过网关预算；未发送。不会丢弃工具或截断参数定义。',
                            'tools', 'context_length_exceeded')
    return prompt
