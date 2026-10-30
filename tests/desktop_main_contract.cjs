// Isolated Electron-main contract checks: no real app, CLI, credentials or orders.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),vm=require('node:vm'),crypto=require('node:crypto');
const {EventEmitter}=require('node:events');const {PassThrough}=require('node:stream');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'ta-main-contract-'));
const actual=path.resolve(__dirname,'../desktop');
const fakePython=path.join(tmp,'.venv/bin/python');fs.mkdirSync(path.dirname(fakePython),{recursive:true});fs.writeFileSync(fakePython,'');fs.mkdirSync(path.join(tmp,'.venv/lib/python3.12/site-packages'),{recursive:true});
fs.writeFileSync(path.join(tmp,'trading-desktop.json'),JSON.stringify({repo:tmp,python:fakePython,pythonExecutable:fakePython,pythonSitePackages:path.join(tmp,'.venv/lib/python3.12/site-packages')}));
const hooks={},requests=[];let response=0,win,pid=1000;
const preview={id:'virtual-test-order',preview_hash:'test-preview',status:'PROPOSED',notional:5000000,estimated_fee:7500,available_cash:100000000,order:{ticker:'FPT.VN',quantity:100,side:'Buy',limit_price:50000,exchange:'HOSE',asof:'2026-10-29',analysis_date:'2026-10-30',rationale:'Isolated test only',data_gaps:[]}};
const meta={version:'test',config:{},history:[],providers:[]};
function spawn(_file,_args,options){
 assert.equal(options.cwd,tmp,'bridge cwd must avoid Documents getcwd stalls');
 assert.equal(_args[0],'-S');assert.equal(_args[1],'-c');assert.ok(_args[3].endsWith('site-packages'));assert.ok(_args[2].includes('site.addsitedir'));
 const child=new EventEmitter();child.pid=pid++;child.stdout=new PassThrough();child.stderr=new PassThrough();
 child.stdin={on(){},end(data){const request=JSON.parse(data);requests.push({...request,env:options.env});queueMicrotask(()=>{let payload={ok:true};if(request.action==='meta')payload=meta;if(request.action==='paper_preview')payload=preview;if(request.action==='paper_approve')payload={status:'APPROVED'};child.stdout.write(JSON.stringify({type:'result',payload})+'\n');child.emit('close',0);});}};
 return child;
}
class Window{constructor(){win=this;this.webContents={send(){},setWindowOpenHandler(){},on(){},session:{setPermissionRequestHandler(){}}};}loadFile(){return Promise.resolve();}on(){}isDestroyed(){return false;}}
const app={setName(){},setPath(){},getPath(){return tmp;},disableHardwareAcceleration(){},commandLine:{appendSwitch(){}},whenReady(){return Promise.resolve();},setAboutPanelOptions(){},on(name,fn){hooks[name]=fn;},quit(){}};
const electron={app,BrowserWindow:Window,ipcMain:{handle(name,fn){hooks[name]=fn;}},dialog:{async showMessageBox(){return {response};}},shell:{},nativeTheme:{},Menu:{setApplicationMenu(){},buildFromTemplate(x){return x;}},clipboard:{}};
const proc={resourcesPath:tmp,env:{PATH:'/test/bin',PYTHONHOME:'/wrong/home',PYTHONPATH:'/wrong/path',__PYVENV_LAUNCHER__:'/wrong/python',TRADINGAGENTS_PAPER_APPROVAL_KEY:'must-not-leak'},on(){},kill(){}};
const context={require:name=>name==='electron'?electron:name==='node:child_process'?{spawn,execFile(){}}:require(name),__dirname:actual,process:proc,URL,Intl,Promise,setTimeout,clearTimeout};
(async()=>{
 try{
  vm.runInNewContext(fs.readFileSync(path.join(actual,'main.cjs'),'utf8'),context);
  await Promise.resolve();const send=(action,payload={})=>hooks['ta:request']({sender:win.webContents},action,payload);
  assert.equal(await send('bootstrap'),null);
  await assert.rejects(send('start',{action:'analyze'}),/khởi động/);
  await Promise.all([send('meta'),send('meta')]);
  assert.equal(requests.filter(x=>x.action==='meta').length,1,'deduplicate startup metadata');
  assert.equal((await send('bootstrap')).version,'test');
  const environment=requests[0].env;
  for(const name of ['PYTHONHOME','PYTHONPATH','__PYVENV_LAUNCHER__','TRADINGAGENTS_PAPER_APPROVAL_KEY'])assert.equal(environment[name],undefined);
  const cancelled=await send('paper_review',{id:preview.id});assert.equal(cancelled.cancelled,true);
  assert.equal(requests.filter(x=>x.action==='paper_approve').length,0,'cancel never submits');
  response=1;await send('paper_review',{id:preview.id});
  const approved=requests.find(x=>x.action==='paper_approve');assert.ok(approved);
  const signature=crypto.createHmac('sha256',approved.env.TRADINGAGENTS_PAPER_APPROVAL_KEY).update(`${preview.id}|${preview.preview_hash}|${approved.payload.approval.timestamp}`).digest('hex');
  assert.equal(approved.payload.approval.signature,signature);
  assert.notEqual(approved.env.TRADINGAGENTS_PAPER_APPROVAL_KEY,'must-not-leak');
  await assert.rejects(send('paper_approve',{id:preview.id}),/Unsupported desktop action/);
  await assert.rejects(send('start',{action:'paper_approve'}),/Unsupported TradingAgents job/);
  console.log('Electron main: startup gate, environment isolation, cancel/confirm and renderer approval protection passed');
 }finally{fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
