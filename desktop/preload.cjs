'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// 仅向本机主界面开放指定的窗口操作与系统目录选择器。
if (window === window.top && location.origin === 'http://127.0.0.1:8000'
    && /^\/(?:chat(?:\/[^/]*){0,3}|schedule|channel|credential|mcp(?:\/[^/]+)?|skill(?:\/[^/]+)?|knowledge(?:\/[^/]+)?|voice-profile|setup|collaboration|dispatch\/?)?\/?$/.test(location.pathname)) {
  contextBridge.exposeInMainWorld('agentScopeWindow', {
    minimize: () => ipcRenderer.send('desktop:action', 'minimize'),
    toggleMaximize: () => ipcRenderer.send('desktop:action', 'maximize'),
    close: () => ipcRenderer.send('desktop:action', 'close'),
    getState: () => ipcRenderer.invoke('desktop:state'),
    chooseDirectory: () => ipcRenderer.invoke('desktop:choose-directory'),
    onStateChanged: callback => {
      const listener = (_, state) => callback(state);
      ipcRenderer.on('desktop:state', listener);
      return () => ipcRenderer.removeListener('desktop:state', listener);
    },
  });
}
