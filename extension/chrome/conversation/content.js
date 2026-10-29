/* Existing-conversation adapter. ISOLATED extension world, explicit commands only.
 * No page fetch hooks, DOM scraping, model lookup, automatic sends or file tools.
 */
(() => {
  'use strict';
  const Core = globalThis.ArenaFusionChat;
  let client = null, clientKey = '', sendSeen = false, active = false, collected = '';
  const completed = new Map();
  let progress = null, replyOverflow = false;
  let nativePolicy = () => true;
  function referencePolicy(allowed) {
    const calls = new Map();
    const paths = new Set(['/home/user/uploads/tools-index.md', '/home/user/uploads/tools-reference.md']);
    const checksum = 'sha256sum /home/user/uploads/tools-index.md /home/user/uploads/tools-reference.md';
    const check = event => {
      if (!allowed) return false;
      const id = event.toolCallId;
      if (typeof id !== 'string' || !id || id.length > 512 || calls.size > 256) return false;
      const prior = calls.get(id), name = event.toolName || prior?.name;
      if (!['read_file', 'functions.read_file', 'bash', 'functions.bash'].includes(name)) return false;
      if (event.type === 'tool-input-start') { calls.set(id, {name, checked:false}); return true; }
      if (event.type === 'tool-input-available') {
        const a = event.input;
        if (!a || typeof a !== 'object' || Array.isArray(a)) return false;
        const read = name.endsWith('read_file');
        const valid = read
          ? Object.keys(a).every(k => ['path','offset','limit'].includes(k)) && paths.has(a.path)
            && Number.isSafeInteger(a.offset) && a.offset >= 1 && Number.isSafeInteger(a.limit) && a.limit >= 1 && a.limit <= 300
          : Object.keys(a).every(k => ['command','cwd','timeout'].includes(k)) && a.command === checksum
            && (a.cwd === undefined || a.cwd === '/home/user')
            && (a.timeout === undefined || Number.isSafeInteger(a.timeout) && a.timeout >= 1 && a.timeout <= 30);
        calls.set(id, {name, checked:valid}); return valid;
      }
      if (event.type === 'tool-input-delta') return !!prior && prior.name === name;
      return !!prior?.checked && prior.name === name && ['tool-output-available','tool-output-error','tool-output-denied'].includes(event.type);
    };
    check.complete = () => [...calls.values()].every(call => call.checked === true);
    return check;
  }
  function liveProgress(cmd) {
    let buffer = '', seq = 0, timer = null, broken = false, chain = Promise.resolve();
    let lastActivity = '', lastAt = -Infinity, activityCount = 0;
    const names = new Map();
    function post(payload) {
      const message = {type:'CONVERSATION_PROGRESS', request_id:cmd.request_id,
        context_key:cmd.context_key, progress:{seq:++seq, ...payload}};
      chain = chain.then(() => broken ? undefined : new Promise(resolve => {
        const timeout = setTimeout(() => {broken = true; resolve();}, 12000);
        try {
          chrome.runtime.sendMessage(message, result => {
            clearTimeout(timeout);
            if (chrome.runtime.lastError || !result?.ok) broken = true;
            resolve();
          });
        } catch { clearTimeout(timeout); broken = true; resolve(); }
      }));
    }
    // 思考原文单独缓冲，按块转发；总量上限防止超长思考拖垮通道
    let rbuf = '', rtimer = null, rtotal = 0;
    function flushReasoning() {
      if (rtimer !== null) { clearTimeout(rtimer); rtimer = null; }
      if (broken) { rbuf = ''; return; }
      while (rbuf.length) {
        let n = Math.min(rbuf.length, 16000);
        const last = rbuf.charCodeAt(n - 1);
        if (n < rbuf.length && last >= 0xD800 && last <= 0xDBFF) n--;
        const delta = rbuf.slice(0, n); rbuf = rbuf.slice(n);
        post({kind:'reasoning', delta});
      }
    }
    function reasoning(delta) {
      if (cmd.activity_stream !== true || broken || typeof delta !== 'string' || !delta) return;
      if (rtotal + delta.length > 200000) return;
      rtotal += delta.length;
      flushText();  // 先发出已缓冲的正文，保证顺序；不冲刷思考缓冲，才能按块合并
      rbuf += delta;
      if (rtimer === null) rtimer = setTimeout(flushReasoning, 50);
    }
    function flush(final = false) {
      flushReasoning();
      flushText(final);
    }
    function flushText(final = false) {
      if (timer !== null) { clearTimeout(timer); timer = null; }
      if (broken) { buffer = ''; return; }
      while (buffer.length) {
        let n = Math.min(buffer.length, 16000);
        const last = buffer.charCodeAt(n - 1);
        if ((n < buffer.length || !final) && last >= 0xD800 && last <= 0xDBFF) n--;
        if (!n) break;
        const delta = buffer.slice(0, n); buffer = buffer.slice(n);
        post({delta});
      }
    }
    function activity(state, event = {}) {
      if (cmd.activity_stream !== true || broken || activityCount >= 256) return;
      let tool = typeof event.toolName === 'string' && /^[A-Za-z_][A-Za-z0-9_.-]{0,79}$/.test(event.toolName) ? event.toolName : '';
      const id = event.toolCallId;
      if (typeof id === 'string' && id.length <= 512) {
        if (tool && names.size < 256) names.set(id, tool);
        else tool = names.get(id) || '';
      }
      const now = Date.now(), key = state + ':' + tool;
      if (key === lastActivity && now - lastAt < 5000) return;
      if (!tool && now - lastAt < 500 && !['submitted','received'].includes(state)) return;
      lastActivity = key; lastAt = now; activityCount++;
      flush();
      post({kind:'activity', delta:state, ...(tool ? {tool} : {})});
    }
    return {
      add(delta) { if (!broken) { buffer += delta; if (timer === null) timer = setTimeout(() => flush(), 50); } },
      activity,
      reasoning,
      async finish() { activity('received'); flush(true); await chain; },
      discard() { if (timer !== null) clearTimeout(timer); if (rtimer !== null) clearTimeout(rtimer); timer = rtimer = null; buffer = rbuf = ''; broken = true; }
    };
  }
  async function commandFiles(cmd) {
    if (cmd.attachments === undefined) return [];
    const fail = () => new Core.ChatError('BAD_FILE', '客户端附件传递失败；未提交消息。');
    if (!Array.isArray(cmd.attachments) || cmd.attachments.length > Core.LIMITS.files) throw fail();
    const files=[];let total=0;
    for (let index=0;index<cmd.attachments.length;index++) {
      const item=cmd.attachments[index];
      if (!item || !Number.isSafeInteger(item.size) || item.size<0 || item.size>Core.LIMITS.fileBytes || !Core.MIME.includes(item.type) || item.type==='application/pdf'&&item.size>Core.LIMITS.pdfBytes || typeof item.name!=='string' || !/^[0-9a-f]{64}$/.test(item.sha256||'')) throw fail();
      total+=item.size;if(total>Core.LIMITS.totalBytes)throw fail();
      const parts=[];let offset=0,bytes=0;
      while (true) {
        if(context()?.key!==cmd.context_key)throw fail();
        const chunk=await new Promise((resolve,reject)=>{
          chrome.runtime.sendMessage({type:'CONVERSATION_ATTACHMENT',request_id:cmd.request_id,context_key:cmd.context_key,file_index:index,offset},r=>chrome.runtime.lastError?reject(fail()):resolve(r));
        });
        if(!chunk?.ok||chunk.offset!==offset||typeof chunk.data!=='string'||chunk.data.length>262144||chunk.next!==offset+chunk.data.length||typeof chunk.done!=='boolean'||!chunk.done&&chunk.data.length!==262144)throw fail();
        let raw;try{raw=atob(chunk.data)}catch{throw fail()}
        bytes+=raw.length;if(bytes>item.size)throw fail();parts.push(Uint8Array.from(raw,c=>c.charCodeAt(0)));offset=chunk.next;
        if(chunk.done)break;
      }
      const file=new File(parts,item.name,{type:item.type});
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer())),b=>b.toString(16).padStart(2,'0')).join('');
      if(bytes!==item.size||hash!==item.sha256)throw fail();
      files.push(file);
    }
    Core.validateInput(cmd.text,files);return files;
  }
  const context = () => Core.contextFromUrl(location.href);
  function getClient() {
    const ctx = context();
    if (!ctx || ctx.origin !== 'https://arena.ai') throw new Error('请打开已有 Agent/Work 对话');
    if (ctx.key !== clientKey) {
      client?.dispose(); sendSeen = false; clientKey = ctx.key;
      client = Core.createClient({getContext: context, timeouts: {streamIdle: 90000}, nativeToolPolicy: event => nativePolicy(event),
        onEvent(event) {
          if (event.type === 'accepted') progress?.activity('submitted');
          if (event.type === 'delta') {
            const native = event.event || {};
            // 思考原文直接转发；步骤标题由网关按思考段落生成
            if (native.type === 'reasoning-delta') progress?.reasoning(native.delta);
            const states = {'tool-input-start':'tool_input', 'tool-input-available':'tool_running',
              'tool-output-available':'tool_returned', 'tool-output-error':'tool_error',
              'text-delta':'generating'};
            if (states[native.type]) progress?.activity(states[native.type], native);
          }
          if (event.type === 'delta' && event.event?.type === 'text-delta') {
            if (collected.length + event.event.delta.length > 1000000) { replyOverflow = true; progress?.discard(); return; }
            if (!replyOverflow) { collected += event.event.delta; progress?.add(event.event.delta); }
          }
        }});
    }
    return client;
  }
  function history(snap) {
    let remaining = 100000;
    const messages = snap.messages.slice(-60).map(m => {
      const text = (Array.isArray(m.parts) ? m.parts : []).filter(p => p?.type === 'text' && typeof p.text === 'string').map(p => p.text).join('\n');
      const out = {role: ['user','assistant','system'].includes(m.role) ? m.role : 'other', text: text.slice(0, Math.max(0, remaining)),
        non_text: !!m.parts?.some(p => p?.type !== 'text')};
      remaining -= out.text.length; return out;
    });
    return {ok:true, title:snap.title, messages, streaming:snap.streaming,
      truncated:snap.messages.length > 60 || snap.pagination?.hasMore === true || remaining <= 0,
      note:'仅显示最近60条的文本；附件、工具详情和更早历史请在原页面查看。'};
  }
  chrome.runtime.onMessage.addListener((msg, sender, respond) => {
    if (msg.type === 'CONVERSATION_CONTEXT') {
      const ctx = context();
      respond(ctx && ctx.origin === 'https://arena.ai' ? {context_key:ctx.key, title:document.title.slice(0,160), attachment_protocol:1} : {error:'请打开已有 Agent/Work 对话'});
      return;
    }
    if (msg.type !== 'CONVERSATION_ACTION') return;
    const cmd = msg.command;
    if (!cmd || !['read','send','check'].includes(cmd.kind) || context()?.key !== cmd.context_key) {
      respond({ok:false, uncertain:false, error:'会话已切换，拒绝执行；不会发送到其它对话'}); return;
    }
    if (completed.has(cmd.request_id)) { respond(completed.get(cmd.request_id)); return; }
    if (active || typeof cmd.expires_at !== 'number' || Date.now() > cmd.expires_at) {
      respond({ok:false, uncertain:false, error:'操作已过期或已有操作进行中'}); return;
    }
    active = true;
    (async () => {
      const c = getClient();
      if (cmd.kind === 'read') return history(await c.load());
      if (cmd.kind === 'check') {
        if (c.getState().pending) await c.check();
        const result = history(await c.load());
        return {...result, resolved:sendSeen && !c.getState().pending};
      }
      if (typeof cmd.text !== 'string' || !cmd.text.trim() || cmd.text.length > Core.LIMITS.textChars) throw new Error('消息为空或超过会话发送模块的本地长度限制');
      if (cmd.client_execution === true && Core.CLIENT_EXECUTION_GUARD !== 1) throw new Core.ChatError('CLIENT_SCOPE_UNVERIFIED', '接收核心尚未更新，未发送；请重新加载扩展。');
      nativePolicy = cmd.client_execution === true ? referencePolicy(cmd.reference_reads === true) : () => true;
      collected = ''; replyOverflow = false; sendSeen = true;
      progress = cmd.stream === true ? liveProgress(cmd) : null;
      const files = await commandFiles(cmd);
      if(context()?.key!==cmd.context_key)throw new Core.ChatError('CONTEXT_CHANGED','附件准备期间会话已切换；未发送。');
      await c.send({text:cmd.text, files});
      if (cmd.client_execution === true && !nativePolicy.complete()) throw new Core.ChatError('EXECUTION_SCOPE', '原生参考工具参数未完整收到，无法确认执行域；云端操作可能已经发生，未返回本地成功。');
      if (replyOverflow) throw new Core.ChatError('STREAM_LIMIT', '回复超过本地接收上限，请回原页面查看；未返回截断成功结果。');
      await progress?.finish();
      return {ok:true, text:collected, completed:true, uncertain:false, execution_scope_verified:cmd.client_execution === true};
    })().then(result => {
      completed.set(cmd.request_id,result);
      while(completed.size > 8) completed.delete(completed.keys().next().value);
      respond(result);
    }).catch(error => {
      const safeCodes=new Set(["EXECUTION_SCOPE", "CLIENT_SCOPE_UNVERIFIED", "STREAM_CONNECT", "STREAM_READ", "INTERNAL_ERROR", "STREAM_TIMEOUT", "STREAM_ENDED", "STREAM_PROTOCOL", "STREAM_LIMIT", "NETWORK_ERROR", "MODEL_ERROR", "ACCESS_DENIED", "ACCOUNT_CHANGED", "ACK_PENDING", "ACK_UNKNOWN", "AUTH_PROTOCOL", "BAD_JSON", "BUSY", "CANCELLED", "CONTEXT_CHANGED", "CONVERSATION_CHANGED", "NO_SESSION", "PAGE_BUSY", "PROTOCOL_CHANGED", "RATE_LIMIT", "REPLY_LIMIT", "RESPONSE_LIMIT", "SESSION_MISMATCH", "SNAPSHOT_PROTOCOL", "TOOL_CONFIRMATION", "TOOL_STALE", "UNSUPPORTED_SESSION", "VERIFICATION_REQUIRED", "TIMEOUT", "OTHER"]);
      const result = {ok:false, error_code:safeCodes.has(error?.code)?error.code:'OTHER', uncertain:error?.code === 'EXECUTION_SCOPE' || client?.getState().pending === true,
        error: error instanceof Core.ChatError ? error.message : '对话读取或发送失败，请回原页面核对；未自动重发。'};
      // Fixed metadata only: never forward upstream bodies, tokens, URLs or headers.
      const stages={'读取会话':1,'获取会话授权':2,'提交消息':3,'读取回复':4,'上传附件':5,'核对本轮状态':6,'请求停止':7};
      if(error instanceof Core.ChatError){
        if(Number.isInteger(error.status)&&error.status>=100&&error.status<=599)result.upstream_status=error.status;
        if(Object.prototype.hasOwnProperty.call(stages,error.stage))result.failure_stage=stages[error.stage];
        for(const k of ['submission_attempted','submission_accepted'])if(typeof error[k]==='boolean')result[k]=error[k];
        // 只转发固定类别名，不转发上游响应原文
        if(['empty','cloudflare_challenge','cloudflare_block','captcha','rate_limit','login','json_error','html_page','other'].includes(error.hint))result.upstream_hint=error.hint;
      }
      completed.set(cmd.request_id,result); respond(result);
    }).finally(() => {progress?.discard(); progress=null; nativePolicy=() => true; active=false;});
    return true;
  });
  // Guard navigation without sending traffic. Core also checks the context at each await.
  setInterval(() => client?.cancelForNavigation(), 500);
  addEventListener('pagehide', () => client?.dispose());
})();
