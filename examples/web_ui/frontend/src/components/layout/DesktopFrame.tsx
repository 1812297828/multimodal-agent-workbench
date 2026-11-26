import { useEffect, useState, type ReactNode } from 'react';

import './desktop-frame.css';

type WindowState = { maximized: boolean };
type DesktopWindow = {
	minimize: () => void;
	toggleMaximize: () => void;
	close: () => void;
	getState: () => Promise<WindowState | null>;
	chooseDirectory?: () => Promise<string | null>;
	onStateChanged: (callback: (state: WindowState) => void) => () => void;
};

declare global {
	interface Window {
		agentScopeWindow?: DesktopWindow;
	}
}

export function DesktopFrame({ children }: { children: ReactNode }) {
	const nativeWindow = window.agentScopeWindow;
	const [maximized, setMaximized] = useState(false);

	useEffect(() => {
		if (!nativeWindow) return;
		const update = (state: WindowState | null) => {
			if (state) setMaximized(state.maximized);
		};
		document.documentElement.classList.add('dark');
		const unsubscribe = nativeWindow.onStateChanged(update);
		nativeWindow.getState().then(update).catch(() => {});
		return unsubscribe;
	}, [nativeWindow]);

	if (!nativeWindow) return children;

	return (
		<div className="desktop-frame">
			<div className="desktop-titlebar" aria-label="应用窗口标题栏">
				<div className="desktop-title"><img src="/agentscope.svg" alt="" /><span>AgentScope</span></div>
				<div className="desktop-controls" role="group" aria-label="窗口控制">
					<button id="window-minimize" title="最小化" aria-label="最小化" onClick={nativeWindow.minimize}>
						<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5h10" /></svg>
					</button>
					<button id="window-maximize" title={maximized ? '还原窗口' : '最大化'} aria-label={maximized ? '还原窗口' : '最大化'} aria-pressed={maximized} onClick={nativeWindow.toggleMaximize}>
						<svg viewBox="0 0 16 16" aria-hidden="true"><path d={maximized ? 'M5.5 5.5h7v7h-7zM3.5 10.5v-7h7' : 'M3.5 3.5h9v9h-9z'} /></svg>
					</button>
					<button id="window-close" title="关闭窗口" aria-label="关闭窗口" onClick={nativeWindow.close}>
						<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" /></svg>
					</button>
				</div>
			</div>
			<div className="desktop-app-content">{children}</div>
		</div>
	);
}
