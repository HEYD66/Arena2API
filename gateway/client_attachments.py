"""Inline client attachments only. No URL fetching, filesystem reads or persistence."""
import base64
import binascii
import hashlib
import re
import unicodedata

MiB=1024*1024
MAX_FILES=20
MAX_TOTAL=50*MiB
CHUNK_CHARS=256*1024  # Multiple of four; below the existing CDP response ceiling.
MIME={'image/png','image/jpeg','image/gif','image/webp','text/plain','text/markdown','text/csv','text/html','text/xml','text/css','text/javascript','application/json','application/xml','application/javascript','application/pdf'}
EXT={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','gif':'image/gif','webp':'image/webp','pdf':'application/pdf','txt':'text/plain','md':'text/markdown','csv':'text/csv','json':'application/json','xml':'application/xml','html':'text/html','css':'text/css','js':'text/javascript'}
EXT.update({x:'text/plain' for x in ('py','ts','tsx','jsx','c','h','cpp','java','rs','go','sh','yaml','yml')})
class AttachmentError(ValueError):pass

def parse_part(part, index):
    kind=part.get('type')
    if kind=='image_url':
        info=part.get('image_url');value=info.get('url') if isinstance(info,dict) else None
        name=None
    elif kind=='file':
        info=part.get('file');value=info.get('file_data') if isinstance(info,dict) else None
        name=info.get('filename') if isinstance(info,dict) else None
        if not isinstance(name,str):raise AttachmentError('Inline file requires filename and file_data; file_id/path/URL are not supported')
    else:raise AttachmentError('Supported parts: text, image_url with inline data URL, file with inline file_data')
    if not isinstance(value,str):raise AttachmentError('Attachment bytes must be supplied inline')
    if value.startswith('data:'):
        match=re.match(r'^data:([a-zA-Z0-9.+/-]+);base64,',value)
        if not match:raise AttachmentError('Expected a base64 data URL')
        mime=match[1].lower();encoded=value[match.end():]
    else:
        if kind=='image_url':raise AttachmentError('Remote image URLs and local paths are not fetched; supply a base64 data URL')
        mime=EXT.get(name.rsplit('.',1)[-1].lower(),'');encoded=value
    if mime not in MIME or kind=='image_url' and not mime.startswith('image/'):
        raise AttachmentError('Unsupported attachment MIME type')
    maximum=(10 if mime=='application/pdf' else 25)*MiB
    if len(encoded)>4*((maximum+2)//3):raise AttachmentError('Attachment exceeds existing upload size limit')
    try:data=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise AttachmentError('Invalid attachment base64') from None
    if len(data)>maximum or base64.b64encode(data).decode()!=encoded:raise AttachmentError('Invalid or oversized attachment base64')
    if name is None:name=f'image-{index+1}.'+{'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif'}[mime]
    name=unicodedata.normalize('NFC',name)
    if not name or len(name)>255 or re.search(r'[\x00-\x1f\x7f/\\]',name) or name in ('.','..'):
        raise AttachmentError('Attachment filename must be a simple filename, not a path')
    return {'name':name,'type':mime,'size':len(data),'data':encoded,'sha256':hashlib.sha256(data).hexdigest()}

def validate_batch(files):
    if len(files)>MAX_FILES or sum(f['size'] for f in files)>MAX_TOTAL:
        raise AttachmentError('Existing upload limits: at most 20 attachments, 50 MiB total per send')
    return files

def collect_files(messages):
    return validate_batch([f for m in messages for f in m.get('_client_files',[])])

def descriptor(file):return {k:v for k,v in file.items() if k!='data'}
