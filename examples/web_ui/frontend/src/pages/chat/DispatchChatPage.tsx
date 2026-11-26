import type { ContentBlock, Msg } from '@agentscope-ai/agentscope/message';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { FolderOpen, MessageSquare, Plus, Settings2, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';

import { activeDispatch, dispatch, dispatchStatus, type ChatMode, type ConversationList,
	type DispatchConfig, type DispatchConversation, type DispatchMessage, type DispatchJob } from '@/api/dispatch';
import { ASMessageBubble } from '@/components/chat/ASMessageBubble';
import { TextInput } from '@/components/chat/TextInput';
import { DeleteDialog } from '@/components/dialog/DeleteDialog';
import { Button } from '@/components/ui/button';
import {
	MessageScroller, MessageScrollerButton, MessageScrollerContent, MessageScrollerItem,
	MessageScrollerProvider, MessageScrollerViewport,
} from '@/components/ui/message-scroller';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Sidebar, SidebarContent, SidebarGroup, SidebarGroupLabel, SidebarHeader, SidebarMenu,
	SidebarMenuAction, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger, useSidebar } from '@/components/ui/sidebar';
import { AudioProvider } from '@/context/AudioContext';
import { DispatchJobDetails, TerminalRequest, useDispatchJob } from './DispatchJobDetails';

const recentKey = 'agentscope_active_conversation';
const modeKey = 'agentscope_dispatch_mode';
const modelKey = 'agentscope_dispatch_model';
const localFolderOption = ':local-folder';
const imageTypes = ['image/png', 'image/jpeg', 'image/webp'];
const textTypes = ['text/plain', '.md', '.txt', '.csv', '.json', '.log', '.py', '.c', '.cpp', '.h', '.hpp', '.js', '.ts'];
const selectStyle = 'h-8 min-w-0 rounded-md border bg-background px-2 text-xs text-foreground';

function toNativeMessage(source: DispatchMessage): Msg {
	const id = source.job_id + ':' + source.role;
	const date = source.created_at;
	const active = !!source.status && activeDispatch.has(source.status);
	const attachments = [...(source.image_names || []), ...(source.has_context ? ['补充材料'] : [])];
	const content = [source.error && source.error !== source.content ? source.error : '',
		source.content, attachments.length && '附件：' + attachments.join('、')].filter(Boolean).join('\n\n');
	return {
		id, name: source.role === 'user' ? '你' : 'AgentScope', role: source.role, metadata: {},
		created_at: date, finished_at: active ? null : source.updated_at,
		content: content ? [{ id, type: 'text', text: content, created_at: date }] : [],
		usage: source.usage,
	};
}

async function attachment(file: File): Promise<ContentBlock> {
	const image = imageTypes.includes(file.type);
	if (file.size > (image ? 2_000_000 : 700_000)) throw new Error('附件过大，图片最多 2 MB，文本最多 700 KB。');
	const data = await new Promise<string>((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
		reader.onerror = () => reject(new Error('无法读取附件。'));
		reader.readAsDataURL(file);
	});
	return {
		id: crypto.randomUUID(), type: 'data', name: file.name, created_at: new Date().toISOString(),
		source: { type: 'base64', media_type: image ? file.type : 'text/plain', data },
	};
}

function DispatchChat() {
	const cache = useQueryClient();
	const { isMobile, setOpenMobile } = useSidebar();
	const [id, setId] = useState(() => localStorage.getItem(recentKey) || '');
	const [mode, setMode] = useState<ChatMode>(() => localStorage.getItem(modeKey) === 'collaboration' ? 'collaboration' : 'single');
	const [model, setModel] = useState(() => localStorage.getItem(modelKey) || '');
	const [repository, setRepository] = useState('');
	const [localDirectory, setLocalDirectory] = useState('');
	const [useKnowledge, setUseKnowledge] = useState(true);
	const [useCode, setUseCode] = useState(true);
	const [execute, setExecute] = useState(true);
	const [mcpIds, setMcpIds] = useState<string[]>([]);
	const [detail, setDetail] = useState<string | null>(null);
	const [conversationToDelete, setConversationToDelete] = useState<ConversationList['conversations'][number] | null>(null);
	const [sending, setSending] = useState(false);
	const [stopping, setStopping] = useState(false);
	const config = useQuery({ queryKey: ['dispatch-config'], queryFn: () => dispatch<DispatchConfig>('/config') });
	const projects = useQuery({ queryKey: ['dispatch-projects'], queryFn: () => dispatch<{
		default_repository?: string; repositories: { id: string; branch?: string; scope?: string }[];
	}>('/projects') });
	const library = useQuery({ queryKey: ['dispatch-library'], queryFn: () => dispatch<{ enabled: boolean }>('/library') });
	const plugins = useQuery({ queryKey: ['dispatch-mcps'], queryFn: () => dispatch<{ plugins: { id: string; name: string }[] }>('/project-mcps') });
	const history = useQuery({
		queryKey: ['dispatch-conversations'],
		queryFn: () => dispatch<ConversationList>('/conversations'),
		refetchInterval: (query) => query.state.data?.conversations.some((chat) => activeDispatch.has(chat.status)) ? 1500 : false,
	});
	const current = useQuery({
		queryKey: ['dispatch-conversation', id],
		queryFn: () => dispatch<DispatchConversation>('/conversations/' + id),
		enabled: !!id,
		refetchInterval: (query) => query.state.data && activeDispatch.has(query.state.data.status) ? 1100 : false,
	});
	const chat = current.data;
	const active = !!chat && activeDispatch.has(chat.status);
	const historyBusy = history.data?.conversations.some((entry) => entry.id !== id && activeDispatch.has(entry.status));
	const activeJobId = active || stopping ? chat?.job_ids.at(-1) || null : null;
	const { data: activeJob } = useDispatchJob(activeJobId);
	const terminal = activeJob?.tool_runs?.find((run) => run.status === 'waiting' && run.request_id);
	const models = config.data?.models.filter((item) => item.ready) || [];
	const selected = models.find((item) => item.id === model)
		|| models.find((item) => item.id === config.data?.policy.simple)
		|| models.find((item) => item.id === config.data?.policy.standard)
		|| models[0];
	const repos = projects.data?.repositories || [];
	const repo = repos.find((entry) => entry.id === repository) || repos.find((entry) => entry.id === projects.data?.default_repository) || repos[0];
	const customFolder = repository === localFolderOption;
	const canExecute = customFolder ? !!localDirectory.trim() : !!repo && repo.scope !== 'root_files';
	const busy = sending || active || stopping;

	function selectLocalDirectory(path: string) {
		setLocalDirectory(path);
		setMcpIds([]);
	}

	async function chooseDirectory() {
		try {
			const path = await window.agentScopeWindow?.chooseDirectory?.();
			if (path) selectLocalDirectory(path);
		} catch {
			toast.error('无法打开文件夹选择器，请输入完整路径。');
		}
	}

	function openChat(next: string) {
		setId(next);
		if (next) localStorage.setItem(recentKey, next);
		else localStorage.removeItem(recentKey);
		setExecute(false);
		setMcpIds([]);
		setDetail(null);
		if (isMobile) setOpenMobile(false);
	}

	async function deleteChat() {
		if (!conversationToDelete) return false;
		const target = conversationToDelete.id;
		try {
			await dispatch('/conversations/' + target, undefined, 'DELETE');
		} catch (error) {
			toast.error((error as Error).message);
			return false;
		}
		await cache.cancelQueries({ queryKey: ['dispatch-conversations'] });
		cache.setQueryData<ConversationList>(['dispatch-conversations'], (data) => data && ({
			conversations: data.conversations.filter((entry) => entry.id !== target),
		}));
		if (id === target) openChat('');
		cache.removeQueries({ queryKey: ['dispatch-conversation', target] });
		void cache.invalidateQueries({ queryKey: ['dispatch-conversations'] });
		toast.success('对话已删除');
	}

	async function send(blocks: ContentBlock[]) {
		let target = id;
		const plain: string[] = [];
		const docs: string[] = [];
		const images = [];
		for (const block of blocks) {
			if (block.type === 'text') plain.push(block.text);
			if (block.type !== 'data' || block.source.type !== 'base64') continue;
			const { media_type, data } = block.source;
			if (imageTypes.includes(media_type)) images.push({ name: block.name || '图片', media_type, data });
			else docs.push('文件：' + (block.name || '附件') + '\n' +
				new TextDecoder().decode(Uint8Array.from(atob(data), (char) => char.charCodeAt(0))));
		}
		setSending(true);
		try {
			if (images.length > 3) throw new Error('每条消息最多附加 3 张图片。');
			if (!target) {
				const created = await dispatch<DispatchConversation>('/conversations', {});
				target = created.id;
			}
			const job = await dispatch<DispatchJob>('/conversations/' + target + '/messages', {
				prompt: plain.join('\n').trim() || '请分析附件。',
				context: docs.join('\n\n'), images, chat_mode: mode, single_model: mode === 'single' ? selected?.id : null,
				use_code: !customFolder && !!repo && useCode, use_knowledge: !!library.data?.enabled && useKnowledge,
				execute_project: canExecute && execute, repository: customFolder ? null : repo?.id || null,
				local_directory: customFolder && canExecute && execute ? localDirectory.trim() : null,
				mcp_ids: canExecute && execute ? mcpIds : [],
			});
			cache.setQueryData(['dispatch-job', job.id], job);
			openChat(target);
			await Promise.all([
				cache.invalidateQueries({ queryKey: ['dispatch-conversation', target] }),
				cache.invalidateQueries({ queryKey: ['dispatch-conversations'] }),
			]);
		} catch (error) {
			toast.error((error as Error).message);
			throw error;
		} finally {
			setSending(false);
		}
	}

	async function stop() {
		if (!activeJobId) return;
		setStopping(true);
		try {
			await dispatch('/jobs/' + activeJobId + '/cancel', {});
			await Promise.all([current.refetch(), history.refetch()]);
		} catch (error) {
			toast.error((error as Error).message);
		} finally {
			setStopping(false);
		}
	}

	const controls = <div className="flex flex-wrap items-center gap-1.5 px-2 py-1.5">
		<select aria-label="对话模式" className={selectStyle} value={mode} disabled={busy}
			onChange={(event) => {
				const next = event.target.value as ChatMode;
				setMode(next); localStorage.setItem(modeKey, next);
			}}>
			<option value="single">单模型</option>
			<option value="collaboration">多模型</option>
		</select>
		{mode === 'single' && <select aria-label="实际模型" className={selectStyle + ' max-w-48'} value={selected?.id || ''}
			disabled={busy || !models.length}
			onChange={(event) => { setModel(event.target.value); localStorage.setItem(modelKey, event.target.value); }}>
			{!models.length && <option value="">请选择已配置模型</option>}
			{models.map((item) => <option key={item.id} value={item.id}>{item.model}</option>)}
		</select>}
		<Popover>
			<PopoverTrigger asChild><Button size="sm" variant="ghost" disabled={busy}
				title={customFolder ? localDirectory : repo?.id}>
				<FolderOpen /> 本轮工具 · {execute && canExecute ? '可执行' : '只读'}
			</Button></PopoverTrigger>
			<PopoverContent align="start" className="w-72 space-y-3 text-sm">
				<label className="flex flex-col gap-1">工作目录<select className={selectStyle} value={customFolder ? localFolderOption : repo?.id || ''}
					onChange={(event) => { setRepository(event.target.value); setMcpIds([]); }}>
					{!repos.length && <option value="">请选择工作目录</option>}
					{repos.map((entry) => <option key={entry.id} value={entry.id}>{entry.id}</option>)}
					<option value={localFolderOption}>自定义本地文件夹</option>
				</select></label>
				{customFolder ? <div className="flex items-end gap-2">
					<label className="flex min-w-0 flex-1 flex-col gap-1">文件夹路径
						<input type="text" className={selectStyle + ' w-full'} value={localDirectory}
							placeholder={'D:\\Work\\MyApp'} onChange={(event) => selectLocalDirectory(event.target.value)} />
					</label>
					{window.agentScopeWindow?.chooseDirectory &&
						<Button type="button" size="sm" variant="outline" onClick={() => void chooseDirectory()}>浏览</Button>}
				</div> : <label className="flex gap-2"><input type="checkbox" checked={useCode && !!repo} disabled={!repo} onChange={(event) => setUseCode(event.target.checked)} /> 检索工程代码</label>}
				<label className="flex gap-2"><input type="checkbox" checked={useKnowledge && !!library.data?.enabled} disabled={!library.data?.enabled} onChange={(event) => setUseKnowledge(event.target.checked)} /> 检索本机资料</label>
				<label className="flex gap-2"><input type="checkbox" checked={execute && canExecute} disabled={!canExecute} onChange={(event) => setExecute(event.target.checked)} /> 允许本轮在所选项目执行</label>
				{execute && plugins.data?.plugins.map((plugin) => <label key={plugin.id} className="flex gap-2">
					<input type="checkbox" checked={mcpIds.includes(plugin.id)} onChange={(event) =>
						setMcpIds((before) => event.target.checked ? [...before, plugin.id] : before.filter((value) => value !== plugin.id))} /> {plugin.name}
				</label>)}
			</PopoverContent>
		</Popover>
	</div>;

	return (
		<div className="flex h-full min-h-0 w-full gap-2 p-2">
			<Sidebar collapsible={isMobile ? 'offcanvas' : 'none'} className="rounded-[22px]">
				<SidebarHeader className="p-3">
					<Button disabled={sending} variant="secondary" className="justify-start" onClick={() => openChat('')}><Plus /> 新建对话</Button>
				</SidebarHeader>
				<SidebarContent>
					<SidebarGroup>
						<SidebarGroupLabel>对话记录</SidebarGroupLabel>
						<SidebarMenu>
							{history.data?.conversations.map((entry) => <SidebarMenuItem key={entry.id}>
								<SidebarMenuButton className="pr-8" isActive={entry.id === id} disabled={sending} tooltip={entry.title} onClick={() => openChat(entry.id)}>
									<MessageSquare /> <span>{entry.title}</span>
									{activeDispatch.has(entry.status) && <small className="ml-auto">执行中</small>}
								</SidebarMenuButton>
								<SidebarMenuAction type="button" aria-label={'删除对话 ' + entry.title}
									title={activeDispatch.has(entry.status) ? '任务执行期间无法删除' : '删除对话'}
									disabled={sending || activeDispatch.has(entry.status)}
									className="hover:text-destructive disabled:opacity-40"
									onClick={() => setConversationToDelete(entry)}>
									<Trash2 />
								</SidebarMenuAction>
							</SidebarMenuItem>)}
						</SidebarMenu>
					</SidebarGroup>
				</SidebarContent>
			</Sidebar>
			<main className="flex min-w-0 flex-1 flex-col overflow-hidden">
				<header className="flex items-center gap-2 px-2 py-1">
					{isMobile && <SidebarTrigger />}
					<h1 className="min-w-0 flex-1 truncate text-sm font-medium">{chat?.title || '对话'}</h1>
					<Button asChild size="sm" variant="ghost"><Link to="/chat/settings"><Settings2 /> <span className="max-sm:hidden">模型与分工</span></Link></Button>
				</header>
				<div className="flex min-h-0 flex-1 flex-col items-center overflow-hidden [--chat-content-w:48rem]">
					{chat?.messages.length ? (
						<MessageScrollerProvider key={id} autoScroll defaultScrollPosition="end">
							<MessageScroller>
								<MessageScrollerViewport>
									<MessageScrollerContent>
										{chat.messages.map((message) => (
											<MessageScrollerItem key={message.job_id + message.role} messageId={message.job_id + message.role}>
												<ASMessageBubble message={toNativeMessage(message)} onUserConfirm={() => {}} />
												{message.role === 'assistant' && <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
													<span>{message.mode === 'single' ? '单模型' : message.routing?.route === 'direct' ? '自动分流' : '多模型'}</span>
													{message.routing?.model_name && <span>· {message.routing.model_name}</span>}
													{message.status && activeDispatch.has(message.status) && <span>· {dispatchStatus[message.status]}</span>}
													<Button size="xs" variant="ghost" onClick={() => setDetail(message.job_id)}>执行明细</Button>
												</div>}
											</MessageScrollerItem>
										))}
									</MessageScrollerContent>
								</MessageScrollerViewport>
								<MessageScrollerButton className="rounded-full" />
							</MessageScroller>
						</MessageScrollerProvider>
					) : (
						<div className="flex min-h-0 flex-1 items-end justify-center px-3 pb-6">
							<h2 className="text-center text-3xl font-light tracking-tight">{id && current.isPending ? '正在读取对话…' : '今天想做什么？'}</h2>
						</div>
					)}
					<div className={'w-full max-w-[48rem] space-y-2 px-2 ' + (chat?.messages.length ? 'pb-3' : 'mb-[20vh]')}>
						{(current.error || config.error) && <p className="text-sm text-destructive" role="alert">{(current.error || config.error)?.message}</p>}
						{historyBusy && <p className="text-xs text-muted-foreground">另一个对话正在执行，可从侧栏打开查看。</p>}
						{activeJob && terminal && <TerminalRequest key={terminal.request_id} job={activeJob} entry={terminal} />}
						<TextInput
							key={id || 'new'} className="w-full rounded-[32px] bg-muted p-1"
							phase={stopping ? 'interrupting' : active ? 'streaming' : 'idle'} onInterrupt={() => void stop()}
							disabled={busy || !!historyBusy || !config.data || (mode === 'single' && !selected) || (!!id && current.isPending)}
							voiceDisabled
							onSend={send} headerSlot={controls}
							allowedInputTypes={[...textTypes, ...(mode !== 'single' || selected?.supports_images ? imageTypes : [])]}
							fileProcessor={attachment}
						/>
					</div>
				</div>
			</main>
			<DispatchJobDetails id={detail} onClose={() => setDetail(null)} />
			<DeleteDialog
				open={!!conversationToDelete}
				onOpenChange={(open) => { if (!open) setConversationToDelete(null); }}
				title="删除对话"
				description={`「${conversationToDelete?.title || ''}」的消息和执行记录将被删除，无法恢复。`}
				confirmLabel="确认删除"
				onConfirm={deleteChat}
			/>
		</div>
	);
}

export function DispatchChatPage() {
	return <AudioProvider><SidebarProvider defaultOpen><DispatchChat /></SidebarProvider></AudioProvider>;
}

export function DispatchSettingsPage() {
	return <div className="flex h-full flex-col">
		<header className="flex items-center gap-3 border-b px-3 py-2">
			<Button asChild variant="ghost" size="sm"><Link to="/chat">返回对话</Link></Button>
			<span className="text-sm">模型与分工</span>
		</header>
		<iframe className="min-h-0 w-full flex-1 border-0" title="模型与分工设置" src="/dispatch?embedded=1&settings=1#models" />
	</div>;
}
