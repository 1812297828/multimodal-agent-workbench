export type ChatMode = 'single' | 'collaboration';

export interface DispatchUsage {
	input_tokens: number;
	output_tokens: number;
	actual_costs?: Record<string, number>;
}

export interface DispatchMessage {
	role: 'user' | 'assistant';
	content: string;
	job_id: string;
	created_at: string;
	updated_at: string;
	mode: ChatMode | 'auto';
	status?: string;
	error?: string;
	routing?: { route: string; model_name?: string };
	usage?: DispatchUsage;
	image_names?: string[];
	has_context?: boolean;
}

export interface DispatchConversation {
	id: string;
	title: string;
	job_ids: string[];
	status: string;
	messages: DispatchMessage[];
	usage: DispatchUsage;
}

export interface ConversationList {
	conversations: { id: string; title: string; status: string; turn_count: number }[];
}

export interface DispatchConfig {
	models: { id: string; name: string; model: string; ready: boolean; supports_images: boolean }[];
	policy: { simple: string; standard: string; money_budget?: number; currency: string };
}

export interface DispatchJob {
	id: string;
	title: string;
	status: string;
	error?: string;
	usage: DispatchUsage;
	routing?: { reason: string };
	calls: { label: string; model: string; status: string; error?: string; input_tokens: number; output_tokens: number }[];
	tasks: { id: string; title: string; status: string; model_id: string; output: string }[];
	events: { time: string; message: string }[];
	tool_runs?: DispatchToolRun[];
	execute_project?: boolean;
	execution_root?: string;
	work_plans?: Record<string, { title: string; status: string }[]>;
	knowledge_sources?: { name: string; chunk_index: number }[];
	code_sources?: { path: string; line: number; repository: string }[];
}

export interface DispatchToolRun {
	tool: string;
	label: string;
	arguments: Record<string, string>;
	status: string;
	request_id?: string;
	command?: string;
	directory?: string;
	description?: string;
	output?: string;
	error?: string;
	exit_code?: number;
}

export const activeDispatch = new Set(['reading_code', 'retrieving', 'reading_images', 'assessing',
	'planning', 'running', 'reviewing', 'repairing', 'summarizing', 'cancelling']);

export const dispatchStatus: Record<string, string> = {
	reading_code: '检索代码', retrieving: '检索资料', reading_images: '读取图片', assessing: '判断难度',
	planning: '拆分任务', running: '执行中', reviewing: '复核中', repairing: '修正中',
	summarizing: '整理成果', cancelling: '正在停止', waiting: '等待确认', pending: '待处理',
	done: '已完成', failed: '未完成', cancelled: '已停止', interrupted: '已中断',
	blocked: '依赖未完成',
	budget_stopped: '达到费用限额', needs_review: '需要人工复核',
};

export async function dispatch<T>(path: string, body?: object, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
	const response = await fetch('/dispatch/api' + path, {
		headers: { 'Content-Type': 'application/json', 'X-Dispatch-Client': 'local' },
		method,
		body: body === undefined ? undefined : JSON.stringify(body),
		cache: 'no-store',
	});
	const payload = await response.json().catch(() => null);
	if (!response.ok || payload === null) {
		throw new Error(typeof payload?.detail === 'string' ? payload.detail : '请求未完成，请检查本地后台。');
	}
	return payload as T;
}
