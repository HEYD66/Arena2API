/* Native ask_user contract adapter. Pure data validation; no network, DOM or tool execution. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ArenaFusionTools = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const HITL = new Set(['ask_user', 'add_voice', 'generate_image', 'propose_plan']);
  class ToolError extends Error {
    constructor(code, message) { super(message); this.name = 'ToolProtocolError'; this.code = code; }
  }
  const error = (code, text) => new ToolError(code, text);
  const str = (v, max = 12000) => typeof v === 'string' && !!v.trim() && v.length <= max;
  function toolName(part) {
    if (!part || typeof part !== 'object') return null;
    if (typeof part.type === 'string' && part.type.startsWith('tool-')) { const n = part.type.slice(5); return HITL.has(n) ? n : null; }
    if (part.type === 'dynamic-tool' && ['arena__ask_user', 'arena__add_voice', 'arena__generate_image'].includes(part.toolName)) return part.toolName.slice(7);
    return null;
  }
  const isAskEvent = name => name === 'ask_user' || name === 'arena__ask_user';
  function questions(input) {
    const qs = input && input.questions;
    if (!Array.isArray(qs) || qs.length < 1 || qs.length > 6) throw error('TOOL_SCHEMA', '选项参数超出已核对的 ask_user 范围，请在主页面处理。');
    const qids = new Set(); let size = 0;
    const normalized = qs.map(q => {
      if (!q || !str(q.id, 512) || qids.has(q.id) || !str(q.question) || !Array.isArray(q.options) || q.options.length < 2 || q.options.length > 6 || (q.allowCustomResponse !== undefined && typeof q.allowCustomResponse !== 'boolean')) throw error('TOOL_SCHEMA', '问题或选项格式不符，请在主页面处理。');
      qids.add(q.id); const ids = new Set();
      const options = q.options.map(o => {
        if (!o || !str(o.id, 512) || ids.has(o.id) || !str(o.label, 4000) || (o.description !== undefined && (typeof o.description !== 'string' || o.description.length > 12000))) throw error('TOOL_SCHEMA', '选项标识重复或内容格式不符，请在主页面处理。');
        ids.add(o.id); return { id: o.id, label: o.label, description: o.description || '' };
      });
      const n = { id: q.id, question: q.question, options, allowCustomResponse: q.allowCustomResponse !== false };
      size += JSON.stringify(n).length; return n;
    });
    if (size > 128000) throw error('TOOL_SCHEMA', '选项内容过长，请在主页面查看完整问题。');
    return normalized;
  }
  const fingerprint = qs => JSON.stringify(qs);
  function response(qs, value) {
    if (!value || typeof value.skipped !== 'boolean' || !Array.isArray(value.answers)) throw error('TOOL_ANSWER', '选项回答格式不符。');
    if (value.skipped) {
      if (value.answers.length) throw error('TOOL_ANSWER', '跳过操作不能同时携带选项答案。');
      return { answers: [], skipped: true };
    }
    if (value.answers.length !== qs.length) throw error('TOOL_INCOMPLETE', '请回答本组每个问题，或明确选择跳过。');
    const found = new Map();
    for (const a of value.answers) {
      if (!a || typeof a.questionId !== 'string' || found.has(a.questionId) || !qs.some(q => q.id === a.questionId)) throw error('TOOL_ANSWER', '答案包含重复或未知的问题。');
      found.set(a.questionId, a);
    }
    const answers = qs.map(q => {
      const a = found.get(q.id);
      if (typeof a.selectedOptionId === 'string' && a.customResponse === null && q.options.some(o => o.id === a.selectedOptionId)) return { questionId: q.id, selectedOptionId: a.selectedOptionId, customResponse: null };
      if (a.selectedOptionId === null && q.allowCustomResponse && str(a.customResponse, 2000)) return { questionId: q.id, selectedOptionId: null, customResponse: a.customResponse };
      throw error('TOOL_INCOMPLETE', '请选择有效选项，或填写允许的自定义回答（最多 2000 字符）。');
    });
    return { answers, skipped: false };
  }
  function partState(part) {
    if (part.output && typeof part.output === 'object' && part.output.status === 'awaiting_user_response') return 'pending';
    if (part.state === 'input-available') return 'pending';
    if (part.state === 'input-streaming') return 'loading';
    if (part.state === 'output-available' && part.output !== undefined) return 'answered';
    return 'unavailable';
  }
  function cards(message) {
    if (!message || message.role !== 'assistant' || !Array.isArray(message.parts)) return [];
    const nodeId = message.metadata?.nodeId ?? message.id;
    return message.parts.flatMap((part, index) => {
      if (toolName(part) !== 'ask_user' || part.providerExecuted === true) return [];
      const card = { index, nodeId, assistantId: message.id, toolCallId: part.toolCallId, state: partState(part), questions: [], fingerprint: '', error: '' };
      try {
        if (!str(part.toolCallId, 512)) throw error('TOOL_SCHEMA', '工具调用标识无效。');
        card.questions = questions(part.input); card.fingerprint = fingerprint(card.questions);
        if (card.state === 'answered') card.output = response(card.questions, part.output);
      } catch (e) { card.error = e.message; card.state = 'unavailable'; }
      return [card];
    });
  }
  function tailParts(message) {
    const parts = Array.isArray(message?.parts) ? message.parts : [];
    let start = -1; parts.forEach((p, i) => { if (p?.type === 'step-start') start = i; });
    return { parts: parts.slice(start + 1), start };
  }
  function hasPendingInteraction(messages) {
    const last = Array.isArray(messages) ? messages.at(-1) : null;
    if (last?.role !== 'assistant' || last.metadata?.feedback?.interruption === 'stop') return false;
    return tailParts(last).parts.some(p => p && p.providerExecuted !== true &&
      ((toolName(p) && ['pending', 'loading'].includes(partState(p))) || p.output?.status === 'awaiting_user_response'));
  }
  function boundary(snapshot) {
    const last = snapshot?.messages?.at(-1), contextKey = snapshot?.context?.key || '';
    if (last?.role !== 'assistant') return { contextKey, asks: [], pending: [], nodeId: null, canSubmit: false, reason: '当前没有等待回答的模型提问。' };
    const { parts, start } = tailParts(last), asks = cards(last).filter(c => c.index > start);
    const other = parts.some(p => p && p.providerExecuted !== true &&
      ((toolName(p) && toolName(p) !== 'ask_user' && ['input-streaming', 'input-available', 'output-available'].includes(p.state)) || (!toolName(p) && p.output?.status === 'awaiting_user_response')));
    const nodeId = last.metadata?.nodeId ?? last.id, pending = asks.filter(a => a.state === 'pending');
    let reason = '';
    if (last.metadata?.feedback?.interruption === 'stop') reason = '此轮已停止，请在主页面处理，不能回答历史选项。';
    else if (snapshot.streaming) reason = '当前轮仍在生成，完成后再读取选项。';
    else if (other) reason = '本轮还含语音、图片、计划或其它交互，请在主页面完成整组确认。';
    else if (!str(nodeId, 128) || !UUID.test(nodeId)) reason = '提问节点尚未确认，请先读取会话。';
    else if (asks.length > 8 || asks.some(c => c.error || ['loading', 'unavailable'].includes(c.state))) reason = '部分选项尚未完整或超出小窗支持范围，请读取会话或到主页面处理。';
    else if (!pending.length) reason = '这些选项已回答或不再属于当前等待节点。';
    return { contextKey, nodeId, assistantId: last.id, asks, pending, canSubmit: !reason, reason };
  }
  function prepare(snapshot, spec) {
    const b = boundary(snapshot);
    if (!spec || spec.contextKey !== b.contextKey || spec.nodeId !== b.nodeId) throw error('TOOL_STALE', '会话或提问节点已经变化，未提交选择，请重新读取会话。');
    if (!b.canSubmit) throw error('TOOL_NOT_READY', b.reason);
    if (!Array.isArray(spec.calls) || spec.calls.length !== b.pending.length) throw error('TOOL_INCOMPLETE', '必须完成当前组所有待回答的选项，不提交部分结果。');
    const supplied = new Map();
    for (const c of spec.calls) {
      if (!c || typeof c.toolCallId !== 'string' || supplied.has(c.toolCallId)) throw error('TOOL_ANSWER', '工具回答重复或格式无效。');
      supplied.set(c.toolCallId, c);
    }
    const results = [], summaries = [];
    for (const ask of b.asks) {
      let output;
      if (ask.state === 'answered') output = ask.output;
      else {
        const c = supplied.get(ask.toolCallId);
        if (!c || c.fingerprint !== ask.fingerprint) throw error('TOOL_STALE', '问题或选项内容已更新，未提交旧选择，请重新读取会话。');
        output = response(ask.questions, c.output);
        summaries.push(output.skipped ? '已跳过本组选项' : ask.questions.map(q => {
          const a = output.answers.find(x => x.questionId === q.id);
          return q.question + ' → ' + (a.selectedOptionId === null ? a.customResponse : q.options.find(o => o.id === a.selectedOptionId).label);
        }).join('\n'));
      }
      results.push({ requestNodeId: b.nodeId, tool: 'ask_user', toolCallId: ask.toolCallId, output });
    }
    return { results, summary: summaries.join('\n\n') };
  }
  function replySummary(message, allMessages = []) {
    if (message?.role !== 'user' || !Array.isArray(message.parts)) return '';
    const results = message.parts.filter(p => ['data-tool-results', 'data-durable-tool-results'].includes(p?.type) && Array.isArray(p.data)).flatMap(p => p.data);
    const out = [];
    for (const r of results) {
      if (r?.tool !== 'ask_user') continue;
      const request = allMessages.find(m => m?.role === 'assistant' && (m.metadata?.nodeId ?? m.id) === r.requestNodeId);
      const card = cards(request).find(c => c.toolCallId === r.toolCallId);
      try {
        if (!card?.questions.length) { out.push('已提交选项回答'); continue; }
        const value = response(card.questions, r.output);
        out.push(value.skipped ? '已跳过本组选项' : card.questions.map(q => {
          const a = value.answers.find(x => x.questionId === q.id);
          return q.question + ' → ' + (a.selectedOptionId === null ? a.customResponse : q.options.find(o => o.id === a.selectedOptionId).label);
        }).join('\n'));
      } catch { out.push('已提交选项回答（请在主页面查看详情）'); }
    }
    return out.join('\n\n');
  }
  return Object.freeze({ ToolError, toolName, isAskEvent, questions, fingerprint, response, cards, boundary, hasPendingInteraction, prepare, replySummary });
});
