const {app,BrowserWindow,ipcMain,dialog,shell,nativeTheme,Menu,clipboard}=require('electron');
const fs=require('node:fs');
const path=require('node:path');
const {spawn,execFile}=require('node:child_process');
const manifest=JSON.parse(fs.readFileSync(path.join(process.resourcesPath,'trading-desktop.json'),'utf8'));
app.setName('Trading Agents');
const desktopUserData=path.join(manifest.repo,'work','desktop-userdata');
fs.mkdirSync(desktopUserData,{recursive:true});app.setPath('userData',desktopUserData);
let window,active=null,quitting=false;
const quickActions=new Set(['meta','history','report','memory','save_config','doctor','subscription_status','subscription_redirect','subscription_logout','subscription_models','api_key_status','save_api_key','remove_api_key']);
const jobActions=new Set(['analyze','backtest','settle','subscription_login','test_provider','review_council']);
function send(type,payload){if(window&&!window.isDestroyed())window.webContents.send('ta:event',{type,payload});}
function bridge(action,payload={},long=false){
 return new Promise((resolve,reject)=>{
  const child=spawn(manifest.python,[path.join(manifest.repo,'desktop','bridge.py')],{
   cwd:manifest.repo,env:{...process.env,PYTHONUNBUFFERED:'1',PATH:[path.join(app.getPath('home'),'.local','bin'),'/opt/homebrew/bin','/usr/local/bin',process.env.PATH||''].join(':')},
   stdio:['pipe','pipe','pipe'],detached:true
  });
  let buffer='',result,error,stderr=false;
  if(long)active={child,action,payload,started:Date.now(),cancelled:false};
  child.stdout.setEncoding('utf8');child.stdout.on('data',chunk=>{
   buffer+=chunk;let end;
   while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!line.trim())continue;
    try{const event=JSON.parse(line);if(event.type==='result')result=event.payload;else if(event.type==='error')error=event.payload;else if(long)send(event.type,event.payload);}catch{stderr=true;}
   }
  });
  child.stderr.on('data',()=>{stderr=true;});
  child.on('error',err=>{if(long)active=null;reject(new Error('Không mở được Python của TradingAgents: '+err.code));});
  child.on('close',code=>{
   const cancelled=long&&active?.cancelled;const action=long?active?.action:null;
   if(long)active=null;
   if(cancelled){send('cancelled',{message:action==='subscription_login'?'Đã hủy phiên đăng nhập ChatGPT.':'Đã dừng. Có thể chạy lại cùng mã, ngày và cấu hình để tiếp tục từ checkpoint.'});resolve({cancelled:true});}
   else if(error||code!==0||result===undefined){const message=error?.message||'Tiến trình TradingAgents kết thúc trước khi trả kết quả.';if(long)send('failed',{message,type:error?.type||'ProcessError'});reject(new Error(message));}
   else {if(long)send('completed',result);resolve(result);}
  });
  child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify({action,payload}));
 });
}
function stopJob(){
 if(!active)return Promise.resolve(false);
 const owned=active;owned.cancelled=true;
 return new Promise(resolve=>{
  execFile('/bin/ps',['-axo','pid=,ppid='],(error,stdout)=>{
   const descendants=new Set([owned.child.pid]);
   if(!error){const rows=stdout.trim().split('\n').map(x=>x.trim().split(/\s+/).map(Number));let changed=true;
    while(changed){changed=false;for(const [pid,ppid]of rows)if(descendants.has(ppid)&&!descendants.has(pid)){descendants.add(pid);changed=true;}}
   }
   for(const pid of [...descendants].reverse()){try{process.kill(pid,'SIGTERM');}catch{}}
   const timer=setTimeout(()=>{if(active===owned){for(const pid of descendants){try{process.kill(pid,'SIGKILL');}catch{}}}resolve(true);},800);timer.unref();
  });
 });
}
function checkSender(event){if(!window||event.sender!==window.webContents)throw new Error('Invalid desktop sender');}
ipcMain.handle('ta:request',async(event,action,payload)=>{
 checkSender(event);
 if(quickActions.has(action))return bridge(action,payload||{});
 if(action==='clear_checkpoints'){if(active)throw new Error('Dừng tác vụ đang chạy trước khi xóa checkpoint.');const answer=await dialog.showMessageBox(window,{type:'warning',buttons:['Giữ lại','Xóa checkpoint'],defaultId:0,cancelId:0,title:'Chạy mới',message:'Xóa toàn bộ checkpoint đã lưu?',detail:'Không xóa báo cáo hoặc memory. Các lượt đang dở sẽ không thể tiếp tục từ checkpoint cũ.'});if(answer.response!==1)return {cancelled:true};return bridge('clear_checkpoints');}
 if(action==='status')return active?{action:active.action,payload:active.payload,started:active.started}:null;
 if(action==='start'){
  if(active)throw new Error('Một tác vụ đang chạy. Hãy đợi hoặc dừng tác vụ đó.');
  if(!jobActions.has(payload?.action))throw new Error('Unsupported TradingAgents job');
  send('launching',{action:payload.action});bridge(payload.action,payload.payload||{},true).catch(()=>{});return {started:true};
 }
 if(action==='cancel'){await stopJob();return {requested:true};}
 if(action==='open_report'){
  const report=await bridge('report',{id:payload.id});const error=await shell.openPath(report.path);if(error)throw new Error(error);return {opened:true};
 }
 if(action==='copy_report'){
  const report=await bridge('report',{id:payload.id});clipboard.writeText(report.markdown);return {copied:true};
 }
 if(action==='open_source'){
  const url=new URL(payload.url);if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new Error('Unsupported source URL');await shell.openExternal(url.href);return {opened:true};
 }
 if(action==='open_env'){
  const env=path.join(manifest.repo,'.env');if(!fs.existsSync(env))fs.writeFileSync(env,'# Optional data/provider keys. Do not share this file.\n',{mode:0o600});const error=await shell.openPath(env);if(error)throw new Error(error);return {opened:true};
 }
 throw new Error('Unsupported desktop action');
});
app.whenReady().then(()=>{
 nativeTheme.themeSource='dark';
 window=new BrowserWindow({width:1480,height:980,minWidth:1024,minHeight:700,title:'Trading Agents',backgroundColor:'#09121e',titleBarStyle:'hiddenInset',trafficLightPosition:{x:18,y:20},
  webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
 window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 window.webContents.on('will-navigate',(event,url)=>{if(url!==window.webContents.getURL())event.preventDefault();});
 window.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
 app.setAboutPanelOptions({applicationName:'Trading Agents',applicationVersion:'0.7.1',copyright:'TradingAgents framework · Desktop nghiên cứu chuyên dụng',credits:'TradingAgents / Tauric Research. Desktop UI mã nguồn trong bản fork TradingAgents.'});
 const command=(action,payload={})=>()=>window.webContents.send('ta:menu',{action,payload});
 Menu.setApplicationMenu(Menu.buildFromTemplate([
  {label:'Trading Agents',submenu:[{label:'Giới thiệu Trading Agents',role:'about'},{type:'separator'},{label:'Cấu hình…',accelerator:'CmdOrCtrl+,',click:command('navigate',{page:'settings'})},{type:'separator'},{role:'hide'},{role:'hideOthers'},{role:'unhide'},{type:'separator'},{label:'Thoát Trading Agents',role:'quit'}]},
  {label:'Nghiên cứu',submenu:[{label:'Phân tích thị trường',accelerator:'CmdOrCtrl+1',click:command('navigate',{page:'analysis'})},{label:'Chạy phân tích',accelerator:'CmdOrCtrl+Return',click:command('analyze')},{type:'separator'},{label:'Backtest',accelerator:'CmdOrCtrl+2',click:command('navigate',{page:'backtest'})},{label:'Dừng tác vụ',accelerator:'CmdOrCtrl+.',click:command('cancel')}]},
  {label:'Báo cáo',submenu:[{label:'Thư viện báo cáo',accelerator:'CmdOrCtrl+3',click:command('navigate',{page:'reports'})},{label:'Mở báo cáo hiện tại',click:command('open_report')},{label:'Sao chép Markdown',click:command('copy_report')}]},
  {label:'Sửa',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
  {label:'Công cụ',submenu:[{label:'Memory & phản tư',accelerator:'CmdOrCtrl+4',click:command('navigate',{page:'memory'})},{label:'Mô hình & dữ liệu',click:command('navigate',{page:'settings'})},{label:'Đăng nhập Subscription',click:command('navigate',{page:'settings'})},{type:'separator'},{label:'Kiểm tra cài đặt',click:command('doctor')}]},
  {label:'Cửa sổ',submenu:[{role:'minimize'},{role:'zoom'},{role:'togglefullscreen'}]},
  {label:'Trợ giúp',submenu:[{label:'Hướng dẫn TradingAgents',click:()=>shell.openExternal('https://github.com/triettran-31251027936/TradingAgents/tree/codex/agentsroom-cli-backends')}]}
 ]));
 window.loadFile(path.join(__dirname,'index.html'));
 window.on('close',event=>{
  if(active&&!quitting){event.preventDefault();const response=dialog.showMessageBoxSync(window,{type:'question',buttons:['Tiếp tục chạy','Dừng và thoát'],defaultId:0,cancelId:0,title:'Trading Agents đang phân tích',message:'Tác vụ chưa hoàn tất. Dừng tác vụ và thoát?',detail:active.action==='subscription_login'?'Phiên đăng nhập này sẽ bị hủy.':'Các bước đã lưu checkpoint có thể được tiếp tục khi chạy lại cùng cấu hình.'});if(response===1){quitting=true;stopJob().then(()=>app.quit());}}
 });
});
app.on('window-all-closed',()=>app.quit());
