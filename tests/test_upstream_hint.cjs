/* 上游错误响应只归成固定类别，不回传原文 */
'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const src=fs.readFileSync(path.join(__dirname,'../extension/chrome/conversation/chat-core.js'),'utf8');
const a=src.indexOf('function upstreamHint('),b=src.indexOf('function httpError(');
const ctx={};vm.runInNewContext(src.slice(a,b)+'\nthis.upstreamHint=upstreamHint;',ctx);
const h=ctx.upstreamHint;
test('categories',()=>{
 assert.equal(h(''),'empty');
 assert.equal(h('<!DOCTYPE html><title>Just a moment...</title>'),'cloudflare_challenge');
 assert.equal(h('<html>Sorry, you have been blocked. Cloudflare Ray ID: abc</html>'),'cloudflare_block');
 assert.equal(h('{"error":"Too many requests"}'),'rate_limit');
 assert.equal(h('{"error":"Unauthorized"}'),'login');
 assert.equal(h('{"error":"bad input"}'),'json_error');
 assert.equal(h('<html><body>x</body></html>'),'html_page');
 assert.equal(h('plain'),'other');
});
test('raw body never part of result',()=>{assert(!h('{"token":"SECRET"}').includes('SECRET'))});
