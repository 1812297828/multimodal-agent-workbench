'use strict';

(() => {
  if (new URLSearchParams(location.search).get('embedded') === '1') {
    document.documentElement.classList.add('embedded-workspace');
  }
  const nativeWindow = window.agentScopeWindow;
  if (!nativeWindow) return;
  document.documentElement.classList.add('desktop-app');
  const maximize = document.getElementById('window-maximize');
  const icon = document.getElementById('window-maximize-icon');
  function update(state) {
    if (!state) return;
    maximize.title = state.maximized ? '还原窗口' : '最大化';
    maximize.setAttribute('aria-label', maximize.title);
    maximize.setAttribute('aria-pressed', String(state.maximized));
    document.documentElement.classList.toggle('window-maximized', state.maximized);
    icon.setAttribute('d', state.maximized ? 'M5.5 5.5h7v7h-7zM3.5 10.5v-7h7' : 'M3.5 3.5h9v9h-9z');
  }
  document.getElementById('window-minimize').onclick = () => nativeWindow.minimize();
  maximize.onclick = () => nativeWindow.toggleMaximize();
  document.getElementById('window-close').onclick = () => nativeWindow.close();
  nativeWindow.onStateChanged(update);
  nativeWindow.getState().then(update).catch(() => {});
})();
