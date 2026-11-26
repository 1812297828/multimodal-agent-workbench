import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';

import { activeDispatch, dispatch, dispatchStatus, type DispatchJob, type DispatchToolRun } from '@/api/dispatch';
import { Markdown } from '@/components/markdown';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';

const toolNames: Record<string, string> = {
	project_files: '查找文件', project_read: '读取文件', project_edit: '修改文件', project_create: '新建文件',
	project_search: '搜索代码', project_git: 'Git', project_rules: '项目规则', project_skill: '技能',
	project_diagnostics: 'AOI 工具', project_mcp: 'MCP', project_plan: '更新计划', project_knowledge: '知识库',
	project_powershell: 'PowerShell', project_terminal: '终端', project_build: '编译', project_test: '测试',
};

export function useDispatchJob(id: string | null) {
	return useQuery({
		queryKey: ['dispatch-job', id],
		enabled: !!id,
		queryFn: () => dispatch<DispatchJob>('/jobs/' + id),
		refetchInterval: (query) => query.state.data && activeDispatch.has(query.state.data.status) ? 1100 : false,
	});
}

export function TerminalRequest({ job, entry }: { job: DispatchJob; entry: DispatchToolRun }) {
	const cache = useQueryClient();
	const [pending, setPending] = useState(false);
	async function respond(approved: boolean) {
		setPending(true);
		try {
			await dispatch('/jobs/' + job.id + '/terminal/' + entry.request_id, { approved });
			await cache.invalidateQueries({ queryKey: ['dispatch-job', job.id] });
		} catch (error) {
			toast.error((error as Error).message);
			setPending(false);
		}
	}
	return (
		<div className="max-h-52 overflow-auto rounded-xl border bg-card p-3 text-sm">
			<strong>确认运行 PowerShell 命令</strong>
			<p>{entry.description}</p>
			<p className="break-all text-xs text-muted-foreground">{entry.directory}</p>
			<pre className="my-2 whitespace-pre-wrap break-all rounded-md bg-muted p-2 text-xs">{entry.command}</pre>
			<div className="flex gap-2">
				<Button size="sm" disabled={pending} onClick={() => void respond(true)}>运行一次</Button>
				<Button size="sm" variant="outline" disabled={pending} onClick={() => void respond(false)}>跳过</Button>
			</div>
		</div>
	);
}

function FileChanges({ job }: { job: DispatchJob }) {
	const cache = useQueryClient();
	const [change, setChange] = useState<string | null>(null);
	const endpoint = '/jobs/' + job.id + '/changes';
	const files = useQuery({ queryKey: ['dispatch-changes', job.id], queryFn: () => dispatch<{
		files: { id: string; path: string; restored: boolean }[];
	}>(endpoint) });
	const detail = useQuery({ queryKey: ['dispatch-change', job.id, change], enabled: !!change,
		queryFn: () => dispatch<{ diff: string; restored: boolean }>(endpoint + '/' + change) });
	async function restore() {
		if (!window.confirm('恢复本轮修改前的文件版本？')) return;
		try {
			await dispatch(endpoint + '/' + change + '/restore', {});
			await Promise.all([files.refetch(), detail.refetch(), cache.invalidateQueries({ queryKey: ['dispatch-job', job.id] })]);
		} catch (error) {
			toast.error((error as Error).message);
		}
	}
	return (
		<details className="rounded-lg border p-3">
			<summary className="cursor-pointer">本轮文件改动</summary>
			{files.error && <p role="alert">{files.error.message}</p>}
			{files.data?.files.length === 0 && <p className="mt-2 text-muted-foreground">暂无改动记录</p>}
			<div className="mt-2 flex flex-wrap gap-1">
				{files.data?.files.map((file) => <Button key={file.id} size="sm" variant={change === file.id ? 'secondary' : 'ghost'}
					onClick={() => setChange(file.id)}>{file.path}{file.restored ? ' · 已撤销' : ''}</Button>)}
			</div>
			{detail.error && <p role="alert">{detail.error.message}</p>}
			{detail.data && <>
				<pre className="my-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-2 text-xs">{detail.data.diff || '无行级差异'}</pre>
				{!detail.data.restored && !activeDispatch.has(job.status) &&
					<Button variant="outline" size="sm" onClick={() => void restore()}>撤销本轮对此文件的改动</Button>}
			</>}
		</details>
	);
}

export function DispatchJobDetails({ id, onClose }: { id: string | null; onClose: () => void }) {
	const { data: job, error } = useDispatchJob(id);
	return (
		<Sheet open={!!id} onOpenChange={(open) => { if (!open) onClose(); }}>
			<SheetContent className="w-full overflow-y-auto sm:max-w-lg">
				<SheetHeader>
					<SheetTitle>执行明细</SheetTitle>
					<SheetDescription>{job?.title || '本轮模型与工具记录'}</SheetDescription>
				</SheetHeader>
				{error && <p className="px-4" role="alert">{error.message}</p>}
				{job && <div className="space-y-3 px-4 pb-5 text-sm">
					<p>{dispatchStatus[job.status] || job.status} · {job.calls.length} 次模型请求</p>
					{job.execution_root && <p className="break-all text-muted-foreground">工作目录 {job.execution_root}</p>}
					{job.routing?.reason && <p className="text-muted-foreground">{job.routing.reason}</p>}
					{job.error && <p role="alert" className="text-destructive">{job.error}</p>}
					{Object.entries(job.work_plans || {}).map(([label, steps]) => <div key={label} className="rounded-lg border p-3">
						<strong>{label}</strong>
						<ul className="ml-4 list-disc">{steps.map((step, i) => <li key={i}>{step.title} · {dispatchStatus[step.status] || (step.status === 'working' ? '进行中' : step.status)}</li>)}</ul>
					</div>)}
					{job.tasks.map((task) => <details key={task.id} className="rounded-lg border p-3">
						<summary className="cursor-pointer">{task.title} · {dispatchStatus[task.status] || task.status}</summary>
						{task.output && <Markdown className="mt-2" mode="static">{task.output}</Markdown>}
					</details>)}
					{job.execute_project && <FileChanges job={job} />}
					{job.tool_runs?.map((run, i) => <details key={i} className="rounded-lg border p-3">
						<summary className="cursor-pointer">{toolNames[run.tool] || run.tool} {run.arguments?.path || run.arguments?.name || ''} · {dispatchStatus[run.status] || run.status}</summary>
						<pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{[run.command, run.exit_code != null && '退出码 ' + run.exit_code, run.error, run.output].filter(Boolean).join('\n') || run.label}</pre>
					</details>)}
					<details className="rounded-lg border p-3">
						<summary className="cursor-pointer">模型调用与费用</summary>
						{Object.entries(job.usage.actual_costs || {}).map(([currency, value]) => <p key={currency}>{currency} {value} · 平台消费变化</p>)}
						{job.calls.map((call, i) => <p key={i} className="mt-2">{call.label} · {call.model} · {dispatchStatus[call.status] || call.status} · {call.input_tokens} / {call.output_tokens} Token {call.error}</p>)}
					</details>
					<details className="rounded-lg border p-3">
						<summary className="cursor-pointer">进度与来源</summary>
						{job.events.map((event, i) => <p key={i} className="mt-1">{event.message}</p>)}
						{job.knowledge_sources?.map((source, i) => <p key={i}>{source.name} · 片段 {source.chunk_index + 1}</p>)}
						{job.code_sources?.map((source, i) => <p key={i}>{source.repository}/{source.path}:{source.line}</p>)}
					</details>
					<Button asChild variant="outline" size="sm"><a href={'/dispatch/api/jobs/' + job.id + '/report'} download>导出报告</a></Button>
				</div>}
			</SheetContent>
		</Sheet>
	);
}
