from pathlib import Path
p=Path(__file__).parent
model=(p/'src/model.mjs').read_text().replace('export ','')
ui=(p/'src/ui.js').read_text()
s="// Generated from src/model.mjs + src/ui.js. No network, tools, filesystem or eval.\nwindow.__ModuleLoader__.load({id:'dsh-step-viewer',factory:(require)=>{\nconst React=require('react'),ReactDOM=require('react-dom');\n"+model+'\n'+ui+"\nreturn {apply,inject:['uiSession','slots']};\n}});\n"
(p/'client.js').write_text(s)
