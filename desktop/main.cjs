'use strict';

const { app, BrowserWindow, Menu, ipcMain, nativeTheme, shell, dialog, screen } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const runtime = path.join(root, '.runtime');
const statePath = path.join(runtime, 'desktop-window.json');
const home = 'http://127.0.0.1:8000/';
let mainWindow;

app.setPath('userData', path.join(runtime, 'desktop-user-data'));
app.setAppUserModelId('local.agentscope.desktop');
nativeTheme.themeSource = 'dark';

function isWorkstation(url) {
  try {
    const parsed = new URL(url);
    return parsed.origin === 'http://127.0.0.1:8000'
      && /^\/(?:chat(?:\/[^/]*){0,3}|schedule|channel|credential|mcp(?:\/[^/]+)?|skill(?:\/[^/]+)?|knowledge(?:\/[^/]+)?|voice-profile|setup|collaboration|dispatch\/?)?\/?$/.test(parsed.pathname);
  } catch { return false; }
}

function openLink(url) {
  try {
    const parsed = new URL(url);
    if (['http:', 'https:'].includes(parsed.protocol)) {
      shell.openExternal(parsed.href).catch(() => {});
    }
  } catch {}
}

function readWindowState() {
  const defaults = { width: 1440, height: 960 };
  try {
    const stored = JSON.parse(fs.readFileSync(statePath, 'utf8'));
    const bounds = stored.bounds;
    if (!bounds || !['x', 'y', 'width', 'height'].every(key => Number.isInteger(bounds[key]))) return defaults;
    const display = screen.getDisplayMatching(bounds).workArea;
    const width = Math.min(display.width, Math.max(980, bounds.width));
    const height = Math.min(display.height, Math.max(680, bounds.height));
    return {
      width, height,
      x: Math.min(Math.max(bounds.x, display.x), display.x + display.width - width),
      y: Math.min(Math.max(bounds.y, display.y), display.y + display.height - height),
      maximized: Boolean(stored.maximized),
    };
  } catch { return defaults; }
}

function saveWindowState() {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isMinimized()) return;
  try {
    fs.writeFileSync(statePath, JSON.stringify({
      bounds: mainWindow.getNormalBounds(), maximized: mainWindow.isMaximized(),
    }, null, 2));
  } catch {}
}

function sendWindowState() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('desktop:state', { maximized: mainWindow.isMaximized() });
  }
}

function trusted(event) {
  return mainWindow && !mainWindow.isDestroyed()
    && event.sender === mainWindow.webContents
    && event.senderFrame === mainWindow.webContents.mainFrame
    && isWorkstation(event.senderFrame.url);
}

ipcMain.handle('desktop:state', event => {
  if (!trusted(event)) return null;
  return { maximized: mainWindow.isMaximized() };
});
ipcMain.handle('desktop:choose-directory', async event => {
  if (!trusted(event)) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 AgentScope 工作目录',
    properties: ['openDirectory'],
  });
  return result.canceled ? null : (result.filePaths[0] ?? null);
});
ipcMain.on('desktop:action', (event, action) => {
  if (!trusted(event)) return;
  switch (action) {
    case 'minimize': mainWindow.minimize(); break;
    case 'maximize': mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize(); break;
    case 'close': mainWindow.close(); break;
  }
});

function createWindow() {
  const state = readWindowState();
  const { maximized, ...bounds } = state;
  mainWindow = new BrowserWindow({
    ...bounds, minWidth: 980, minHeight: 680, show: false,
    frame: false, backgroundColor: '#0c0d10', title: 'AgentScope',
    icon: path.join(root, 'AgentScope.ico'), autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, sandbox: true, nodeIntegration: false,
      spellcheck: false, webSecurity: true,
    },
  });
  mainWindow.setMenu(null);
  mainWindow.webContents.session.setPermissionRequestHandler(async (contents, permission, callback, details) => {
    const source = details.requestingUrl || contents.getURL();
    if (isWorkstation(source) && permission === 'clipboard-sanitized-write') {
      callback(true);
      return;
    }
    if (!isWorkstation(source) || permission !== 'media' || details.mediaTypes?.includes('video')) {
      callback(false);
      return;
    }
    const result = await dialog.showMessageBox(mainWindow, {
      type: 'question', title: '麦克风访问',
      message: '允许 AgentScope 使用麦克风进行语音输入和实时语音对话？',
      buttons: ['允许', '取消'], defaultId: 0, cancelId: 1,
    });
    callback(result.response === 0);
  });
  mainWindow.webContents.session.setPermissionCheckHandler((contents, permission, origin) =>
    origin === 'http://127.0.0.1:8000' && contents && isWorkstation(contents.getURL())
      && ['media', 'clipboard-sanitized-write'].includes(permission));
  mainWindow.webContents.on('will-attach-webview', event => event.preventDefault());
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isWorkstation(url)) { event.preventDefault(); openLink(url); }
  });
  mainWindow.webContents.on('will-redirect', (event, url) => {
    if (!isWorkstation(url)) event.preventDefault();
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    openLink(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('did-finish-load', sendWindowState);
  mainWindow.once('ready-to-show', () => {
    if (maximized) mainWindow.maximize();
    mainWindow.show();
  });
  for (const event of ['maximize', 'unmaximize', 'restore']) mainWindow.on(event, sendWindowState);
  mainWindow.on('close', saveWindowState);
  mainWindow.on('closed', () => { mainWindow = null; });
  mainWindow.loadURL(home).catch(() => {
    dialog.showErrorBox('AgentScope 无法连接', '请重新运行启动入口，或检查 .runtime/service.log 中的本地服务状态。');
    app.quit();
  });
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    createWindow();
  });
  app.on('window-all-closed', () => app.quit());
}
