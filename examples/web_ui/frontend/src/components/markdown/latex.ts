import { fromMarkdown } from 'mdast-util-from-markdown';

const latexDelimiters = /(?<!\\)((?:\\\\)*)\\(?:\[([\s\S]*?)(?<!\\)\\\]|\(([^\r\n]*?)(?<!\\)\\\))/g;

export function normalizeLatex(source: string) {
	if (!source.includes('\\[') && !source.includes('\\(')) return source;

	function convert(text: string) {
		return text.replace(latexDelimiters, (original: string, prefix: string, display?: string, inline?: string) => {
			if (display?.trim()) {
				// 保留已有的换行与引用缩进，让 Markdown 继续识别块级公式。
				if (/^[ \t]*\r?\n/.test(display) && /\n(?:[ \t]*>)*[ \t]*$/.test(display)) {
					return prefix + '$$' + display + '$$';
				}
				return prefix + '\n\n$$\n' + display.trim() + '\n$$\n\n';
			}
			return inline?.trim() ? prefix + '$' + inline + '$' : original;
		});
	}

	// 沿用 Markdown 的位置记录，保留代码块、行内代码和链接目标中的字面内容。
	type Node = ReturnType<typeof fromMarkdown>['children'][number];
	const protectedTypes = new Set(['code', 'inlineCode', 'html', 'link', 'image', 'definition']);
	const parts: string[] = [];
	let offset = 0;
	function visit(node: Node) {
		const start = node.position?.start.offset;
		const end = node.position?.end.offset;
		if (protectedTypes.has(node.type) && start !== undefined && end !== undefined) {
			parts.push(convert(source.slice(offset, start)), source.slice(start, end));
			offset = end;
		} else if ('children' in node) {
			node.children.forEach(visit);
		}
	}
	fromMarkdown(source).children.forEach(visit);
	parts.push(convert(source.slice(offset)));
	return parts.join('');
}
