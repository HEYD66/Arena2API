"""Offline logging contract. No sockets, browser, messages or credentials."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'desktop'))
import gateway_log as g
from core import ConsoleCore
from conversation_bridge import ConversationBridge
from conversation_stream import LiveReply

class LoggingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        d=ROOT/'.test-tmp';d.mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=d);self.root=Path(self.tmp.name)
        self.sink=g.EventLog(self.root/'logs/gateway-events.jsonl');self.patch=patch.object(g,'_sink',self.sink);self.patch.start()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def rows(self):return [json.loads(x) for x in self.sink.path.read_text().splitlines()]
    async def run_app(self, app, receiver=None):
        sent=[]
        async def send(m):sent.append(m)
        async def receive():return {'type':'http.disconnect'}
        await g.TraceMiddleware(app)({'type':'http','path':'/v1/chat/completions'},receiver or receive,send)
        return sent
    async def test_http_end_and_trace_header(self):
        async def app(s,r,w):
            await w({'type':'http.response.start','status':200,'headers':[]})
            await w({'type':'http.response.body','body':b'PRIVATE BODY','more_body':False})
        sent=await self.run_app(app);rows=self.rows()
        self.assertEqual([x['event'] for x in rows],['request_received','http_started','http_finished'])
        self.assertEqual(dict(sent[0]['headers'])[b'x-arena-trace-id'].decode(),rows[0]['trace'])
        self.assertEqual(rows[-1]['bytes'],12);self.assertNotIn('PRIVATE',self.sink.path.read_text());self.assertIsNone(g.CURRENT.get())
    async def test_exception_sanitized(self):
        async def app(s,r,w):raise RuntimeError('SECRET TOKEN')
        with self.assertRaises(RuntimeError):await self.run_app(app)
        self.assertNotIn('SECRET',self.sink.path.read_text());self.assertIn('http_error',[x['event'] for x in self.rows()])
    async def test_disconnect_recorded(self):
        async def app(s,r,w):await r()
        await self.run_app(app);self.assertIn('client_disconnected',[x['event'] for x in self.rows()])
    async def test_allowlist_and_rotation(self):
        self.sink.limit=400
        for _ in range(30):self.sink.write({'trace':'a'*32,'event':'http_error','status':500,'body':'SECRET','error_code':'STREAM_TIMEOUT','path':'SECRET'})
        files=list(self.sink.path.parent.iterdir());self.assertLessEqual(len(files),4)
        self.assertTrue(Path(str(self.sink.path)+'.1').exists())
        self.assertNotIn('SECRET',''.join(p.read_text() for p in files))
    async def test_write_failure_does_not_break_request(self):
        self.sink.path=self.root  # Directory cannot be opened as a log file.
        g.Trace().event('request_received');self.assertGreater(self.sink.failures,0)
    async def test_command_and_progress_share_trace(self):
        b=ConversationBridge();cid='a'*32;key='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
        b.offer({'client_id':cid,'context_key':key});b.bind({'client_id':cid,'context_key':key,'confirmed':True,'allow_clients':True})
        t=g.Trace();token=g.CURRENT.set(t)
        try:task=asyncio.create_task(b.execute('send',revision=b.binding['revision'],text='SECRET',confirmed=True,progress=LiveReply()))
        finally:g.CURRENT.reset(token)
        cmd=await b.command(cid);data={'client_id':cid,'context_key':key,'request_id':cmd['request_id']}
        b.result(dict(data,progress={'seq':1,'delta':'SECRET'}));b.result(dict(data,progress={'seq':1,'delta':'SECRET'}))
        b.result(dict(data,result={'ok':True,'completed':True,'text':'SECRET'}));await task
        rows=self.rows();self.assertEqual({r['trace'] for r in rows},{t.id});self.assertEqual(sum(r['event']=='first_delta' for r in rows),1)
        self.assertEqual(rows[-1]['delta_count'],1);self.assertNotIn('SECRET',self.sink.path.read_text());self.assertNotIn(key,self.sink.path.read_text())
    async def test_reader_keeps_only_metadata_and_handles_partial_line(self):
        g.Trace().event('request_received');self.sink.path.open('a').write('{unfinished')
        v=ConsoleCore(self.root).gateway_logs();self.assertTrue(v['available']);self.assertEqual(len(v['events']),1)
    async def test_real_asgi_stream_lifecycle(self):
        import httpx, server
        b=ConversationBridge();cid='a'*32;key='https://arena.ai/agent/11111111-1111-4111-8111-111111111111'
        b.offer({'client_id':cid,'context_key':key});b.bind({'client_id':cid,'context_key':key,'confirmed':True,'allow_clients':True})
        b.execute=AsyncMock(return_value={'ok':True,'completed':True,'text':'PRIVATE REPLY'})
        with patch.object(server,'conversation_bridge',b):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
                r=await c.post('/v1/chat/completions',headers={'Authorization':'Bearer '+server.API_KEY},json={'model':'arena-bound','stream':True,'messages':[{'role':'user','content':'PRIVATE PROMPT'}]})
                self.assertEqual(r.status_code,200);self.assertIn('[DONE]',r.text)
        events=[x['event'] for x in self.rows()]
        self.assertIn('stream_success',events);self.assertEqual(events[-1],'http_finished')
        self.assertNotIn('PRIVATE',self.sink.path.read_text())
    async def test_nonstream_http_rejection_is_logged(self):
        import httpx, server
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
            r=await c.post('/v1/chat/completions',json={'model':'arena-bound'})
            self.assertEqual(r.status_code,401)
        self.assertEqual(self.rows()[-1]['status'],401)
    async def test_missing_log_message(self):
        v=ConsoleCore(self.root).gateway_logs();self.assertFalse(v['available'])
    async def test_concurrent_request_ids_distinct(self):
        async def app(s,r,w):
            await asyncio.sleep(0)
            await w({'type':'http.response.start','status':403,'headers':[]})
            await w({'type':'http.response.body','body':b'error'})
        await asyncio.gather(self.run_app(app),self.run_app(app));self.assertEqual(len({x['trace'] for x in self.rows()}),2)
if __name__=='__main__':unittest.main()
