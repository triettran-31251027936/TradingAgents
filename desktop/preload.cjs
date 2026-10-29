const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('tradingAgents',{
 request:(action,payload={})=>ipcRenderer.invoke('ta:request',action,payload),
 onMenu:listener=>{const handler=(_event,data)=>listener(data);ipcRenderer.on('ta:menu',handler);return()=>ipcRenderer.removeListener('ta:menu',handler);},
 onEvent:listener=>{const handler=(_event,data)=>listener(data);ipcRenderer.on('ta:event',handler);return()=>ipcRenderer.removeListener('ta:event',handler);}
});
