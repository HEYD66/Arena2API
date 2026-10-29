import json,subprocess,sys,os,shutil,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];(root/'.test-tmp').mkdir(exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='organized-tests-',dir=root/'.test-tmp'))
for directory in ['','gateway','desktop','tests']:
 dest=stage/directory;dest.mkdir(exist_ok=True)
 for pattern in ['*.py','*.cjs','*.mjs','*.html','*.js']:
  for p in (root/directory).glob(pattern):shutil.copyfile(p,dest/p.name)
shutil.copytree(root/'extension/chrome',stage/'extension/chrome',ignore=shutil.ignore_patterns('node_modules','__pycache__'))
shutil.copyfile(root/'tool_reference_manifest.json',stage/'tool_reference_manifest.json');assert not (stage/'.console').exists()
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',API_KEY='offline-fixture-key')
names=[p.name for p in (stage/'tests').glob('test_*.py') if p.name not in ['test_desktop_native.py','test_desktop_ui.py']]
py="import os,sys; sys.path.insert(0,os.getcwd()); import unittest; names="+repr(names)+"; s=unittest.TestSuite(); [s.addTests(unittest.defaultTestLoader.discover('tests',pattern=n)) for n in names]; r=unittest.TextTestRunner(verbosity=1).run(s); raise SystemExit(not r.wasSuccessful())"
def run(args,timeout=90):
 try:
  r=subprocess.run(args,cwd=stage,env=env,capture_output=True,text=True,encoding='utf8',errors='replace',timeout=timeout);return {'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
 except subprocess.TimeoutExpired:return {'exit_code':124,'stdout':'','stderr':'fixture runner deadline exceeded'}
pyr=run([sys.executable,'-X','utf8','-c',py]);js={p.name:run(['node',str(p)],30) for p in sorted((stage/'tests').glob('*.cjs'))}
extra={}
if os.name=='nt' and os.environ.get('DSH_NODE_MODULES'):
 extra['actual_dsh_lifecycle']=run(['node','tests/test_dsh_receive_lifecycle.mjs'],30)
 w=run([sys.executable,'tests/test_activity_stream.py','--wire']);wire=stage/'activity-wire.json';wire.write_text(w['stdout'],encoding='utf8')
 extra['installed_dsh_protocol']=run(['node','tests/test_dsh_progress_installed.mjs',str(wire)],20)
result={'ok':all(v['exit_code']==0 for v in [pyr,*js.values(),*extra.values()]),'python':pyr,'javascript':js,'extra':extra,'stage':str(stage),'live_requests':0,'production_config_copied':False}
(root/'.test-tmp/organized-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print('VALIDATION',result['ok']);print(pyr['stderr'][-2500:]);print('JS_SUITES',len(js));print({k:v for k,v in js.items() if v['exit_code']});print(extra);sys.exit(not result['ok'])
