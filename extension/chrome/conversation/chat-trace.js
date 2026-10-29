/* Read-only trace model. Only data already returned by the chat API is shown.
 * No network, storage, file reads, tool execution, or approval actions.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ArenaFusionTrace = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const LIMITS = Object.freeze({ detail: 60000, total: 800000, items: 160, transport: 500000 });
  const FINAL = new Set(['returned', 'error', 'input-error', 'denied', 'answered']);
  const WAITING = new Set(['waiting-user', 'approval']);
  const LABELS = Object.freeze({ input: '参数接收中', running: '等待工具返回', partial: '阶段性返回', returned: '已返回', error: '返回错误', 'input-error': '参数错误', denied: '已拒绝', 'waiting-user': '等待用户交互', approval: '等待批准 · 主页面处理', answered: '用户已回答', thinking: '思考内容接收中', thought: '思考内容已结束', unconfirmed: '未收到最终返回 · 请核对', interrupted: '接收中断 · 结果未确认' });
  const cleanName = x => typeof x === 'string' && x ? x.slice(0, 160) : '未命名工具';
  function capture(value, max = LIMITS.detail) {
    let text, format;
    try {
      if (typeof value === 'string') { text = value; format = 'text'; }
      else if (value === undefined) return { text: '', format: 'text', missing: true, truncated: false };
      else { text = JSON.stringify(value, null, 2); format = 'json'; }
    } catch { return { text: '内容无法序列化，请在主页面查看。', format: 'text', truncated: true }; }
    text = typeof text === 'string' ? text : String(text);
    const limit = Math.max(0, Math.min(LIMITS.detail, Number.isFinite(max) ? Math.floor(max) : LIMITS.detail));
    return { text: text.slice(0, limit), format, truncated: text.length > limit, missing: false };
  }
  function pick(event, available) {
    const out = {};
    if (typeof event.toolCallId === 'string' && event.toolCallId.length <= 512) out.toolCallId = event.toolCallId;
    if (event.providerExecuted === true) out.providerExecuted = true;
    if (event.preliminary === true) out.preliminary = true;
    const size = Math.max(0, Math.min(LIMITS.detail, Math.floor(available / 4) - 1200));
    if (event.type === 'tool-input-delta') {
      if (typeof event.inputTextDelta === 'string') { out.inputTextDelta = event.inputTextDelta.slice(0, size); out.inputTruncated = event.inputTextDelta.length > size; }
    } else if (['tool-input-available', 'tool-input-error'].includes(event.type)) out.inputDetail = capture(event.input, size);
    if (event.type === 'tool-output-available') out.outputDetail = capture(event.output, size);
    if (['tool-output-error', 'tool-input-error'].includes(event.type)) out.errorDetail = capture(event.errorText, size);
    if (event.type === 'tool-output-available' && event.output && typeof event.output === 'object') {
      if (event.output.status === 'awaiting_user_response') out.waitingUser = true;
      const status = event.output.status;
      if (typeof status === 'string' && ['error', 'failed', 'failure', 'shell_error', 'aborted', 'cancelled'].includes(status)) out.resultStatus = status;
      const exit = event.output.exit_code ?? event.output.exitCode;
      if (Number.isSafeInteger(exit) && exit !== 0) out.exitCode = exit;
      if (event.output.success === false) out.resultStatus = 'failed';
    }
    return out;
  }
  function sections(detail) {
    if (!detail || detail.missing) return [];
    if (detail.format !== 'json' || detail.truncated) return [{ label: detail.format === 'json' ? 'JSON（显示片段）' : '内容', text: detail.text }];
    try {
      const value = JSON.parse(detail.text);
      if (!value || Array.isArray(value) || typeof value !== 'object') return [{ label: 'JSON', text: detail.text }];
      const preferred = ['file_path', 'path', 'cwd', 'command', 'content', 'old_text', 'new_text', 'stdout', 'stderr'];
      const fields = [], rest = { ...value };
      for (const key of preferred) if (typeof value[key] === 'string') { fields.push({ label: key, text: value[key] }); delete rest[key]; }
      if (!fields.length) return [{ label: 'JSON', text: detail.text }];
      if (Object.keys(rest).length) fields.unshift({ label: '其它参数 / 状态（JSON）', text: JSON.stringify(rest, null, 2) });
      return fields;
    } catch { return [{ label: '接收中的内容', text: detail.text }]; }
  }
  class Store {
    constructor() { this.items = []; this.byKey = new Map(); this.step = 0; this.used = 0; this.omitted = false; this.missingIds = 0; this.counter = 0; }
    take(text, limit = LIMITS.detail) {
      const left = Math.max(0, Math.min(limit, LIMITS.total - this.used));
      const part = String(text || '').slice(0, left); this.used += part.length;
      if (part.length < String(text || '').length) this.omitted = true;
      return part;
    }
    detail(value, previous) {
      if (!value) return previous || { text: '', missing: true, truncated: false, format: 'text' };
      if (previous?.text) this.used = Math.max(0, this.used - previous.text.length);
      const text = this.take(value.text);
      return { ...value, text, truncated: !!value.truncated || text.length < value.text.length };
    }
    item(kind, id, name, newStart = false) {
      if (!id) { this.missingIds++; return null; }
      const step = Math.max(1, this.step), key = kind + ':' + step + ':' + id;
      let item = this.byKey.get(key);
      if (!item && !newStart) item = [...this.items].reverse().find(x => x.kind === kind && x.id === id && !FINAL.has(x.state));
      if (!item) {
        if (this.items.length >= LIMITS.items) { this.omitted = true; return null; }
        item = { key: kind + '-' + (++this.counter), id, kind, step, name: kind === 'reasoning' ? '思考过程' : cleanName(name), state: kind === 'reasoning' ? 'thinking' : 'input', input: null, output: null, error: null, revision: 0, providerExecuted: false };
        this.items.push(item); this.byKey.set(key, item);
      }
      if (name && kind === 'tool') item.name = cleanName(name);
      return item;
    }
    push(event) {
      if (!event || typeof event.type !== 'string') return;
      if (event.detailsOmitted) this.omitted = true;
      if (event.type === 'start-step') { this.step++; return; }
      if (event.type === 'finish-step') { for (const x of this.items) if (x.kind === 'reasoning' && x.state === 'thinking') { x.state = 'thought'; x.revision++; } return; }
      if (event.type.startsWith('reasoning-')) {
        const id = event.id || 'visible-reasoning';
        const x = this.item('reasoning', id, '', event.type === 'reasoning-start'); if (!x) return;
        if (event.type === 'reasoning-delta') {
          const before = x.output || { text: '', format: 'text', missing: false, truncated: false };
          const addition = this.take(event.delta, Math.max(0, LIMITS.detail - before.text.length));
          x.output = { ...before, text: before.text + addition, truncated: before.truncated || addition.length < String(event.delta || '').length };
        }
        if (event.type === 'reasoning-end') x.state = 'thought'; x.revision++; return;
      }
      if (!event.type.startsWith('tool-')) return;
      const x = this.item('tool', event.toolCallId, event.toolName && event.toolName !== '工具' ? event.toolName : '', event.type === 'tool-input-start'); if (!x) return;
      x.providerExecuted ||= event.providerExecuted === true;
      if (FINAL.has(x.state) && ['tool-input-start', 'tool-input-delta', 'tool-input-available'].includes(event.type)) return;
      if (event.type === 'tool-input-start') x.state = 'input';
      if (event.type === 'tool-input-delta') {
        const prior = x.input || { text: '', format: 'text', missing: false, truncated: false };
        const add = this.take(event.inputTextDelta, Math.max(0, LIMITS.detail - prior.text.length));
        x.input = { ...prior, text: prior.text + add, truncated: prior.truncated || !!event.inputTruncated || add.length < String(event.inputTextDelta || '').length }; x.state = 'input';
      }
      if (['tool-input-available', 'tool-input-error'].includes(event.type)) {
        if (event.inputDetail?.text || !x.input?.text) x.input = this.detail(event.inputDetail, x.input);
        else if (event.inputDetail?.truncated) x.input.truncated = true;
        x.state = event.type === 'tool-input-error' ? 'input-error' : 'running';
      }
      if (event.type === 'tool-output-available') {
        x.output = this.detail(event.outputDetail, x.output);
        x.state = event.waitingUser ? 'waiting-user' : event.preliminary ? 'partial' : 'returned';
        x.resultStatus = event.resultStatus || ''; x.exitCode = event.exitCode;
      }
      if (event.type === 'tool-output-error') x.state = 'error';
      if (event.errorDetail) x.error = this.detail(event.errorDetail, x.error);
      if (event.type === 'tool-output-denied') x.state = 'denied';
      if (event.type === 'tool-approval-request') x.state = 'approval';
      x.revision++;
    }
    finish(reason = 'complete') {
      for (const x of this.items) {
        if (x.kind === 'reasoning' && x.state === 'thinking') { x.state = reason === 'complete' ? 'thought' : 'interrupted'; x.revision++; }
        else if (x.kind === 'tool' && ['input', 'running', 'partial'].includes(x.state)) { x.state = reason === 'complete' ? 'unconfirmed' : 'interrupted'; x.revision++; }
      }
    }
    summary(x) {
      let state = LABELS[x.state] || x.state;
      if (x.state === 'returned' && x.resultStatus) state += ' · ' + x.resultStatus;
      if (x.state === 'returned' && Number.isSafeInteger(x.exitCode)) state += ' · 退出码 ' + x.exitCode;
      return x.name + ' · ' + state + ' · 第 ' + x.step + ' 步';
    }
    applyAnswers(results) {
      if (!this.nodeId || !Array.isArray(results)) return;
      for (const r of results) if (r?.requestNodeId === this.nodeId) for (const x of this.items) if (x.kind === 'tool' && x.id === r.toolCallId && WAITING.has(x.state)) { x.output = this.detail(capture(r.output), x.output); x.state = 'answered'; x.revision++; }
    }
    fromMessage(message, allMessages = []) {
      this.nodeId = message?.metadata?.nodeId ?? message?.id;
      const parts = Array.isArray(message?.parts) ? message.parts : [];
      const answers = new Map(), nodeId = message?.metadata?.nodeId ?? message?.id;
      for (const m of allMessages) if (m?.role === 'user') for (const p of (Array.isArray(m.parts) ? m.parts.slice(0, 2000) : [])) {
        if (['data-tool-results', 'data-durable-tool-results'].includes(p?.type) && Array.isArray(p.data)) for (const r of p.data.slice(0, 2000)) if (r?.requestNodeId === nodeId && typeof r.toolCallId === 'string') answers.set(r.toolCallId, r.output);
      }
      for (let i = 0; i < parts.length; i++) {
        if (this.items.length >= LIMITS.items) { this.omitted = true; break; }
        const p = parts[i]; if (!p || typeof p !== 'object') continue;
        if (p.type === 'step-start') { this.push({ type: 'start-step' }); continue; }
        if (p.type === 'reasoning') {
          const x = this.item('reasoning', 'history-reasoning-' + i, '', true);
          if (x) { x.output = this.detail(capture(typeof p.text === 'string' ? p.text : undefined)); x.state = p.state === 'streaming' ? 'thinking' : 'thought'; x.revision++; }
          continue;
        }
        if (!(p.type === 'dynamic-tool' || (typeof p.type === 'string' && p.type.startsWith('tool-')))) continue;
        const name = p.type === 'dynamic-tool' ? p.toolName : p.type.slice(5), id = p.toolCallId || 'history-part-' + i;
        this.push({ type: 'tool-input-start', toolName: name, toolCallId: id, providerExecuted: p.providerExecuted });
        if (p.input !== undefined || p.rawInput !== undefined) this.push({ type: p.state === 'input-streaming' ? 'tool-input-delta' : 'tool-input-available', toolCallId: id, inputDetail: capture(p.input ?? p.rawInput), inputTextDelta: p.state === 'input-streaming' ? capture(p.input ?? p.rawInput).text : undefined, inputTruncated: p.state === 'input-streaming' && capture(p.input ?? p.rawInput).truncated });
        if (p.state === 'output-available') this.push({ type: 'tool-output-available', toolCallId: id, ...pick({ type: 'tool-output-available', toolCallId: id, output: p.output, preliminary: p.preliminary }, LIMITS.detail * 8) });
        if (p.state === 'output-error') this.push({ type: 'tool-output-error', toolCallId: id, errorDetail: capture(p.errorText) });
        if (p.state === 'output-denied') this.push({ type: 'tool-output-denied', toolCallId: id });
        if (p.state === 'approval-requested') this.push({ type: 'tool-approval-request', toolCallId: id });
        if (p.state === 'approval-responded') { const x = this.item('tool', id); if (x) { x.state = p.approval?.approved === false ? 'denied' : 'running'; x.revision++; } }
        if (answers.has(p.toolCallId)) { const x = this.item('tool', id); if (x) { x.output = this.detail(capture(answers.get(p.toolCallId)), x.output); x.state = 'answered'; x.revision++; } }
      }
      // History does not prove missing tools are still running. Explicit human waits remain distinguishable.
      if (message?.metadata?.pending !== true) this.finish(message?.metadata?.feedback?.interruption === 'stop' ? 'interrupted' : 'complete');
      return this;
    }
  }
  return Object.freeze({ LIMITS, capture, pick, sections, Store });
});
