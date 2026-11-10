"""Dify 1.8 submission help and case-insensitive confirmation aliases."""
from pathlib import Path
import ast,json,yaml
ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'downloads/cabinet_project_v34_1.yml'
if not BASE.exists():BASE=ROOT.parent/'recognition-v34-1-20261110/cabinet_project_v34_1.yml'


def build():
 d=yaml.safe_load(BASE.read_text());by={n['data']['title']:n['data'] for n in d['workflow']['graph']['nodes']}
 node=by['识别明确操作，严格区分项目阶段']
 tree=ast.parse(node['code'])
 f=next(f for f in tree.body if isinstance(f,ast.FunctionDef) and f.name=='route_action')
 for i,stmt in enumerate(f.body):
  if isinstance(stmt,ast.Assign) and any(isinstance(n,ast.Name) and n.id=='aliases' for n in stmt.targets):
   extra=ast.parse("""aliases.update({'提交问题确认清单':'提交问题清单','提交问题确认单':'提交问题清单','提交问题清单表':'提交问题清单','提交BOM表':'提交选型确认','提交BOM':'提交选型确认','提交BOM确认表':'提交选型确认','提交选型确认表':'提交选型确认','提交报价确认表':'提交报价表','提交采购报价表':'提交报价表'})
aliases={re.sub(r'\\s+','',k).casefold():v for k,v in aliases.items()}
""").body
   f.body[i+1:i+1]=extra;break
 for call in ast.walk(f):
  if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id=='aliases' and call.func.attr=='get':
   call.args[0]=ast.parse("re.sub(r'\\s+','',text).casefold()",mode='eval').body
 for s in ast.walk(f):
  if isinstance(s,ast.Constant) and isinstance(s.value,str):
   if s.value=='请在聊天框上传当前版本的一份XLSX确认表，再提交。':
    s.value='提交位置：下方聊天输入框旁的附件按钮（回形针或＋）。添加当前阶段的一份XLSX，并在同一条消息发送提交命令；问题表发“提交问题清单”，BOM选型表发“提交BOM表”，报价表发“提交报价表”。'
   if s.value=='当前阶段不接受此类表格，请先“查看项目”。':
    s.value='当前阶段与所选提交命令不匹配。请先发送“查看项目”，按提示上传当前阶段的确认表；问题确认、BOM选型确认、报价确认须依次进行。'
 node['code']=ast.unparse(ast.fix_missing_locations(tree))+'\n'
 form=by['生成当前阶段确认表，保留项目版本与签名']
 form['code']=form['code'].replace("title = '产品选型确认表'", "title = 'BOM选型确认表'")
 helper='''
def submission_help(kind):
    commands={'SOURCE':('问题确认清单','提交问题清单'),'SELECTION':('BOM选型确认表','提交BOM表'),'QUOTE':('采购报价确认表','提交报价表')}
    label,command=commands[kind]
    return '\\n\\n本轮要回传：'+label+'。\\n提交位置：下方聊天输入框旁的附件按钮（回形针或＋）。\\n操作：下载并填写XLSX → 在本对话添加这一份XLSX → 在同一条消息输入“'+command+'”并点发送。也可发送“提交”，系统按当前阶段处理。\\n“提交BOM表”与“提交选型确认”含义相同。带价格的采购报价表使用“提交报价表”。'
'''
 form['code']+='\n'+helper
 needle='append_approver(state, sheets, text)';assert needle in form['code']
 form['code']=form['code'].replace(needle,'text+=submission_help(kind)\n    '+needle)
 # Status is deliberately explicit even after the initial shortcut buttons disappear.
 status_code='''
def full_status(state):
    if not state:return '首次在上方填写项目、上传图纸PDF并发送“开始选型”。之后的确认表都在下方聊天附件（回形针或＋）上传。'
    base='项目：%s；版本：%s；阶段：%s。'%(state['name'],state['revision'],STAGE_NAMES.get(state['stage'],state['stage']))
    stage=state['stage']
    hints={'SOURCE_REVIEW':'本轮回传问题确认清单：在聊天附件上传填好的XLSX，同一条消息发送“提交问题清单”。',
    'SELECTION_REVIEW':'本轮回传BOM选型确认表：在聊天附件上传填好的XLSX，同一条消息发送“提交BOM表”（也叫“提交选型确认”）。',
    'QUOTE_REVIEW':'本轮回传采购报价确认表：在聊天附件上传填好的XLSX，同一条消息发送“提交报价表”。',
    'MATCH_PENDING':'当前等待数据库产品匹配。', 'PRICE_PENDING':'当前等待报价参考记录。',
    'QUOTE_CONFIRMED':'报价已确认，可以发送“生成排产资料”。','PRODUCTION_READY':'排产资料已生成；发送“查看项目”查看当前状态。'}
    return base+'\\n'+hints.get(stage,'当前正在处理项目资料。')+'\\n顺序：问题确认 → 数据库选型 → BOM选型确认 → 报价确认 → 排产资料。'
'''
 for n in d['workflow']['graph']['nodes']:
  data=n['data']
  if data['type']=='code' and 'def full_status(' in data.get('code',''):data['code']+='\n'+status_code
  if data['type']=='start':
   data['title']='首次传PDF；确认表从下方聊天附件提交'
   for v in data['variables']:
    if v['variable']=='pdf_files':v['label']='客户图纸PDF（首次传图用）'
    if v['variable']=='technical_files':v['label']='技术协议PDF（首次选填）'
  if data['type']=='code':compile(data['code'],data['title'],'exec')
 d['app']['name']='成套柜项目助手 V34.2（明确问题表与BOM提交入口）'
 d['workflow']['features']['opening_statement']='首次：填写项目并上传图纸PDF，发送“开始选型”。\\n后续确认表统一从下方聊天附件（回形针或＋）上传，每次一份XLSX并在同一条消息输入提交命令：\\n①问题确认清单 → 提交问题清单；\\n②数据库选型后生成的BOM选型确认表 → 提交BOM表（或提交选型确认）；\\n③采购报价确认表 → 提交报价表。\\n按钮看不到时直接输入命令。可发送“查看项目”查看当前阶段。'
 # Actual newlines, not literal backslash-n text in the product UI.
 d['workflow']['features']['opening_statement']=d['workflow']['features']['opening_statement'].replace('\\n','\n')
 (ROOT/'cabinet_project_v34_2.yml').write_text(yaml.safe_dump(d,allow_unicode=True,sort_keys=False))
 print(json.dumps({'nodes':len(d['workflow']['graph']['nodes']),'edges':len(d['workflow']['graph']['edges'])}));return d
if __name__=='__main__':build()
