const {app,BrowserWindow,ipcMain,dialog,shell,nativeTheme,Menu,clipboard}=require('electron');
const fs=require('node:fs');
const crypto=require('node:crypto');
let approving=false;
const path=require('node:path');
const {spawn,execFile}=require('node:child_process');
const manifest=JSON.parse(fs.readFileSync(path.join(process.resourcesPath,'trading-desktop.json'),'utf8'));
app.setName('Trading Agents');
const desktopUserData=path.join(manifest.repo,'work','desktop-userdata');
fs.mkdirSync(desktopUserData,{recursive:true});app.setPath('userData',desktopUserData);
// Keep preferences, reports and account data stable; isolate Chromium caches per build.
const cacheRevision=crypto.createHash('sha256');
for(const file of ['main.cjs','index.html','app.js','app.css'])cacheRevision.update(fs.readFileSync(path.join(__dirname,file)));
const sessionCache=path.join(desktopUserData,'session-cache',cacheRevision.digest('hex').slice(0,16));
fs.mkdirSync(sessionCache,{recursive:true});app.setPath('sessionData',sessionCache);
app.disableHardwareAcceleration();app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
const startupLog=path.join(desktopUserData,'startup.log');
function startupFailure(error){fs.appendFileSync(startupLog,new Date().toISOString()+' '+String(error?.stack||error)+'\n');}
process.on('unhandledRejection',startupFailure);
fs.appendFileSync(startupLog,new Date().toISOString()+' boot\n');
let window,active=null,quitting=false,engineReady=false,metaPromise=null;
const bridgeChildren=new Set();
const bootstrapFile=path.join(desktopUserData,'startup-meta.json');
const quickActions=new Set(['meta','history','report','memory','save_config','doctor','subscription_status','subscription_redirect','subscription_logout','subscription_models','api_key_status','save_api_key','remove_api_key','paper_snapshot','paper_preview']);
const jobActions=new Set(['analyze','backtest','settle','subscription_login','test_provider','paper_advance']);
function send(type,payload){if(window&&!window.isDestroyed())window.webContents.send('ta:event',{type,payload});}
function bridge(action,payload={},long=false,approvalKey=null){
 if(action==='meta')startupFailure('meta spawn requested');
 return new Promise((resolve,reject)=>{
  const childEnv={...process.env,PYTHONUNBUFFERED:'1',PATH:[path.join(app.getPath('home'),'.local','bin'),'/opt/homebrew/bin','/usr/local/bin',process.env.PATH||''].join(':')};
  for(const key of ['TRADINGAGENTS_PAPER_APPROVAL_KEY','PYTHONHOME','PYTHONPATH','PYTHONEXECUTABLE','__PYVENV_LAUNCHER__','PYTHONSTARTUP'])delete childEnv[key];
  childEnv.PYTHONDONTWRITEBYTECODE='1';
  if(approvalKey)childEnv.TRADINGAGENTS_PAPER_APPROVAL_KEY=approvalKey;
  const child=spawn(manifest.python,[path.join(manifest.repo,'desktop','bridge.py')],{
   cwd:manifest.repo,env:childEnv,
   stdio:['pipe','pipe','pipe'],detached:true
  });
  bridgeChildren.add(child);
  const watchdog=long?null:setTimeout(()=>{bridgeChildren.delete(child);try{process.kill(-child.pid,'SIGKILL');}catch{}reject(new Error('Không nhận được phản hồi kịp thời. Làm mới dữ liệu để kiểm tra trạng thái trước khi thử lại.'));},action==='meta'?90000:45000);
  watchdog?.unref();
  let buffer='',result,error,stderr=false;
  if(long)active={child,action,payload,started:Date.now(),cancelled:false};
  child.stdout.setEncoding('utf8');child.stdout.on('data',chunk=>{
   buffer+=chunk;let end;
   while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!line.trim())continue;
    try{const event=JSON.parse(line);if(event.type==='result')result=event.payload;else if(event.type==='error')error=event.payload;else if(long)send(event.type,event.payload);}catch{stderr=true;}
   }
  });
  child.stderr.on('data',()=>{stderr=true;});
  child.on('error',err=>{if(watchdog)clearTimeout(watchdog);bridgeChildren.delete(child);if(long)active=null;reject(new Error('Không mở được Python của TradingAgents: '+err.code));});
  child.on('close',code=>{
   if(watchdog)clearTimeout(watchdog);
   bridgeChildren.delete(child);
   if(action==='meta')startupFailure('meta closed '+code);
   const cancelled=long&&active?.cancelled;const jobAction=long?active?.action:null;
   if(long)active=null;
   if(cancelled){send('cancelled',{message:jobAction==='subscription_login'?'Đã hủy phiên đăng nhập ChatGPT.':'Đã dừng. Có thể chạy lại cùng mã, ngày và cấu hình để tiếp tục từ checkpoint.'});resolve({cancelled:true});}
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
 if(action==='meta')startupFailure('meta IPC received');
 if(action==='bootstrap'){
  try{return JSON.parse(fs.readFileSync(bootstrapFile,'utf8'));}catch{return null;}
 }
 if(action==='meta'){
  if(!metaPromise)metaPromise=bridge('meta',{}).then(meta=>{engineReady=true;fs.writeFileSync(bootstrapFile,JSON.stringify(meta),{mode:0o600});return meta;}).finally(()=>{metaPromise=null;});
  return metaPromise;
 }
 if(['save_config','save_api_key','remove_api_key','paper_review','paper_reject','start'].includes(action)&&!engineReady)throw new Error('Bộ dữ liệu đang khởi động. Chờ đồng bộ cấu hình trước khi thực hiện.');
 if(quickActions.has(action))return bridge(action,payload||{});
 if(action==='paper_reject'){if(active||approving)throw new Error('Chờ tác vụ hiện tại kết thúc.');return bridge(action,payload||{});}
 if(action==='paper_review'){
  if(active||approving)throw new Error('Chờ tác vụ hiện tại kết thúc trước khi duyệt lệnh.');
  approving=true;
  try{
   const preview=await bridge('paper_preview',{id:payload?.id});
   if(preview.status!=='PROPOSED')throw new Error('Lệnh đã được xử lý.');
   const o=preview.order;const v=n=>new Intl.NumberFormat('vi-VN').format(n)+' VND';
   const answer=await dialog.showMessageBox(window,{type:'question',buttons:['Chưa đồng ý','Đồng ý gửi lệnh TIỀN ẢO'],defaultId:0,cancelId:0,title:'Duyệt lệnh tiền ảo — Trading Agents',message:`${o.side==='Buy'?'MUA':'BÁN'} ${o.quantity} cổ phiếu ${o.ticker}`,detail:`CHỈ TIỀN ẢO · Backtrader daily replay\nSàn tham chiếu: ${o.exchange}\nGiá giới hạn: ${v(o.limit_price)}/cổ phiếu\nGiá trị tối đa: ${v(preview.notional)}\nPhí/thuế ước tính: ${v(preview.estimated_fee)}\nTiền khả dụng: ${v(preview.available_cash)}\nNgày mô phỏng: ${o.asof} · Ngày phân tích: ${o.analysis_date}\n\n${o.rationale}\nThiếu dữ liệu: ${o.data_gaps.join('; ')||'Không có mục được PM khai báo'}\n\nLệnh chỉ chờ khớp từ nến giao dịch tiếp theo, tối đa 1% khối lượng ngày. Hết hiệu lực sau phiên đủ điều kiện đầu tiên. Mô phỏng T+2 cuối ngày. Không gửi lệnh đến sàn thật.`});
   if(answer.response!==1)return {cancelled:true};
   const timestamp=Math.floor(Date.now()/1000),secret=crypto.randomBytes(32).toString('hex');
   const signature=crypto.createHmac('sha256',secret).update(`${preview.id}|${preview.preview_hash}|${timestamp}`).digest('hex');
   return await bridge('paper_approve',{id:preview.id,preview_hash:preview.preview_hash,approval:{timestamp,signature}},false,secret);
  }finally{approving=false;}
 }
 if(action==='clear_checkpoints'){if(active)throw new Error('Dừng tác vụ đang chạy trước khi xóa checkpoint.');const answer=await dialog.showMessageBox(window,{type:'warning',buttons:['Giữ lại','Xóa checkpoint'],defaultId:0,cancelId:0,title:'Chạy mới',message:'Xóa toàn bộ checkpoint đã lưu?',detail:'Không xóa báo cáo hoặc memory. Các lượt đang dở sẽ không thể tiếp tục từ checkpoint cũ.'});if(answer.response!==1)return {cancelled:true};return bridge('clear_checkpoints');}
 if(action==='status')return active?{action:active.action,payload:active.payload,started:active.started}:null;
 if(action==='start'){
  if(active||approving)throw new Error('Một tác vụ đang chạy. Hãy đợi hoặc dừng tác vụ đó.');
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
 window.webContents.on('console-message',(_event,...details)=>startupFailure('console '+JSON.stringify(details)));
 window.webContents.on('preload-error',(_event,_file,error)=>startupFailure(error));
 window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 window.webContents.on('will-navigate',(event,url)=>{if(url!==window.webContents.getURL())event.preventDefault();});
 window.webContents.session.setPermissionRequestHandler((_contents,_permission,callback)=>callback(false));
 app.setAboutPanelOptions({applicationName:'Trading Agents',applicationVersion:'0.7.1',copyright:'TradingAgents framework · Desktop nghiên cứu chuyên dụng',credits:'TradingAgents / Tauric Research. Desktop UI mã nguồn trong bản fork TradingAgents.'});
 const command=(action,payload={})=>()=>window.webContents.send('ta:menu',{action,payload});
 Menu.setApplicationMenu(Menu.buildFromTemplate([
  {label:'Trading Agents',submenu:[{label:'Giới thiệu Trading Agents',role:'about'},{type:'separator'},{label:'Cấu hình…',accelerator:'CmdOrCtrl+,',click:command('navigate',{page:'settings'})},{type:'separator'},{role:'hide'},{role:'hideOthers'},{role:'unhide'},{type:'separator'},{label:'Thoát Trading Agents',role:'quit'}]},
  {label:'Nghiên cứu',submenu:[{label:'Phân tích thị trường',accelerator:'CmdOrCtrl+1',click:command('navigate',{page:'analysis'})},{label:'Chạy phân tích',accelerator:'CmdOrCtrl+Return',click:command('analyze')},{type:'separator'},{label:'Dữ liệu & quyết định',accelerator:'CmdOrCtrl+5',click:command('navigate',{page:'evidence'})},{label:'Danh mục tiền ảo',accelerator:'CmdOrCtrl+6',click:command('navigate',{page:'paper'})},{label:'Backtest',accelerator:'CmdOrCtrl+2',click:command('navigate',{page:'backtest'})},{label:'Dừng tác vụ',accelerator:'CmdOrCtrl+.',click:command('cancel')}]},
  {label:'Báo cáo',submenu:[{label:'Thư viện báo cáo',accelerator:'CmdOrCtrl+3',click:command('navigate',{page:'reports'})},{label:'Mở báo cáo hiện tại',click:command('open_report')},{label:'Sao chép Markdown',click:command('copy_report')}]},
  {label:'Sửa',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
  {label:'Công cụ',submenu:[{label:'Memory & phản tư',accelerator:'CmdOrCtrl+4',click:command('navigate',{page:'memory'})},{label:'Mô hình & dữ liệu',click:command('navigate',{page:'settings'})},{label:'Đăng nhập Subscription',click:command('navigate',{page:'settings'})},{type:'separator'},{label:'Kiểm tra cài đặt',click:command('doctor')}]},
  {label:'Cửa sổ',submenu:[{role:'minimize'},{role:'zoom'},{role:'togglefullscreen'}]},
  {label:'Trợ giúp',submenu:[{label:'Hướng dẫn TradingAgents',click:()=>shell.openExternal('https://github.com/triettran-31251027936/TradingAgents/tree/codex/agentsroom-cli-backends')}]}
 ]));
 window.webContents.on('did-finish-load',()=>fs.appendFileSync(startupLog,new Date().toISOString()+' ready\n'));
 window.webContents.on('did-fail-load',(_event,code,description)=>startupFailure('load '+code+' '+description));
 window.webContents.on('render-process-gone',(_event,details)=>startupFailure('renderer '+JSON.stringify(details)));
 window.loadFile(path.join(__dirname,'index.html')).catch(startupFailure);
 window.on('close',event=>{
  if(active&&!quitting){event.preventDefault();const response=dialog.showMessageBoxSync(window,{type:'question',buttons:['Tiếp tục chạy','Dừng và thoát'],defaultId:0,cancelId:0,title:'Trading Agents đang phân tích',message:'Tác vụ chưa hoàn tất. Dừng tác vụ và thoát?',detail:active.action==='subscription_login'?'Phiên đăng nhập này sẽ bị hủy.':'Các bước đã lưu checkpoint có thể được tiếp tục khi chạy lại cùng cấu hình.'});if(response===1){quitting=true;stopJob().then(()=>app.quit());}}
 });
});
app.on('before-quit',()=>{for(const child of bridgeChildren){if(child!==active?.child){try{process.kill(-child.pid,'SIGKILL');}catch{}}}});
app.on('window-all-closed',()=>app.quit());
