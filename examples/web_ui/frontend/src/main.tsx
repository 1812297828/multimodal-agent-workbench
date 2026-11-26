// 此文件已在多模态智能体协作工作台中修改，上游为 AgentScope。
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import './index.css';
import './i18n';
import App from './App.tsx';
import { DesktopFrame } from '@/components/layout/DesktopFrame';
import { TooltipProvider } from '@/components/ui/tooltip.tsx';

createRoot(document.getElementById('root')!).render(
	<StrictMode>
		<TooltipProvider>
			<DesktopFrame><App /></DesktopFrame>
		</TooltipProvider>
	</StrictMode>,
);
