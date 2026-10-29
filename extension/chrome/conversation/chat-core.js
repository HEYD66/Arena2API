/* Arena Hybrid custom chat 1.1.0 — normal authenticated requests, no CAPTCHA bypass.
 * Pure core: no DOM, no storage, no cookie changes, no public window messages.
 * Protocol source and limits: public Arena frontend inspected 2026-10-15.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.ArenaFusionChat = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const VERSION = '2.4.5';
  const Trace = typeof module === 'object' && module.exports ? require('./chat-trace.js') : globalThis.ArenaFusionTrace;
  if (!Trace) throw new Error('chat-trace.js must be loaded before chat-core.js');
  const Tools = typeof module === 'object' && module.exports ? require('./chat-tools.js') : globalThis.ArenaFusionTools;
  if (!Tools) throw new Error('chat-tools.js must be loaded before chat-core.js');
  const MiB = 1024 * 1024;
  const LIMITS = Object.freeze({ files: 20, fileBytes: 25 * MiB, pdfBytes: 10 * MiB,
    totalBytes: 50 * MiB, textChars: 200000, jsonBytes: 8 * MiB, eventChars: 4 * MiB,
    replyChars: 1000000, bufferedChars: 4 * MiB });
  const MIME = Object.freeze(['image/png', 'image/webp', 'image/jpeg', 'image/gif',
    'text/plain', 'text/markdown', 'text/csv', 'text/html', 'text/xml', 'text/css',
    'text/javascript', 'application/json', 'application/xml', 'application/javascript', 'application/pdf']);
  const EXT = Object.freeze({ png: 'image/png', webp: 'image/webp', jpg: 'image/jpeg', jpeg: 'image/jpeg',
    gif: 'image/gif', txt: 'text/plain', md: 'text/markdown', markdown: 'text/markdown',
    csv: 'text/csv', html: 'text/html', htm: 'text/html', xml: 'application/xml', css: 'text/css',
    js: 'text/javascript', mjs: 'text/javascript', cjs: 'text/javascript', json: 'application/json', pdf: 'application/pdf',
    py: 'text/plain', ts: 'text/plain', tsx: 'text/plain', jsx: 'text/plain', c: 'text/plain', h: 'text/plain',
    cpp: 'text/plain', java: 'text/plain', rs: 'text/plain', go: 'text/plain', sh: 'text/plain', yaml: 'text/plain', yml: 'text/plain' });
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  class ChatError extends Error {
    constructor(code, message, details = {}) { super(message); this.name = 'ChatError'; this.code = code; Object.assign(this, details); }
  }
  const fail = (code, text, details) => new ChatError(code, text, details);
  const abortError = () => fail('CANCELLED', '已取消本地等待；已经发出的消息不会因此撤回。');
  function contextFromUrl(value) {
    try {
      const u = new URL(value);
      const m = u.pathname.match(/^\/(agent|work)\/([0-9a-f-]{36})\/?$/i);
      if (u.protocol !== 'https:' || !['arena.ai', 'www.arena.ai'].includes(u.hostname) || u.port || u.username || u.password || !m || !UUID.test(m[2])) return null;
      return Object.freeze({ origin: u.origin, sessionId: m[2].toLowerCase(), path: u.pathname, key: u.origin + '/' + m[1].toLowerCase() + '/' + m[2].toLowerCase() });
    } catch { return null; }
  }
  function shouldSendKey(event) { return event?.isTrusted === true && event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229; }
  function typeOf(file) {
    const raw = String(file && file.type || '').toLowerCase().split(';')[0].trim();
    if (MIME.includes(raw)) return raw;
    if (!raw || raw === 'application/octet-stream') return EXT[String(file && file.name || '').split('.').pop().toLowerCase()] || '';
    return '';
  }
  function validateInput(text, files = []) {
    if (typeof text !== 'string' || text.length > LIMITS.textChars) throw fail('TEXT_LIMIT', '小窗每条最多 20 万字符；草稿未发送，请拆分内容。');
    if (!Array.isArray(files) || files.length > LIMITS.files) throw fail('FILE_COUNT', '每轮最多 20 个附件。');
    let total = 0;
    const normalized = files.map(file => {
      if (!file || !Number.isSafeInteger(file.size) || file.size < 0 || typeof file.arrayBuffer !== 'function') throw fail('BAD_FILE', '附件无效，请重新选择。');
      const mimeType = typeOf(file), name = String(file.name || 'attachment').normalize('NFC');
      if (!mimeType) throw fail('FILE_TYPE', '不支持的附件类型：' + name.slice(0, 100) + '。当前支持图片、文本/代码、CSV、JSON、XML、PDF。');
      if (!name || name.length > 255 || /[\u0000-\u001f\u007f]/.test(name)) throw fail('FILE_NAME', '文件名为空、过长或含控制字符，请重命名后再选。');
      if (file.size > LIMITS.fileBytes || (mimeType === 'application/pdf' && file.size > LIMITS.pdfBytes)) throw fail('FILE_SIZE', '附件超限：' + name.slice(0, 100) + '。PDF 最多 10 MB，其它文件最多 25 MB。');
      total += file.size;
      if (total > LIMITS.totalBytes) throw fail('FILES_SIZE', '本轮附件合计不能超过 50 MB。');
      return { file, mimeType, name, size: file.size };
    });
    if (!text.trim() && !normalized.length) throw fail('EMPTY', '请输入内容或添加附件。');
    return normalized;
  }
  function uuid7(cryptoApi) {
    const bytes = new Uint8Array(16); cryptoApi.getRandomValues(bytes);
    let time = BigInt(Date.now());
    for (let i = 5; i >= 0; i--) { bytes[i] = Number(time & 255n); time >>= 8n; }
    bytes[6] = (bytes[6] & 15) | 0x70; bytes[8] = (bytes[8] & 63) | 0x80;
    const h = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }
  async function fileHash(file, cryptoApi) {
    const bytes = new Uint8Array(await cryptoApi.subtle.digest('SHA-256', await file.arrayBuffer()));
    let s = ''; for (const b of bytes) s += String.fromCharCode(b);
    const encoded = typeof btoa === 'function' ? btoa(s) : Buffer.from(bytes).toString('base64');
    return encoded.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function uploadGrant(data, hash, origin) {
    if (!data || typeof data.key !== 'string' || typeof data.uploadUrl !== 'string') throw fail('UPLOAD_PROTOCOL', '上传接口响应格式不符；没有发送附件数据。');
    const keyMatch = data.key.match(/^cas\/users\/([0-9a-f-]{36})\/([A-Za-z0-9_-]{43})$/i);
    if (!keyMatch || !UUID.test(keyMatch[1]) || keyMatch[2] !== hash) throw fail('UPLOAD_KEY', '附件哈希或存储路径未通过校验；没有发送附件数据。');
    let u;
    try { u = new URL(data.uploadUrl); } catch { throw fail('UPLOAD_ORIGIN', '上传地址无效。'); }
    // The authenticated grant supplies a physical PUT URL and a separate logical CAS key.
    // Virtual-hosted buckets / jurisdiction hosts and staging object paths are valid R2 forms.
    const r2 = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,3}r2\.cloudflarestorage\.com$/i.test(u.hostname);
    if (u.protocol !== 'https:' || u.username || u.password || u.port || u.hash) throw fail('UPLOAD_ORIGIN', '上传地址协议、身份、端口或片段不符合规则，已停止；不提供完整签名 URL。');
    if (u.origin !== origin && !r2) throw fail('UPLOAD_HOST', '上传主机不在 Arena/R2 范围，已停止。仅主机：' + u.hostname.slice(0, 120) + '；不要复制完整签名 URL。');
    if (!u.pathname || u.pathname === '/' || /%00/i.test(u.pathname)) throw fail('UPLOAD_PATH', '上传对象路径无效，已停止；没有传输附件。');
    return { key: data.key, uploadUrl: u.href, ownerId: keyMatch[1].toLowerCase(), url: '/api/chat/workspace/cas/user/' + hash };
  }
  function makePayload({ text, uploads = [], messageId, context, timezone, transportAddress }) {
    const parts = uploads.filter(f => f.mediaType.startsWith('image/')).map(f => ({ type: 'file', url: f.url, mediaType: f.mediaType, filename: f.filename }));
    if (text) parts.push({ type: 'text', text });
    const metadata = uploads.length ? { uploads: uploads.map(({ key, filename, mediaType }) => ({ key, filename, mediaType })) } : undefined;
    return { kind: 'message', payload: { message: { id: messageId, role: 'user', parts, ...(metadata ? { metadata } : {}) },
      chatId: transportAddress || context.sessionId, trigger: 'submit-message', messageId,
      metadata: { originHost: new URL(context.origin).host, timezone: timezone || 'UTC', submissionSource: 'chat_input', recaptchaV3Token: null } } };
  }
  function cursor(value) { return typeof value === 'string' && value.length > 0 && value.length <= 256 && !/[\u0000-\u0020\u007f]/.test(value) ? value : null; }
  function normalizeSnapshot(data, context, prefix) {
    if (!data || !Array.isArray(data.messages) || data.messages.length > 5000) throw fail('SNAPSHOT_PROTOCOL', '会话响应格式不符；未提交消息。请刷新主页面后再试。');
    if (data.id && String(data.id).toLowerCase() !== context.sessionId) throw fail('SESSION_MISMATCH', '返回的会话与当前页面不一致。');
    const address = data.triggerAddress || context.sessionId;
    if (typeof address !== 'string' || !UUID.test(address)) throw fail('SESSION_MISMATCH', '会话传输地址无效。');
    const taskId = data.taskId || 'chat-agent';
    if (!['chat-agent', 'agent-runner'].includes(taskId)) throw fail('UNSUPPORTED_SESSION', '此会话类型尚未支持，请在主页面发送。');
    return { context, prefix, address: address.toLowerCase(), taskId, messages: data.messages,
      lastEventId: cursor(data.lastEventId), streaming: data.isStreaming === true,
      title: typeof data.title === 'string' ? data.title.slice(0, 200) : '', pagination: data.pagination || null };
  }
  function assertIdle(snapshot) {
    const last = snapshot.messages.at(-1);
    if (snapshot.streaming || last?.metadata?.pending === true || (last?.role === 'user' && !last?.metadata?.error)) throw fail('PAGE_BUSY', '主会话可能仍在生成或等待处理，请先在主页面确认，不同时发送两轮。');
    if (Tools.hasPendingInteraction(snapshot.messages)) throw fail('TOOL_CONFIRMATION', '当前轮需要工具回答或确认。请使用上方选项卡；其它交互仍在主页面完成。');
  }
  // 把上游错误响应归成固定类别；只回传类别名，不回传响应原文（可能含令牌或个人信息）
  function upstreamHint(text) {
    if (!text.trim()) return 'empty';
    if (/just a moment|cf-chl|challenge-platform|cf_chl_opt/i.test(text)) return 'cloudflare_challenge';
    if (/attention required|sorry, you have been blocked|cloudflare ray id|cf-error-details|error code:?\s*10\d\d/i.test(text)) return 'cloudflare_block';
    if (/recaptcha|captcha|turnstile|verify you are human|human verification|人机验证/i.test(text)) return 'captcha';
    if (/rate.?limit|too many requests|slow down/i.test(text)) return 'rate_limit';
    if (/unauthori[sz]ed|not authenticated|session (?:has )?expired|log ?in required|sign ?in|forbidden/i.test(text)) return 'login';
    if (/^\s*[{[]/.test(text)) return 'json_error';
    if (/<html|<!doctype/i.test(text)) return 'html_page';
    return 'other';
  }
  function httpError(status, stage, body = '') {
    const text = String(body).slice(0, 10000);
    const hint = upstreamHint(text);
    const label = String(stage || '请求') + '失败（HTTP ' + status + '）：';
    if (/recaptcha|captcha|verify you are human|human verification|人机验证/i.test(text)) return fail('VERIFICATION_REQUIRED', label + '扩展请求返回验证相关错误；网页可能仍可正常发送，不代表一定会弹出验证码。不会绕过验证或自动重发。', { status, stage, hint });
    if (status === 401 || status === 403) return fail('ACCESS_DENIED', label + '登录、会话权限或平台验证未通过，请在主页面确认；未自动重发。', { status, stage, hint });
    if (status === 429) return fail('RATE_LIMIT', label + '平台限流或额度限制，请稍后在主页面确认；未自动重发。', { status, stage, hint });
    return fail('HTTP_' + status, stage + '失败（HTTP ' + status + '）。请在主页面确认，不要连续重发。', { status, stage, hint });
  }
  function bounded(work, ms, signal, code = 'TIMEOUT') {
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (fn, value) => { if (done) return; done = true; clearTimeout(timer); signal?.removeEventListener('abort', onAbort); fn(value); };
      const onAbort = () => finish(reject, abortError());
      const timer = setTimeout(() => finish(reject, fail(code, '等待超时，请检查主页面；没有自动重发。')), ms);
      if (signal?.aborted) { onAbort(); return; }
      signal?.addEventListener('abort', onAbort, { once: true });
      Promise.resolve().then(work).then(v => finish(resolve, v), e => finish(reject, e));
    });
  }
  async function bodyText(response, max, signal, ms) {
    if (!response.body?.getReader) {
      const text = await bounded(() => response.text(), ms, signal);
      if (text.length > max) throw fail('RESPONSE_LIMIT', '响应过大，已停止本地读取。');
      return text;
    }
    const reader = response.body.getReader(), decoder = new TextDecoder(); let size = 0, text = '';
    try {
      for (;;) {
        const item = await bounded(() => reader.read(), ms, signal);
        if (item.done) break;
        size += item.value.byteLength;
        if (size > max) throw fail('RESPONSE_LIMIT', '响应过大，已停止本地读取。');
        text += decoder.decode(item.value, { stream: true });
      }
      return text + decoder.decode();
    } finally { void reader.cancel().catch(() => {}); reader.releaseLock(); }
  }
  class SSEParser {
    constructor(onFrame, max = LIMITS.eventChars) { this.buffer = ''; this.onFrame = onFrame; this.max = max; }
    push(text, final = false) {
      this.buffer += text;
      for (;;) {
        const match = /\r?\n\r?\n/.exec(this.buffer);
        if (!match) break;
        if (match.index > this.max) throw fail('STREAM_LIMIT', '流式数据帧过大，请到主页面继续查看。');
        const frame = this.buffer.slice(0, match.index); this.buffer = this.buffer.slice(match.index + match[0].length); this.frame(frame);
      }
      if (this.buffer.length > this.max) throw fail('STREAM_LIMIT', '流式数据帧过大，请到主页面继续查看。');
      if (final && this.buffer.trim()) { this.frame(this.buffer); this.buffer = ''; }
    }
    frame(raw) {
      let id = null, event = '', data = [];
      for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith(':')) continue;
        const pos = line.indexOf(':'), key = pos < 0 ? line : line.slice(0, pos);
        const val = pos < 0 ? '' : line.slice(pos + 1).replace(/^ /, '');
        if (key === 'data') data.push(val); else if (key === 'id') id = cursor(val); else if (key === 'event') event = val;
      }
      if (data.length) this.onFrame({ id, event, data: data.join('\n') });
    }
  }
  function header(record, key) {
    if (!Array.isArray(record?.headers)) return null;
    const found = record.headers.find(x => Array.isArray(x) && String(x[0]).toLowerCase() === key);
    return found ? String(found[1]) : null;
  }
  function safeSeq(value) { const n = typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value; return Number.isSafeInteger(n) && n >= 0 ? n : null; }
  class TurnFilter {
    constructor({ seq, messageId, lastEventId, knownIds = [], onEvent, nativeToolPolicy }) {
      this.nativeToolPolicy = nativeToolPolicy; this.seq = seq; this.messageId = messageId; this.onEvent = onEvent; this.known = new Set(knownIds);
      this.trusted = !!lastEventId; this.baseTrusted = this.trusted; this.skip = false; this.seen = new Set(); this.recordIds = new Set(); this.format = 'auto'; this.maxSeq = safeSeq(lastEventId) ?? -1; this.traceRemaining = Trace.LIMITS.transport; this.traceLimitNoted = false;
      this.pending = []; this.pendingChars = 0; this.error = null; this.done = false; this.lastEventId = lastEventId || null; this.replyChars = 0;
    }
    flush() { for (const event of this.pending) this.deliver(event); this.pending = []; this.pendingChars = 0; }
    deliver(event) {
      let scopeAllowed = true;
      if (event.nativeScopeEvent) {
        try { scopeAllowed = this.nativeToolPolicy(event.nativeScopeEvent) === true; } catch { scopeAllowed = false; }
        delete event.nativeScopeEvent;
      }
      if (!scopeAllowed) throw fail('EXECUTION_SCOPE', '检测到不属于客户端任务的网页原生工具调用；云端操作可能已经发生，未视为本地完成。请核对，不自动重发。');
      this.replyChars += String(event.delta || '').length;
      if (this.replyChars > LIMITS.replyChars) throw fail('REPLY_LIMIT', '回复超过小窗显示上限，请在主页面继续查看；没有中止服务端任务。');
      this.onEvent(event);
    }
    setStreamVersion(value) {
      // Public SDK uses direct chunks for v1; other declared versions use event: batch.
      const next = !value ? 'auto' : value === 'v1' ? 'direct' : 'batch';
      if (next === 'auto') return;
      if (this.format !== 'auto' && this.format !== next) throw fail('PROTOCOL_CHANGED', '重连时流封装发生变化，请在主页面核对；未重发消息。');
      this.format = next;
    }
    frame(frame) {
      if (this.done || frame.data.trim() === '[DONE]') return;
      if (this.format === 'batch' && frame.event !== 'batch') return; // transport events are not chat JSON
      let data; try { data = JSON.parse(frame.data); } catch { throw fail('STREAM_PROTOCOL', '回复流事件无法解码（仅记录结构：字符数 ' + frame.data.length + '），请在主页面核对。'); }
      if (data && Object.prototype.hasOwnProperty.call(data, 'records') && !Array.isArray(data.records)) throw fail('STREAM_PROTOCOL', '流记录列表格式不符。');
      if (Array.isArray(data?.records)) {
        if (this.format === 'direct') throw fail('STREAM_PROTOCOL', '回复封装与声明的 v1 格式不一致，请在主页面核对。');
        this.format = 'batch';
        for (const r of data.records) { this.record(r); if (this.done) break; }
      } else {
        if (this.format === 'batch') throw fail('STREAM_PROTOCOL', 'batch 事件缺少记录列表，请在主页面核对。');
        if (frame.id) this.lastEventId = frame.id;
        if (data && typeof data === 'object') this.record({ body: JSON.stringify(data) });
      }
    }
    control(kind, record, event) {
      if (kind === 'upgrade-required') throw fail('PROTOCOL_CHANGED', '平台要求更新传输协议，请使用主页面并反馈此提示。');
      if (kind !== 'turn-complete') return; // opaque transport control: never displayed and never unlocks input
      const endSeq = safeSeq(header(record, 'session-in-event-id') ?? event?.sessionInEventId);
      if (endSeq === null) throw fail('BOUNDARY_UNKNOWN', '无法确认本轮结束边界，请在主页面核对。');
      if (endSeq < this.seq) { this.pending = []; this.pendingChars = 0; this.error = null; this.skip = false; this.trusted = false; return; }
      if (endSeq > this.seq) throw fail('OTHER_INPUT', '会话已收到其它输入，停止本轮本地接收；请在主页面核对。');
      if (this.error) throw this.error;
      this.flush(); this.done = true;
    }
    record(record) {
      if (this.done || !record || typeof record !== 'object') return;
      const recordSeq = safeSeq(record.seq_num);
      if (record.seq_num !== undefined && recordSeq === null) throw fail('STREAM_PROTOCOL', '流记录序号无效；未确认回复完成。');
      if (recordSeq !== null) {
        // v2 batches usually have NO SSE id. Advance even for S2 commands, but never regress on replay.
        if (recordSeq > this.maxSeq) { this.maxSeq = recordSeq; this.lastEventId = String(recordSeq); }
        if (this.seen.has(recordSeq)) return;
        this.seen.add(recordSeq);
        if (this.seen.size > 100000) throw fail('STREAM_LIMIT', '本轮数据记录过多，请在主页面继续。');
      }
      // S2 reserves an empty first header name for command records. Their bodies are NOT chat JSON.
      // They are skipped, never executed, and cannot signal that a user turn completed.
      if (Array.isArray(record.headers) && Array.isArray(record.headers[0]) && record.headers[0][0] === '') return;
      const transportControl = header(record, 'trigger-control');
      if (transportControl !== null) { this.control(transportControl, record); return; }
      let parsed = null;
      if (typeof record.body === 'string' && record.body) {
        try { parsed = JSON.parse(record.body); }
        catch { throw fail('STREAM_PROTOCOL', '聊天数据记录无法解码（非命令/控制帧；body 类型 string，字符数 ' + record.body.length + '）。请在主页面核对，勿重发。'); }
      } else if (record.body !== undefined && record.body !== null && typeof record.body !== 'string') {
        throw fail('STREAM_PROTOCOL', '聊天数据记录 body 类型异常；请在主页面核对，勿重发。');
      }
      const wrapped = parsed?.data && typeof parsed.data.type === 'string';
      const event = wrapped ? parsed.data : parsed;
      if (event?.type === 'trigger:turn-complete') { this.control('turn-complete', record, event); return; }
      if (event?.type === 'trigger:upgrade-required') { this.control('upgrade-required', record, event); return; }
      // Envelope id identifies a transport part; event.id identifies a text block and MUST NOT be deduped.
      if (wrapped && typeof parsed.id === 'string' && parsed.id) {
        if (parsed.id.length > 512) throw fail('STREAM_PROTOCOL', '流记录标识长度异常。');
        if (this.recordIds.has(parsed.id)) return;
        this.recordIds.add(parsed.id);
        if (this.recordIds.size > 5000) this.recordIds.delete(this.recordIds.values().next().value);
      }
      if (!event || typeof event.type !== 'string' || event.type.startsWith('trigger:')) return;
      const rowSeq = safeSeq(header(record, 'session-in-event-id'));
      if (rowSeq !== null && rowSeq !== this.seq) return;
      if (rowSeq === this.seq) this.trusted = true;
      if (event.type === 'start') {
        const parent = event.messageMetadata?.lastUserMessageId || event.messageMetadata?.inputId;
        this.skip = !!(event.messageId && this.known.has(event.messageId)) || !!(parent && parent !== this.messageId);
        if (parent === this.messageId) this.trusted = true;
      }
      if (this.skip) return;
      if (event.type === 'error') {
        const reason = String(event.errorText || event.message || '');
        this.error = /captcha|人机验证|human verification|verify you are human/i.test(reason) ? fail('VERIFICATION_REQUIRED', '回复流返回验证相关错误；仅在原页面确有验证提示时按正常流程处理，本次不会自动重发。')
          : fail('MODEL_ERROR', '服务端处理失败：' + reason.replace(/Bearer\s+\S+/gi, 'Bearer [已隐藏]').slice(0, 220));
        return;
      }
      const clean = { type: event.type };
      if (event.type.startsWith('tool-') && this.nativeToolPolicy) {
        // Inspect only after this event is attributed to the current turn, before
        // lossy trace capture can hide arguments. Never forward the raw object.
        clean.nativeScopeEvent = {type:event.type, toolName:event.toolName, toolCallId:event.toolCallId, input:event.input};
      }
      if (['text-delta', 'reasoning-delta'].includes(event.type)) { if (typeof event.delta !== 'string') return; clean.delta = event.delta; clean.id = typeof event.id === 'string' ? event.id.slice(0, 150) : ''; }
      else if (event.type === 'start') clean.messageId = typeof event.messageId === 'string' ? event.messageId.slice(0, 150) : '';
      else if (['tool-input-start', 'tool-input-delta', 'tool-input-available', 'tool-input-error', 'tool-output-available', 'tool-output-error', 'tool-output-denied', 'tool-approval-request'].includes(event.type)) {
        clean.toolName = String(event.toolName || '工具').slice(0, 100);
        if (event.type === 'tool-input-available' && Tools.isAskEvent(clean.toolName)) {
          try { clean.questions = Tools.questions(event.input); clean.toolCallId = typeof event.toolCallId === 'string' ? event.toolCallId.slice(0, 512) : ''; }
          catch { clean.questionError = '选项格式尚未受支持，请在主页面处理。'; }
        }
        // Detail data is optional and budgeted independently; truncation must not break the reply.
        const detail = this.traceRemaining > 2048 ? Trace.pick(event, this.traceRemaining) : null, size = detail ? JSON.stringify(detail).length : Infinity;
        if (this.traceRemaining > 2048 && size <= this.traceRemaining) { Object.assign(clean, detail); this.traceRemaining -= size; }
        else if (!this.traceLimitNoted) { clean.detailsOmitted = true; this.traceLimitNoted = true; }
        else if (event.type === 'tool-input-delta') return;
      }
      else if (['reasoning-start', 'reasoning-end'].includes(event.type)) clean.id = typeof event.id === 'string' ? event.id.slice(0, 150) : '';
      else if (event.type === 'file') { clean.url = typeof event.url === 'string' ? event.url : ''; clean.mediaType = String(event.mediaType || ''); }
      else if (!['finish', 'start-step', 'finish-step', 'text-start', 'text-end', 'reasoning-start', 'reasoning-end'].includes(event.type)) return;
      if (this.trusted && !this.error) this.deliver(clean);
      else { this.pendingChars += JSON.stringify(clean).length; if (this.pendingChars > LIMITS.bufferedChars) throw fail('STREAM_LIMIT', '待核对的历史流过大，请在主页面查看。'); this.pending.push(clean); }
    }
  }
  function createClient(options = {}) {
    const fetchFn = options.fetch || globalThis.fetch.bind(globalThis), cryptoApi = options.crypto || globalThis.crypto;
    const getContext = options.getContext || (() => contextFromUrl(globalThis.location.href));
    const emitFn = options.onEvent || (() => {}), times = { request: 20000, upload: 120000, streamIdle: 90000, reconnectBase: 250, reconnectMax: 10000, ...options.timeouts };
    let active = null, pending = null, disposed = false, phase = 'idle', counter = 0;
    const emit = (type, data = {}) => { if (!disposed) { try { emitFn({ type, ...data }); } catch {} } };
    const state = (name, text, extra = {}) => { phase = name; emit('state', { phase: name, text, ...extra }); };
    function guard(op) {
      if (disposed || op.signal?.aborted) throw abortError();
      const now = getContext();
      if (!now || now.key !== op.context.key) throw fail('CONTEXT_CHANGED', '主页面会话已切换，已停止本轮本地操作；没有自动向新会话发送。');
    }
    function operation() {
      const context = getContext();
      if (disposed) throw fail('DISPOSED', '小窗已失效，请刷新页面。');
      if (!context) throw fail('NO_SESSION', '请先在主页面打开已有 Agent 会话。此版本不自动新建会话或切换模式。');
      const controller = new AbortController();
      return { context, controller, signal: controller.signal, serial: ++counter, attempted: false, accepted: false, token: '', prefix: null };
    }
    async function request(op, url, init, timeout = times.request) {
      guard(op); const c = new AbortController(), relay = () => c.abort(); op.signal.addEventListener('abort', relay, { once: true });
      try {
        const response = await bounded(() => fetchFn(url, { ...init, signal: c.signal, redirect: 'error', cache: 'no-store' }), timeout, op.signal);
        guard(op); return response;
      } finally { c.abort(); op.signal.removeEventListener('abort', relay); }
    }
    // Keep the controller alive while reading a response body; aborting at header time would cancel the stream.
    async function json(op, path, init = {}, { timeout = times.request, stage = '请求', limit = LIMITS.jsonBytes } = {}) {
      guard(op); const c = new AbortController(), relay = () => c.abort(); op.signal.addEventListener('abort', relay, { once: true });
      try {
        return await bounded(async () => {
          const response = await fetchFn(op.context.origin + path, { method: 'GET', credentials: 'include', cache: 'no-store', redirect: 'error', ...init, signal: c.signal });
          guard(op);
          const text = await bodyText(response, limit, op.signal, timeout);
          guard(op);
          if (!response.ok) throw httpError(response.status, stage, text);
          try { return JSON.parse(text); } catch { throw fail('BAD_JSON', stage + '响应不是有效 JSON。'); }
        }, timeout, op.signal);
      } finally { c.abort(); op.signal.removeEventListener('abort', relay); }
    }
    const post = (op, path, data, stage) => json(op, path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }, { stage });
    async function snapshot(op) {
      // `api` is the module's exported client, NOT another path segment.
      // Public client base '/api' + route '/chat/:id' => '/api/chat/:id'.
      const prefix = '/api';
      const data = await json(op, prefix + '/chat/' + op.context.sessionId + '?includeStreamToken=false&recoverRun=false', {}, { stage: '读取会话' });
      const out = normalizeSnapshot(data, op.context, prefix); op.prefix = prefix; return out;
    }
    async function upload(op, item, index, count) {
      state('uploading', '正在准备附件 ' + (index + 1) + '/' + count, { fileIndex: index, fileName: item.name });
      const hash = await bounded(() => fileHash(item.file, cryptoApi), times.request, op.signal); guard(op);
      const grant = uploadGrant(await post(op, op.prefix + '/storage/generate-agent-upload-url', { hash, contentType: item.mimeType, size: item.size }, '申请附件上传'), hash, op.context.origin);
      if (op.uploadOwner && op.uploadOwner !== grant.ownerId) throw fail('ACCOUNT_CHANGED', '附件上传期间账号发生变化，已停止消息提交。');
      op.uploadOwner = grant.ownerId;
      state('uploading', '正在上传附件 ' + (index + 1) + '/' + count, { fileIndex: index, fileName: item.name });
      const response = await request(op, grant.uploadUrl, { method: 'PUT', body: item.file, headers: { 'Content-Type': item.mimeType }, credentials: 'omit', referrerPolicy: 'no-referrer' }, times.upload);
      if (!response.ok) throw httpError(response.status, '上传附件');
      guard(op); emit('file-uploaded', { index, name: item.name });
      return { key: grant.key, url: grant.url, mediaType: item.mimeType, filename: item.name, size: item.size };
    }
    async function stream(op) {
      guard(op);
      const filter = op.filter || new TurnFilter({ seq: op.seq, messageId: op.messageId, lastEventId: op.snapshot.lastEventId,
        nativeToolPolicy: options.nativeToolPolicy, knownIds: op.snapshot.messages.map(m => m && m.id).filter(Boolean), onEvent: event => { guard(op); emit('delta', { event, messageId: op.messageId }); } });
      op.filter = filter;
      // Elapsed time is telemetry, never a reply deadline. Old streamTotal options are ignored.
      const started = Date.now(); let attempts = 0, failures = 0, emptyLeases = 0, sawFrame = false;
      // Before any data frame there is nothing to duplicate. Afterwards require batch sequence dedup.
      const canResume = () => !sawFrame || filter.format === 'batch' && !!filter.lastEventId;
      const elapsed = () => Math.max(0, Date.now() - started);
      const pause = ms => new Promise((resolve, reject) => {
        let timer, done = false;
        const finish = (fn, value) => { if (done) return; done = true; clearTimeout(timer); op.signal.removeEventListener('abort', abort); fn(value); };
        const abort = () => finish(reject, abortError());
        timer = setTimeout(() => finish(resolve), ms);
        if (op.signal.aborted) abort(); else op.signal.addEventListener('abort', abort, {once:true});
      });
      // A quiet read is not a failed task. Without a replay-safe cursor, keep the SAME pending read.
      // One promise/listener per read, not a growing list of Promise.race handlers during long silence.
      const readItem = reader => new Promise((resolve, reject) => {
        let timer, done = false;
        const finish = (fn, value) => { if (done) return; done = true; clearTimeout(timer); op.signal.removeEventListener('abort', abort); fn(value); };
        const abort = () => finish(reject, abortError());
        const quiet = () => {
          try {
            guard(op);
            if (canResume()) { finish(reject, fail('STREAM_TIMEOUT', '接收连接暂未返回数据，将只读续收原任务；未判定任务失败。')); return; }
            state('streaming', '暂未收到新数据，保留原连接等待；任务状态未确认，不重发。', {elapsedMs:elapsed()});
            timer = setTimeout(quiet, times.streamIdle);
          } catch (error) { finish(reject, error); }
        };
        timer = setTimeout(quiet, times.streamIdle);
        if (op.signal.aborted) { abort(); return; }
        op.signal.addEventListener('abort', abort, {once:true});
        Promise.resolve().then(() => { guard(op); return reader.read(); }).then(v => finish(resolve, v), e => finish(reject, e));
      });
      while (!filter.done) {
        guard(op); attempts++;
        const cursorBefore = filter.lastEventId;
        const c = new AbortController(), relay = () => c.abort(); op.signal.addEventListener('abort', relay, { once: true });
        let reader;
        const receive = async (work, code, ms) => {
          try { return await (ms === undefined ? work() : bounded(work, ms, op.signal, 'STREAM_TIMEOUT')); }
          catch (error) {
            if (error instanceof ChatError) throw error;
            throw fail(code, '接收连接中断（' + code + '），任务结果尚未确认；未重新提交消息。');
          }
        };
        try {
          const headers = { Authorization: 'Bearer ' + op.token, Accept: 'text/event-stream', 'Timeout-Seconds': '60' };
          if (filter.lastEventId) headers['Last-Event-ID'] = filter.lastEventId;
          const response = await receive(() => fetchFn(op.context.origin + '/ai-proxy/realtime/v1/sessions/' + op.snapshot.address + '/out', { headers, credentials: 'include', cache: 'no-store', redirect: 'error', signal: c.signal }), 'STREAM_CONNECT', times.request);
          guard(op);
          if (!response.ok) throw httpError(response.status, '读取回复', await bodyText(response, 65536, op.signal, times.request));
          if (!response.body?.getReader || !/text\/event-stream/i.test(response.headers.get('content-type') || '')) throw fail('STREAM_PROTOCOL', '回复不是预期的流式数据，请回主页面查看。');
          filter.setStreamVersion(response.headers.get('X-Stream-Version'));
          reader = response.body.getReader(); const decoder = new TextDecoder(), parser = new SSEParser(frame => {
            if (frame.data) sawFrame = true;
            const prior = filter.lastEventId;
            filter.frame(frame);
            if (filter.lastEventId !== prior) { failures = 0; emptyLeases = 0; }
          });
          for (;;) {
            const item = await receive(() => readItem(reader), 'STREAM_READ'); guard(op);
            if (item.done) { parser.push(decoder.decode(), true); break; }
            parser.push(decoder.decode(item.value, { stream: true }));
            if (filter.done) break;
          }
          if (!filter.done) {
            if (!canResume()) throw fail('STREAM_ENDED', '连接已断开且没有可靠续收游标，结果待核对；未判定任务完成，也没有重发消息。');
            emptyLeases = filter.lastEventId === cursorBefore ? emptyLeases + 1 : 0;
            state('streaming', '接收连接结束，继续只读接收原任务；不重新提交。', {attempt:attempts,elapsedMs:elapsed()});
          }
        } catch (error) {
          guard(op);
          const retryable = error instanceof ChatError && ['STREAM_CONNECT', 'STREAM_READ', 'STREAM_TIMEOUT'].includes(error.code);
          if (!retryable || !canResume()) throw error;
          failures++;
          state('streaming', '连接暂时中断，等待恢复并只读续收原任务；未判定任务失败。', {code:error.code,attempt:attempts,elapsedMs:elapsed()});
        } finally {
          c.abort();
          if (reader) { try { void Promise.resolve(reader.cancel()).catch(() => {}); } catch {} try { reader.releaseLock(); } catch {} }
          op.signal.removeEventListener('abort', relay);
        }
        // Backoff limits retry frequency, NOT task duration or number of reconnects. Cancellation stays live.
        if (!filter.done) await pause(Math.min(times.reconnectMax, times.reconnectBase * 2 ** Math.min(6, Math.max(failures, emptyLeases))));
      }
      guard(op); pending = null; op.token = ''; state(op.stopRequested ? 'stopped' : 'complete', op.stopRequested ? '本轮已结束（已发送停止请求）' : '本轮回复完成');
    }
    async function load() {
      if (active) throw fail('BUSY', '当前操作尚未完成。');
      const op = operation(); active = op; state('loading', '读取当前会话…');
      try { const snap = await snapshot(op); guard(op); emit('history', { messages: snap.messages.slice(-60), toolBoundary: Tools.boundary(snap), title: snap.title, truncated: snap.messages.length > 60 || snap.pagination?.hasMore === true }); state(pending ? 'uncertain' : 'idle', pending ? '上轮结果仍待核对；未自动重发。' : '就绪 · 发送到当前会话'); return snap; }
      catch (error) { state('error', error.message || '读取失败'); throw error; }
      finally { if (active === op) active = null; }
    }
    const acceptedData = op => ({ messageId: op.messageId, text: op.text, toolReply: op.kind === 'ask_user', ...(op.kind === 'ask_user' ? { toolResults: op.payload?.payload?.message?.parts?.find(p => p.type === 'data-tool-results')?.data || [] } : {}), files: op.files.map(f => ({ name: f.name, type: f.mimeType, size: f.size })) });
    async function submit({ text = '', files = [], toolSpec = null }) {
      if (active || pending) throw fail('BUSY', pending ? '上轮结果尚未确认，请先核对，不要重复发送。' : '当前操作尚未完成。');
      const items = toolSpec ? [] : validateInput(text, files), op = operation();
      if (toolSpec && toolSpec.contextKey !== op.context.key) throw fail('TOOL_STALE', '选项属于其它会话，未提交，请重新读取会话。');
      active = op; op.text = text; op.files = items; op.kind = toolSpec ? 'ask_user' : 'message';
      op.messageId = uuid7(cryptoApi); op.partId = uuid7(cryptoApi);
      state('loading', '核对当前会话…');
      try {
        op.snapshot = await snapshot(op);
        let prepared = null;
        if (toolSpec) { prepared = Tools.prepare(op.snapshot, toolSpec); op.text = prepared.summary; }
        else assertIdle(op.snapshot);
        guard(op);
        const uploads = [];
        for (let i = 0; i < items.length; i++) uploads.push(await upload(op, items[i], i, items.length));
        if (items.length) {
          const fresh = await snapshot(op); assertIdle(fresh);
          if (fresh.address !== op.snapshot.address || fresh.taskId !== op.snapshot.taskId || fresh.messages.at(-1)?.id !== op.snapshot.messages.at(-1)?.id) throw fail('CONVERSATION_CHANGED', '上传期间主会话出现新消息，请确认后重新发送；草稿仍保留。');
          op.snapshot = fresh;
        }
        state('authorizing', '核对会话授权…');
        const auth = await post(op, op.prefix + '/chat/trigger-session', { sessionId: op.context.sessionId, timezone: options.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' }, '获取会话授权');
        if (!auth || typeof auth.publicAccessToken !== 'string' || !auth.publicAccessToken || auth.publicAccessToken.length > 16384 || /[\r\n]/.test(auth.publicAccessToken)) throw fail('AUTH_PROTOCOL', '没有取得有效会话授权；未提交消息。');
        op.token = auth.publicAccessToken; guard(op);
        if (!toolSpec) {
          const fresh = await snapshot(op); assertIdle(fresh); guard(op);
          if (fresh.address !== op.snapshot.address || fresh.taskId !== op.snapshot.taskId || fresh.messages.at(-1)?.id !== op.snapshot.messages.at(-1)?.id) {
            throw fail('CONVERSATION_CHANGED', '授权期间原对话出现新消息，请刷新后重新确认；未提交。');
          }
          op.snapshot = fresh;
        }
        if (toolSpec) {
          const fresh = await snapshot(op); guard(op);
          if (fresh.address !== op.snapshot.address || fresh.taskId !== op.snapshot.taskId) throw fail('TOOL_STALE', '授权期间会话身份发生变化，未提交选择。');
          prepared = Tools.prepare(fresh, toolSpec); op.snapshot = fresh; op.text = prepared.summary;
        }
        const payload = makePayload({ text: toolSpec ? '' : text, uploads, messageId: op.messageId, context: op.context, transportAddress: op.snapshot.address, timezone: options.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone });
        if (toolSpec) payload.payload.message.parts = [{ type: 'data-tool-results', data: prepared.results }];
        op.payload = payload; state('sending', '正在提交一次消息，请勿重复点击…'); op.attempted = true;
        const reply = await json(op, '/ai-proxy/realtime/v1/sessions/' + op.snapshot.address + '/in/append', { method: 'POST', headers: {
          'Content-Type': 'application/json', Authorization: 'Bearer ' + op.token, 'x-trigger-source': 'sdk', 'X-Part-Id': op.partId
        }, body: JSON.stringify(payload) }, { stage: '提交消息', limit: 65536 });
        op.seq = safeSeq(reply?.seq);
        if (op.seq === null) throw fail('ACK_UNKNOWN', '服务端回应缺少有效回执序号，发送结果待核对；没有自动重发。');
        op.accepted = true; emit('accepted', acceptedData(op));
        state('streaming', op.snapshot.lastEventId ? '消息已接收，等待流式回复…' : '消息已接收，正在核对本轮边界（避免混入历史回复）…');
        await stream(op); return { accepted: true, messageId: op.messageId };
      } catch (error) {
        const e = error instanceof ChatError ? error : error instanceof Tools.ToolError ? fail(error.code, error.message) : fail('INTERNAL_ERROR', '本地处理异常，请在主页面核对；未记录原始异常内容，未自动重发。');
        const knownRejected = !op.accepted && Number.isInteger(e.status) && e.status >= 400 && e.status < 500 && e.status !== 408;
        if (op.attempted && !knownRejected) {
          pending = op; op.token = ''; state('uncertain', e.message + (op.accepted ? ' 消息已确认提交，请勿重复发送。' : ' 发送结果未确认，草稿仍保留。'), { code: e.code, accepted: op.accepted });
        } else { op.token = ''; state('error', e.message, { code: e.code, accepted: false }); }
        e.submission_attempted = op.attempted === true; e.submission_accepted = op.accepted === true;
        emit('failure', { code: e.code, message: e.message, toolReply: op.kind === 'ask_user', accepted: op.accepted, uncertain: !!pending, status: e.status || null }); throw e;
      } finally { if (active === op) active = null; }
    }
    const send = ({ text, files = [] }) => submit({ text, files });
    const answerQuestions = toolSpec => submit({ toolSpec });
    async function check() {
      if (active) throw fail('BUSY', '当前操作尚未完成。');
      if (!pending) return load();
      const old = pending, op = operation();
      if (op.context.key !== old.context.key) throw fail('CONTEXT_CHANGED', '请回到原会话核对；不会在另一会话查询或重发。');
      active = op; op.prefix = old.prefix; state('checking', '只读核对上轮结果，不重发消息…');
      try {
        const snap = await snapshot(op); guard(op);
        const found = snap.messages.some(m => m?.id === old.messageId || (m?.role === 'user' && m?.metadata?.inputId === old.messageId));
        if (found && !old.accepted) { old.accepted = true; emit('accepted', acceptedData(old)); }
        emit('history', { messages: snap.messages.slice(-60), toolBoundary: Tools.boundary(snap), title: snap.title, truncated: snap.messages.length > 60 });
        let result = null;
        try { result = await json(op, op.prefix + '/chat/' + op.context.sessionId + '/run-status?inputId=' + encodeURIComponent(old.messageId), {}, { stage: '核对本轮状态', limit: 65536 }); }
        catch (error) { if (['CONTEXT_CHANGED', 'CANCELLED'].includes(error.code)) throw error; }
        guard(op);
        if (result?.status === 'completed' && result.completion?.inputId === old.messageId) {
          if (!old.accepted) emit('accepted', acceptedData(old));
          pending = null; state('complete', '服务端已确认本轮完成，已刷新会话'); return true;
        }
        if (result?.failure?.inputId === old.messageId) { pending = null; state('error', '服务端确认本轮处理失败，请在主页面查看原因；未重发。'); return true; }
        state('uncertain', old.accepted ? '已确认消息存在，但完成状态仍待确认；请在主页面查看，或稍后再核对。' : '暂未取得发送成功或失败的可靠确认。不要重复发送，请查看主页面。'); return false;
      } catch (error) { state('uncertain', '核对未完成，上轮锁定仍保留；没有重新发送。', { code: error.code || 'CHECK_FAILED' }); throw error; } finally { if (active === op) active = null; }
    }
    async function stop() {
      const op = active;
      if (!op) return false;
      guard(op);
      if (!op.attempted) { op.controller.abort(); return true; }
      if (!op.accepted) throw fail('ACK_PENDING', '提交结果未确认，暂不能安全停止。请等回执或在主页面核对。');
      if (op.stopPromise) return op.stopPromise;
      op.stopRequested = true; state('stopping', '正在请求停止本轮生成…');
      op.stopPromise = json(op, '/ai-proxy/realtime/v1/sessions/' + op.snapshot.address + '/in/append', { method: 'POST', headers: {
        'Content-Type': 'application/json', Authorization: 'Bearer ' + op.token, 'x-trigger-source': 'sdk', 'X-Part-Id': uuid7(cryptoApi)
      }, body: JSON.stringify({ kind: 'stop', targetInputId: op.messageId }) }, { stage: '请求停止', limit: 65536 }).then(() => {
        if (active === op && !op.filter?.done) state('streaming', '已提交停止请求，等待本轮结束确认…'); return true;
      }).catch(error => { if (active === op && !op.filter?.done) state('streaming', '停止请求未确认，请在主页面查看；未重发停止请求。'); throw error; });
      return op.stopPromise;
    }
    function releaseUncertain() {
      if (active) throw fail('BUSY', '当前操作尚未完成。');
      pending = null; state('idle', '已由用户解除本地锁定；不会撤回或自动重发先前消息。');
    }
    function cancelForNavigation() { if (active && getContext()?.key !== active.context.key) active.controller.abort(); }
    function dispose() { disposed = true; active?.controller.abort(); if (active) active.token = ''; if (pending) pending.token = ''; pending = null; }
    return Object.freeze({ load, send, answerQuestions, check, stop, releaseUncertain, cancelForNavigation, dispose,
      getState: () => ({ phase, busy: !!active, pending: !!pending, accepted: !!(active?.accepted || pending?.accepted), canStop: !!active && (!active.attempted || active.accepted), context: active?.context || pending?.context || null }) });
  }
  return Object.freeze({ VERSION, CLIENT_EXECUTION_GUARD: 1, LIMITS, MIME, Tools, Trace, ChatError, contextFromUrl, shouldSendKey, typeOf, validateInput, uuid7, fileHash,
    uploadGrant, makePayload, normalizeSnapshot, httpError, bounded, SSEParser, TurnFilter, createClient });
});
