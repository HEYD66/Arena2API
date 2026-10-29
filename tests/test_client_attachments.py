"""Offline byte routing, lifecycle and capability tests. No files or network uploaded."""
import asyncio
import base64
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from client_attachments import parse_part, collect_files, AttachmentError, CHUNK_CHARS
from openai_compat import normalize_request, ProtocolError
from send_content import select_current_messages
from conversation_bridge import ConversationBridge, install_conversation_routes
from fastapi import FastAPI, HTTPException
import httpx

KEY='https://arena.ai/agent/11111111-1111-4111-8111-111111111111';CLIENT='a'*32

def filepart(data=b'hello',name='notes.txt'):
    return {'type':'file','file':{'filename':name,'file_data':base64.b64encode(data).decode()}}
def body(parts):return {'model':'fixture','messages':[{'role':'user','content':parts}]}
def engine():
    b=ConversationBridge();b.offer({'client_id':CLIENT,'context_key':KEY,'title':'fixture'})
    b.bind({'client_id':CLIENT,'context_key':KEY,'confirmed':True,'allow_clients':True})
    b.source_pin=('b'*64,'c'*32);b.attachment_capability_provider=lambda:True
    return b

class Parts(unittest.TestCase):
    def test_inline_image_and_file(self):
        image={'type':'image_url','image_url':{'url':'data:image/png;base64,aGk='}}
        b,_,_=normalize_request(body([{'type':'text','text':'look'},image,filepart()]))
        files=collect_files(b['messages']);self.assertEqual([f['size'] for f in files],[2,5])
        self.assertNotIn('base64',b['messages'][0]['content']);self.assertNotIn('aGk=',b['messages'][0]['content'])
    def test_only_current_turn(self):
        req=body([filepart(name='current.txt')]);req['messages'].insert(0,{'role':'user','content':[filepart(name='old.txt')]})
        b,_,_=normalize_request(req);self.assertEqual([f['name'] for f in collect_files(select_current_messages(b['messages']))],['current.txt'])
    def test_bad_and_remote_inputs(self):
        for part in [{'type':'image_url','image_url':{'url':'https://example.test/image.png'}},filepart(name='../secret.txt'),filepart(name='binary.exe'),{'type':'file','file':{'file_id':'file-x'}},{'type':'file','file':{'filename':'a.txt','file_data':'%%%'}}]:
            with self.subTest(part=part),self.assertRaises(ProtocolError):normalize_request(body([part]))
    def test_installed_pi_ai_tool_image_envelope(self):
        req=body('first');req['messages'] += [{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_file','arguments':'{}'}}]},{'role':'tool','tool_call_id':'call1','content':'image result'},{'role':'user','content':[{'type':'text','text':'Attached image(s) from tool result:'},{'type':'image_url','image_url':{'url':'data:image/png;base64,aGk='}}]}]
        b,_,_=normalize_request(req);selected=select_current_messages(b['messages'])
        self.assertEqual([m['role'] for m in selected],['assistant','tool','user']);self.assertEqual(selected[1]['tool_call_id'],'call1');self.assertEqual(len(collect_files(selected)),1)
    def test_limits_and_reserved(self):
        with self.assertRaises(ProtocolError):normalize_request(body([filepart()]*21))
        b=body('hello');b['messages'][0]['_client_files']=[{}]
        with self.assertRaises(ProtocolError):normalize_request(b)
    def test_tool_images_preserve_pairing(self):
        b=body('hello');b['messages'] += [{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'read_file','arguments':'{}'}}]},{'role':'tool','tool_call_id':'call1','content':[filepart()]}]
        normalized,_,_=normalize_request(b);selected=select_current_messages(normalized['messages'])
        self.assertEqual(selected[1]['tool_call_id'],'call1');self.assertEqual(len(collect_files(selected)),1)

class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_nonstream_and_cleanup(self):
        b=engine();task=asyncio.create_task(b.completion(body([filepart()])));cmd=await b.command(CLIENT)
        self.assertEqual(cmd['attachments'][0]['size'],5);self.assertNotIn('data',cmd['attachments'][0])
        self.assertEqual(b.pending[cmd['request_id']]['attachment_files'][0]['data'],'aGVsbG8=')
        b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':{'ok':True,'completed':True,'text':'done','uncertain':False}})
        await task;self.assertFalse(b.pending)
    async def test_agent_prompt_does_not_embed_bytes(self):
        b=engine();req=body([filepart()]);req['tools']=[{'type':'function','function':{'name':'read_file','parameters':{'type':'object'}}}]
        task=asyncio.create_task(b.completion(req));cmd=await b.command(CLIENT)
        self.assertNotIn('aGVsbG8=',cmd['text']);self.assertNotIn('_client_files',cmd['text'])
        self.assertTrue(cmd['client_execution'])
        b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':{'ok':True,'completed':True,'text':'done','uncertain':False,'execution_scope_verified':True}})
        await task
    async def test_cancel_cleanup(self):
        b=engine();task=asyncio.create_task(b.completion(body([filepart()])));await b.command(CLIENT);task.cancel()
        with self.assertRaises(HTTPException):await task
        self.assertFalse(b.pending);self.assertTrue(b.uncertain)
    async def test_old_extension_rejected_before_enqueue(self):
        b=engine();b.attachment_capability_provider=lambda:False
        with self.assertRaises(ProtocolError):await b.completion(body([filepart()]))
        self.assertFalse(b.pending);self.assertFalse(b.busy)
    async def test_stream_same_bytes(self):
        b=engine();req=body([filepart()]);req['stream']=True;response=await b.completion(req)
        async def consume():return [x async for x in response.body_iterator]
        t=asyncio.create_task(consume());cmd=await b.command(CLIENT);self.assertTrue(cmd['stream']);self.assertEqual(cmd['attachments'][0]['size'],5)
        b.result({'client_id':CLIENT,'context_key':KEY,'request_id':cmd['request_id'],'result':{'ok':True,'completed':True,'text':'done','uncertain':False}})
        await t;self.assertFalse(b.pending)
    async def test_chunk_capability_and_source_pin(self):
        app=FastAPI();legacy=install_conversation_routes(app,lambda r:None);b=engine()
        source={'account_key':'b'*64,'session':'c'*32}
        legacy.sources_router=SimpleNamespace(pending=lambda rid:(b,b.pending[rid]) if rid in b.pending else None,sources={CLIENT:source},account_enabled=lambda key:True)
        task=asyncio.create_task(b.completion(body([filepart(b'x'*(CHUNK_CHARS+20))])));cmd=await b.command(CLIENT)
        q={'client_id':CLIENT,'request_id':cmd['request_id'],'token':cmd['attachment_token'],'file_index':0,'offset':0}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            r=await client.get('/v1/conversation/attachment',params=q);self.assertEqual(r.status_code,200);self.assertFalse(r.json()['done']);self.assertEqual(len(r.json()['data']),CHUNK_CHARS)
            self.assertEqual((await client.get('/v1/conversation/attachment',params={**q,'token':'0'*64})).status_code,403)
            self.assertEqual((await client.get('/v1/conversation/attachment',params={**q,'offset':1})).status_code,400)
            source['session']='d'*32
            self.assertEqual((await client.get('/v1/conversation/attachment',params=q)).status_code,403)
            task.cancel()
            with self.assertRaises(HTTPException):await task
            self.assertEqual((await client.get('/v1/conversation/attachment',params=q)).status_code,404)
if __name__=='__main__':unittest.main()
