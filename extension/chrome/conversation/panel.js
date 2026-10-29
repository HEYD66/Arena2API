/* Page-local settings only. No account/history fetch, page-world bridge, or chat commands. */
(() => {
  "use strict";
  if (
    location.origin !== "https://arena.ai" ||
    window.top !== window ||
    document.getElementById("arena2api-settings-host")
  )
    return;
  const host = document.createElement("div");
  host.id = "arena2api-settings-host";
  host.style.cssText =
    "all:initial!important;position:fixed!important;inset:0!important;width:100vw!important;height:100dvh!important;z-index:2147483000!important;pointer-events:none!important;display:block!important;";
  const root = host.attachShadow({ mode: "closed" });
  // 视觉与桌面控制台保持一致：单色、直角、状态点；常用操作只有一个开关
  root.innerHTML = `<style>
:host{color-scheme:dark}
*{box-sizing:border-box}
button,input{font:inherit}
button{cursor:pointer;color:#f0f0f0;background:#242424;border:1px solid #555;border-radius:0;padding:8px 12px;min-height:36px}
button:hover{background:#2e2e2e}
button:disabled{opacity:.45;cursor:default}
button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid #f0f0f0;outline-offset:2px}
[hidden]{display:none!important}
#entry{position:absolute;right:0;top:42%;pointer-events:auto;width:32px;min-height:0;padding:12px 6px;background:#181818;color:#f0f0f0;border:1px solid #555;border-right:0;font:600 11px/1.5 'Segoe UI',sans-serif;writing-mode:vertical-rl;letter-spacing:1px;box-shadow:0 2px 10px #0006}
#panel{position:absolute;right:12px;top:60px;max-height:calc(100dvh - 72px);width:min(320px,calc(100vw - 24px));pointer-events:auto;overflow:auto;overscroll-behavior:contain;padding:16px;background:#111;color:#f0f0f0;border:1px solid #555;box-shadow:0 12px 36px #0008;font:13px/1.6 'Segoe UI','Microsoft YaHei',sans-serif;text-align:left}
header{display:flex;align-items:center;justify-content:space-between;gap:8px}
h2{font-size:15px;margin:0}
small,p,label,summary{color:#b0b0b0}
small{font-size:11px;letter-spacing:1px}
section{display:flex;gap:10px;align-items:flex-start;padding:12px;background:#181818;border:1px solid #353535;margin:12px 0}
#dot{flex:none;width:8px;height:8px;margin-top:6px;border-radius:50%;background:#666}
#dot.good{background:#4caf6a}
#dot.warn{background:#d9a23b}
b{display:block;color:#f0f0f0}
#detail,#error{overflow-wrap:anywhere;font-size:12px;margin:2px 0 0;color:#b0b0b0}
#error{color:#ffb7a0}
#toggle{width:100%;font-weight:600;font-size:14px;min-height:40px}
#toggle[aria-pressed="false"]{background:#f0f0f0;color:#111;border-color:#f0f0f0}
#toggle[aria-pressed="false"]:hover{background:#d8d8d8}
details{margin-top:12px;border-top:1px solid #353535;padding-top:8px}
summary{cursor:pointer;font-size:12px}
label{display:block;margin-top:10px;font-size:12px}
input[type=text]{width:100%;margin-top:4px;padding:8px;background:#0b0b0b;border:1px solid #555;color:#f0f0f0}
.hint{font-size:11px;margin:4px 0 0}
#reset{width:100%;margin-top:12px;background:transparent;font-size:12px}
#confirmBox{border:1px solid #8a6a3a;background:#241c12;padding:10px;margin-top:10px}
#confirmBox p{color:#f0d2a8;font-size:12px;margin:0 0 8px}
.actions{display:flex;gap:8px;justify-content:flex-end}
#close{min-height:0;padding:4px 10px}
</style>
<button id="entry" title="Arena2API 接入设置" aria-label="展开 Arena2API 接入设置" aria-expanded="false">A2API</button>
<aside id="panel" role="dialog" aria-modal="false" aria-labelledby="heading" hidden>
<header><div><small>ARENA2API · 1.6.0</small><h2 id="heading">接入本机网关</h2></div><button id="close" aria-label="收起接入设置">收起</button></header>
<section aria-live="polite"><span id="dot" aria-hidden="true"></span><div><b id="status">正在检查…</b><p id="detail"></p></div></section>
<button id="toggle" aria-pressed="false" disabled>正在读取开关状态…</button><p id="error" role="alert" hidden></p>
<p class="hint">开启后，控制台可以看到这个账号和它的会话列表，不会自动发消息。</p>
<details id="advanced"><summary>高级</summary>
<label for="url">网关地址<input type="text" id="url" value="http://127.0.0.1:9090" autocomplete="off" spellcheck="false"></label>
<p class="hint">一般不用改。要改的话先停止接入，并和控制台“设置”里的端口一致。</p>
<button id="reset">复制过浏览器配置？重新生成浏览器标识</button>
<div id="confirmBox" hidden><p>重置后此浏览器会停止接入，原来的会话设置失效，需要重新启用并在控制台重新添加。不会退出账号或删除会话。</p><div class="actions"><button id="cancelReset">取消</button><button id="confirmReset">确认重置</button></div></div></details>
</aside>`;
  document.documentElement.appendChild(host);
  const $ = (id) => root.getElementById(id);
  let opened = false,
    timer = null,
    busy = false,
    epoch = 0,
    previousFocus = null,
    draft = 0,
    enabled = false,
    known = false,
    currentUrl = "http://127.0.0.1:9090",
    resetDone = false;
  const fail = (text) => {
    $("error").textContent = text || "";
    $("error").hidden = !text;
  };
  function controls() {
    for (const id of ["reset", "confirmReset"]) $(id).disabled = busy || resetDone;
    $("url").disabled = busy || enabled || !known || resetDone;
    $("toggle").disabled = busy || !known || resetDone;
    $("toggle").setAttribute("aria-pressed", String(enabled));
    $("toggle").textContent = resetDone
      ? "刷新页面后重新启用"
      : busy
        ? "正在切换…"
        : !known
          ? "正在读取开关状态…"
          : enabled
            ? "停止接入"
            : "启用接入";
  }
  function send(message) {
    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(message, (result) => {
          if (chrome.runtime.lastError || !result)
            reject(Error("扩展连接已失效，请刷新此 Arena 页面后重试。"));
          else resolve(result);
        });
      } catch {
        reject(Error("扩展已重载，请刷新此 Arena 页面后重试。"));
      }
    });
  }
  async function update(fill = false) {
    const generation = epoch,
      version = draft;
    try {
      const s = await send({ type: "CONVERSATION_STATUS" });
      if (!opened || generation !== epoch) return;
      enabled = s.enabled === true;
      known = true;
      currentUrl = s.url || "http://127.0.0.1:9090";
      if (fill && version === draft) $("url").value = currentUrl;
      controls();
      $("dot").className = !enabled ? "" : s.offered ? "good" : "warn";
      $("status").textContent = !enabled
        ? "接入已停止"
        : s.offered
          ? "浏览器在线 · " + (s.account || "已识别账号")
          : "已开启 · 等待网关";
      $("detail").textContent =
        (!enabled ? "控制台看不到这个浏览器。不会退出账号或删除会话。" : s.error) ||
        (s.offered
          ? "回到控制台，在“添加账号”里选择这个浏览器。"
          : "请确认已登录 Arena，并在控制台点“启动 / 检查连接”。");
    } catch (e) {
      if (opened && generation === epoch) {
        known = false;
        controls();
        $("dot").className = "warn";
        $("status").textContent = "暂时无法连接扩展";
        fail(e.message);
      }
    }
  }
  function show(value) {
    if (opened === value) return;
    opened = value;
    epoch++;
    $("panel").hidden = !value;
    $("entry").hidden = value;
    $("entry").setAttribute("aria-expanded", String(value));
    clearInterval(timer);
    timer = null;
    $("confirmBox").hidden = true;
    if (value) {
      previousFocus = document.activeElement;
      fail("");
      void update(true);
      timer = setInterval(() => {
        if (!busy) void update();
      }, 4000);
      $("close").focus();
    } else if (previousFocus?.isConnected && previousFocus !== host) previousFocus.focus();
    else $("entry").focus();
  }
  $("url").addEventListener("input", () => {
    draft++;
  });
  $("entry").onclick = () => show(true);
  $("close").onclick = () => show(false);
  root.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape" && opened) {
      e.preventDefault();
      show(false);
    }
  });
  root.addEventListener("keyup", (e) => e.stopPropagation());
  $("toggle").onclick = async () => {
    if (busy || !known || resetDone) return;
    const next = !enabled;
    epoch++;
    busy = true;
    controls();
    fail("");
    try {
      const r = await send({
        type: "WORKSPACE_CONFIG",
        enabled: next,
        url: next ? $("url").value : currentUrl,
      });
      if (r.ok !== true) throw Error(r.error || "开关未保存，状态保持不变。");
      enabled = next;
      await update();
    } catch (e) {
      fail(e.message);
    } finally {
      busy = false;
      controls();
    }
  };
  $("reset").onclick = () => {
    if (!busy) {
      $("confirmBox").hidden = false;
      $("cancelReset").focus();
    }
  };
  $("cancelReset").onclick = () => {
    $("confirmBox").hidden = true;
    $("reset").focus();
  };
  $("confirmReset").onclick = async () => {
    if (busy || $("confirmBox").hidden) return;
    busy = true;
    controls();
    fail("");
    try {
      const r = await send({ type: "WORKSPACE_RESET", confirmed: true });
      if (r.ok !== true) throw Error(r.error || "重置失败");
      epoch++;
      enabled = false;
      resetDone = true;
      $("confirmBox").hidden = true;
      $("dot").className = "";
      $("status").textContent = "浏览器标识已停用并重置";
      $("detail").textContent = "请刷新此 Arena 页面，再启用接入，并在控制台重新添加会话。";
      clearInterval(timer);
      timer = null;
    } catch (e) {
      fail(e.message);
    } finally {
      busy = false;
      controls();
    }
  };
  chrome.runtime.onMessage.addListener((msg, sender, respond) => {
    if (msg?.type !== "WORKSPACE_PANEL_TOGGLE") return;
    if (sender.id && sender.id !== chrome.runtime.id) {
      respond({ ok: false });
      return;
    }
    show(!opened);
    respond({ ok: true, opened });
  });
  window.addEventListener("pagehide", () => {
    clearInterval(timer);
    timer = null;
  });
})();
