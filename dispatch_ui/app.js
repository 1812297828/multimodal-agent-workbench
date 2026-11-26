'use strict';

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
const number = (n) => Number(n || 0).toLocaleString('zh-CN');
const difficulty = { simple: '简单整理', standard: '普通分析', advanced: '常规代码', hard: '复杂实现', expert: '算法攻坚' };
const phases = { reading_code: '读取工程代码', retrieving: '检索本机资料', reading_images: '提取图片信息', assessing: '独立判断难度', planning: '拆分任务', running: '执行中', reviewing: '复核中', repairing: '修正中', summarizing: '整合成果', cancelling: '正在停止', done: '本轮完成', failed: '执行未完成', cancelled: '已停止', interrupted: '重启中断', budget_stopped: '预算已用尽', needs_review: '需要人工复核' };
const activePhases = new Set(['reading_code', 'retrieving', 'reading_images', 'assessing', 'planning', 'running', 'reviewing', 'repairing', 'summarizing', 'cancelling']);
const roleRows = [
  ['router', '判断难度', '项目经理先做简短分类，将本轮消息分给合适的执行流程。'],
  ['planner', '拆解任务', '项目经理仅在需要协作时拆分子任务，确定依赖和验收条件。'],
  ['simple', '简单任务', '日常问答、文字整理、资料提取。优先低价模型，自动排除判断模型。'],
  ['standard', '普通任务', '常规分析和明确的小修改，失败只尝试其他低价执行模型。'],
  ['advanced', '常规开发', '代码设计、普通重构与排错，可使用 GPT 6.1。'],
  ['hard', '复杂开发', '复杂逻辑、多模块整合和高影响问题，可使用 GPT 6.2 Sol。'],
  ['expert', '算法攻坚', '困难推导、算法选择与关键验证，可使用 GPT 6.2 Astra。'],
  ['vision', '图片分析模型', '有图片时才启用视觉工程师；优先使用低价档中可处理图片的模型。'],
  ['reviewer', '常规复核模型', '测试工程师检查成果的逻辑、边界和风险；模型检查不代表已运行测试。'],
  ['summarizer', '成果整理模型', '项目助理合并多个成果，保留关键结论、代码和复核意见。'],
];
const descriptions = {
  economy: '自定义执行模型，用于处理简单任务。',
  deepseek: '独立判断任务难度，也可用于普通分析和成果整合。',
  'deepseek-reasoner': '负责需求拆解和常规复核。',
  'gpt61-sol': '负责常规代码任务，与其他 GPT 模型共用平台密钥池。',
  'gpt61-terra': '用于图像、界面和图表理解。',
  'gpt62-sol': '负责复杂编码和多模块方案。',
  'gpt62-astra': '负责最高难度的算法与数学推导。',
};
let config;
let currentJob = null;
let currentConversation = null;
let conversationReady = false;
let conversationGeneration = 0;
let sending = false;
const conversationDrafts = new Map();
let pollTimer;
const platformCatalogues = new Map();
const catalogueRequests = new Map();
const catalogueErrors = new Map();
const catalogueStorageKey = 'agentscope_platform_catalogues_v1';
let saving = false;
let images = [];
let projectCatalog = { enabled:false, repositories:[] };
let changeRequest = 0;
let terminalDecision = null;
let lastToolResult = '';
let lastToolRepository = '';

async function api(path, options = {}) {
  const response = await fetch('/dispatch/api' + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Dispatch-Client': 'local', ...options.headers },
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请检查配置。');
    error.status = response.status;
    throw error;
  }
  return data;
}

const settingsOnly = new URLSearchParams(location.search).has('settings');
if (settingsOnly) document.documentElement.classList.add('dispatch-settings');

function notice(message, type = '') {
  $('notice').className = 'notice' + (type ? ' ' + type : '');
  $('notice').textContent = message;
}

function view(name) {
  document.querySelectorAll('[data-key-revealed]').forEach((node) => {
    node.textContent = '';
    node.classList.add('hidden');
  });
  document.querySelectorAll('[data-key-action="reveal"]').forEach((button) => { button.textContent = '查看'; });
  document.querySelectorAll('.view').forEach((node) => node.classList.toggle('hidden', node.id !== 'view-' + name));
  document.querySelectorAll('nav button').forEach((node) => node.classList.toggle('active', node.dataset.view === name));
  $('page-title').textContent = ({ tasks: '直接提问，自动选择合适的处理方式', projects: '连接代码，核对实际工程', models: '平台与密钥', policy: '兼顾能力、速度与成本' })[name];
  if(name === 'projects') void loadToolStatus();
  if(name === 'models' && config) void refreshKeyUsage().catch((error) => notice(error.message, 'error'));
  $('notice').classList.add('hidden');
  history.replaceState(null, '', '#'+name);
  if (name === 'policy' && config) {
    syncRoleFields();
    void ensureRoleCatalogues();
  }
}

function options(selected, exclude = '') {
  return config.models.filter((model) => model.id !== exclude).map((model) => `<option value="${esc(model.id)}" ${model.id === selected ? 'selected' : ''}>${esc(model.name)}</option>`).join('');
}

function field(label, key, value, type = 'text', placeholder = '', wide = false, extra = '') {
  return `<div class="field${wide ? ' wide' : ''}"><label>${esc(label)}</label><input aria-label="${esc(label)}" data-field="${esc(key)}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${extra}></div>`;
}

function renderModels() {
  renderPlatforms();
  $('model-count').textContent = `${config.models.filter((model) => model.ready).length}/${config.models.length}`;
  $('model-cards').innerHTML = config.models.map((model) => `
    <article class="model-card" data-model="${model.id}">
      <div class="card-top"><h3>${esc(model.name)}</h3><span class="tag ${model.ready ? 'ready' : ''}">${model.ready ? '配置已就绪' : model.enabled ? '待填写' : '未启用'}</span></div>
      <p class="description">${esc(descriptions[model.id] || '自定义文本模型，可在分工与预算中选择它负责的任务。')}</p>
      <label class="check"><input type="checkbox" data-field="enabled" ${model.enabled ? 'checked' : ''}>参与任务分配</label>
      <div class="form-grid" style="margin-top:15px">
        ${field('显示名称', 'name', model.name, 'text', '', true)}
        ${field('输入价格 / 百万 Token', 'input_price', model.input_price ?? '', 'number', '不确定可留空', false, 'min="0" step="any"')}
        ${field('输出价格 / 百万 Token', 'output_price', model.output_price ?? '', 'number', '统一使用所选币种', false, 'min="0" step="any"')}
        ${field('该接口最大并发', 'concurrency', model.concurrency, 'number', '', false, 'min="1" max="8"')}
      </div>
    </article>`).join('');
}
function renderPlatforms() {
  const platforms = (config.platforms || []).filter((platform) => platform.id !== 'economy-platform');
  $('platform-cards').innerHTML = platforms.map((platform) => `
    <article class="model-card platform-card" data-platform="${esc(platform.id)}">
      <div class="card-top"><h3>${esc(platform.name)}</h3><span class="tag ${platform.has_keys ? 'ready' : ''}">${platform.has_keys ? `${number(platform.key_count)} 个密钥` : '待配置'}</span></div>
      <label class="check"><input type="checkbox" data-platform-field="enabled" ${platform.enabled ? 'checked' : ''}>参与任务分配</label>
      <div class="form-grid" style="margin-top:15px">
        <div class="field wide"><label>平台名称</label><input aria-label="平台名称" data-platform-field="name" type="text" value="${esc(platform.name)}"></div>
        <div class="field wide"><label>服务地址 Base URL</label><input aria-label="服务地址 Base URL" data-platform-field="base_url" type="url" value="${esc(platform.base_url)}" placeholder="例如 https://你的中转域名/v1"></div>
        <div class="field"><label>接口类型</label><select data-platform-field="protocol"><option value="chat" ${platform.protocol === 'chat' ? 'selected' : ''}>OpenAI 兼容 Chat</option><option value="responses" ${platform.protocol === 'responses' ? 'selected' : ''}>OpenAI 兼容 Responses</option><option value="deepseek" ${platform.protocol === 'deepseek' ? 'selected' : ''}>DeepSeek 官方</option></select></div>
      </div>
      <div class="key-pool">
        <div class="key-pool-heading"><h4>API key 池</h4><button data-key-action="refresh">刷新用量</button><button data-key-action="add">＋ 新增 key</button></div>
        <input type="search" class="key-search" aria-label="搜索密钥备注或尾号" placeholder="搜索备注或尾号">
        <small class="muted">逐条编辑后保存配置；密钥栏留空保留原值。按从上到下顺序调用，停用的 key 会跳过。</small>
        <div class="key-list">${(platform.key_entries || []).map(renderKeyEntry).join('')}</div>
        <p class="key-empty ${(platform.key_entries || []).length ? 'hidden' : ''}">还没有密钥，点击“新增 key”。</p>
        <p class="small-note">单价单位：${esc(config.policy.currency)} / 百万 Token。模型专属价优先，其次为本 key 默认价，留空再使用分工页模型价格。累计仅包含本软件记录的调用，属于估算费用；实际扣款以平台账单为准。</p>
      </div>
      <div class="card-actions"><button data-platform-action="catalog">读取此平台的模型列表</button><span class="model-result">当前已配置 ${number(platform.model_count || 0)} 个内部模型角色</span></div>
    </article>`).join('');
  $('platform-cards').querySelectorAll('button[data-platform-action]').forEach((button) => button.addEventListener('click', () => platformAction(button)));
  $('platform-cards').querySelectorAll('.platform-card').forEach((card) => {
    card.addEventListener('click', (event) => {
      const button = event.target.closest('[data-key-action]');
      if (button) void keyAction(card, button);
    });
    card.querySelector('.key-search').oninput = () => filterKeyRows(card);
  });
}

function keyPriceInput(field, value, placeholder) {
  return `<input type="number" min="0" step="any" data-key-field="${field}" value="${esc(value ?? '')}" placeholder="${placeholder}" aria-label="${placeholder}">`;
}

function renderKeyModelPrice(model = '', prices = {}) {
  return `<div class="key-model-price"><input data-price-model value="${esc(model)}" placeholder="实际模型 ID" aria-label="专属价格的模型 ID">${keyPriceInput('input_price', prices.input_price, '输入单价')}${keyPriceInput('output_price', prices.output_price, '输出单价')}<button data-key-action="remove-price" title="删除模型价格">删除</button></div>`;
}

function keyUsageText(usage = {}) {
  const costs = Object.entries(usage.costs || {}).map(([currency, value]) => money(value, currency)).join(' ＋ ') || '尚无计价记录';
  return `软件历史估算：${costs} · ${number(usage.calls)} 次请求 · 输入 ${number(usage.input_tokens)} / 输出 ${number(usage.output_tokens)} Token${usage.unpriced_calls ? ` · ${number(usage.unpriced_calls)} 次未计价` : ''}${usage.uncertain_calls ? ` · ${number(usage.uncertain_calls)} 次计费待核实` : ''}`;
}

function billingText(billing = {}) {
  if (billing.status === 'error') return '实际账单：' + billing.error;
  if (billing.status !== 'ok') return '实际账单：尚未读取，可点击“刷新用量”。';
  const values = [];
  if (billing.actual_total != null) values.push('累计实际消费 ' + money(billing.actual_total, billing.currency));
  if (billing.balance != null) values.push('账户余额 ' + money(billing.balance, billing.currency));
  return values.join(' · ') + ` · ${billing.source} · ${new Date(billing.checked_at).toLocaleString('zh-CN')}`;
}

function renderKeyEntry(entry) {
  return `<section class="key-entry" data-key-id="${esc(entry.id)}" data-saved="${entry.masked ? 'true' : 'false'}" data-masked="${esc(entry.masked || '')}">
    <div class="key-entry-heading"><code>${esc(entry.masked || '新密钥，待保存')}</code><div class="key-row-actions"><button data-key-action="reveal">查看</button><button data-key-action="copy">复制</button><button data-key-action="up" title="上移优先调用">↑</button><button data-key-action="down" title="下移">↓</button><button class="danger" data-key-action="remove">删除</button></div></div>
    <div class="key-entry-fields">
      <div class="field"><label>备注名称</label><input data-key-field="label" value="${esc(entry.label || '')}" placeholder="例如 主用账户 / 备用账户"></div>
      <label class="check key-enabled"><input type="checkbox" data-key-field="enabled" ${entry.enabled !== false ? 'checked' : ''}>启用</label>
      <div class="field wide"><label>${entry.masked ? '替换 API key（留空保留）' : 'API key'}</label><input type="password" data-key-field="secret" value="" placeholder="${entry.masked ? '需要更换时粘贴新 key' : '粘贴 API key'}" autocomplete="new-password"></div>
      <div class="field wide"><label>DeepSeek 计费账户标识（可选）</label><input data-key-field="billing_account" value="${esc(entry.billing_account || '')}" placeholder="同一个账户的 key 使用相同标识；留空按平台归为同账户"></div>
      <div class="field"><label>默认输入单价</label>${keyPriceInput('input_price', entry.input_price, '留空用模型输入价')}</div>
      <div class="field"><label>默认输出单价</label>${keyPriceInput('output_price', entry.output_price, '留空用模型输出价')}</div>
    </div>
    <code data-key-revealed class="key-revealed hidden"></code>
    <details class="key-model-prices"><summary>按模型单独定价（可选）</summary><div class="key-model-price-list">${Object.entries(entry.model_prices || {}).map(([model, prices]) => renderKeyModelPrice(model, prices)).join('')}</div><button data-key-action="add-price">＋ 增加模型价格</button></details>
    <p class="key-usage">${esc(keyUsageText(entry.usage))}</p>
    <p class="key-billing">${esc(billingText(entry.billing))}</p>
  </section>`;
}

function collectPlatformDraft(card) {
  const draft = {};
  card.querySelectorAll('[data-platform-field]').forEach((input) => {
    draft[input.dataset.platformField] = input.type === 'checkbox' ? input.checked : input.value.trim();
  });
  draft.key_entries = [...card.querySelectorAll('.key-entry')].map((row) => {
    const entry = {id: row.dataset.keyId, model_prices: {}};
    row.querySelectorAll('.key-entry-fields [data-key-field]').forEach((input) => {
      const field = input.dataset.keyField;
      if (field === 'secret' && !input.value.trim() && row.dataset.saved === 'true') return;
      entry[field] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value.trim();
    });
    if (row.dataset.saved !== 'true' && !entry.secret) throw new Error('请填写新增条目的 API key，或删除该空条目。');
    row.querySelectorAll('.key-model-price').forEach((priceRow) => {
      const model = priceRow.querySelector('[data-price-model]').value.trim();
      if (!model) throw new Error('请填写专属价格的实际模型 ID，或删除该价格条目。');
      if (entry.model_prices[model]) throw new Error('同一 key 的模型价格不能重复。');
      const prices = {};
      priceRow.querySelectorAll('[data-key-field]').forEach((input) => {
        prices[input.dataset.keyField] = input.value === '' ? null : Number(input.value);
      });
      entry.model_prices[model] = prices;
    });
    return entry;
  });
  return draft;
}

function filterKeyRows(card) {
  const query = card.querySelector('.key-search').value.trim().toLowerCase();
  card.querySelectorAll('.key-entry').forEach((row) => {
    const text = `${row.dataset.masked} ${row.querySelector('[data-key-field="label"]').value}`.toLowerCase();
    row.classList.toggle('hidden', !text.includes(query));
  });
  card.querySelector('.key-empty').classList.toggle('hidden', card.querySelectorAll('.key-entry').length > 0);
}

async function keyAction(card, button) {
  const action = button.dataset.keyAction;
  const row = button.closest('.key-entry');
  try {
    if (action === 'add') {
      card.querySelector('.key-search').value = '';
      card.querySelector('.key-list').insertAdjacentHTML('beforeend', renderKeyEntry({id: crypto.randomUUID(), enabled: true}));
      filterKeyRows(card);
      card.querySelector('.key-entry:last-child [data-key-field="secret"]').focus();
      return;
    }
    if (action === 'remove') row.remove();
    if (action === 'up' && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
    if (action === 'down' && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    if (action === 'add-price') row.querySelector('.key-model-price-list').insertAdjacentHTML('beforeend', renderKeyModelPrice());
    if (action === 'remove-price') button.closest('.key-model-price').remove();
    if (action === 'reveal' || action === 'copy') {
      const node = row.querySelector('[data-key-revealed]');
      if (action === 'reveal' && !node.classList.contains('hidden')) {
        node.textContent = '';
        node.classList.add('hidden');
        button.textContent = '查看';
        return;
      }
      button.disabled = true;
      const typed = row.querySelector('[data-key-field="secret"]').value.trim();
      if (!typed && row.dataset.saved !== 'true') throw new Error('请先填写这条 API key。');
      const secret = typed || (await api(`/platforms/${card.dataset.platform}/keys/${row.dataset.keyId}/reveal`, {method: 'POST', body: '{}'})).secret;
      if (action === 'copy') {
        await navigator.clipboard.writeText(secret);
        notice('已复制此条密钥。', 'success');
      } else {
        node.textContent = secret;
        node.classList.remove('hidden');
        button.textContent = '隐藏';
        setTimeout(() => {
          node.textContent = '';
          node.classList.add('hidden');
          button.textContent = '查看';
        }, 30000);
      }
    } else if (action === 'refresh') {
      button.disabled = true;
      await refreshKeyUsage();
      notice('调用用量与累计估算费用已刷新。', 'success');
    } else {
      filterKeyRows(card);
      notice('密钥条目已修改，点击“保存全部配置”生效。');
    }
  } catch (error) {
    notice(error.message, 'error');
  } finally {
    button.disabled = false;
  }
}

async function refreshKeyUsage() {
  const latest = await api('/billing/refresh', {method: 'POST', body: '{}'});
  document.querySelectorAll('.platform-card').forEach((card) => {
    const platform = latest.platforms.find((platform) => platform.id === card.dataset.platform);
    card.querySelectorAll('.key-entry').forEach((node) => {
      const entry = platform?.key_entries?.find((entry) => entry.id === node.dataset.keyId);
      node.querySelector('.key-usage').textContent = keyUsageText(entry?.usage);
      node.querySelector('.key-billing').textContent = billingText(entry?.billing);
    });
  });
}
function renderPolicy() {
  const assignment = ([key,label,description]) => {
    const selected = config.models.find((model) => model.id === config.policy[key]);
    const platforms = config.platforms.map((platform) => `<option value="${esc(platform.id)}" ${platform.id === selected?.platform_id ? 'selected' : ''}>${esc(platform.name)}</option>`).join('');
    return `<div class="policy-row"><strong>${label}</strong><div class="policy-model-fields"><select aria-label="${label}的模型配置" data-policy="${key}">${options(config.policy[key])}</select><select aria-label="${label}的平台" data-role-platform="${key}"><option value="">选择平台</option>${platforms}</select><div class="model-picker"><select aria-label="${label}的实际模型 ID" data-role-model="${key}"></select><div class="model-picker-actions"><button type="button" data-role-catalog="${key}">刷新列表</button><button type="button" data-role-manual-toggle="${key}" aria-expanded="false">手动填写</button></div><input class="hidden" aria-label="${label}的手动模型 ID" data-role-model-manual="${key}" maxlength="180" placeholder="输入平台提供的模型 ID"><span class="model-catalog-status" data-role-catalog-status="${key}" aria-live="polite"></span></div><select aria-label="${label}的推理强度" data-reasoning="${key}">${['','low','medium','high','xhigh'].map((value) => `<option value="${value}" ${(config.policy.reasoning?.[key] ?? selected?.reasoning_effort ?? '') === value ? 'selected' : ''}>${value || '默认强度'}</option>`).join('')}</select></div><small>${description}</small></div>`;
  };
  const stage = (key) => assignment(roleRows.find((row) => row[0] === key));
  $('role-table').innerHTML = `
    <article class="position-card" data-position="manager"><div class="position-heading"><span class="position-number">01</span><div><h4>项目经理</h4><p>判断这次消息的难度；需要多人协作时，再拆分任务、安排依赖。</p></div></div><div class="policy-table">${stage('router')}${stage('planner')}</div></article>
    <article class="position-card" data-position="engineer"><div class="position-heading"><span class="position-number">02</span><div><h4>研发工程师</h4><p>负责回答问题、分析算法和实现代码。每个子任务按难度选择模型，独立任务可并行执行。</p></div></div><a class="position-link" href="#difficulty-models">设置下方的“任务难度与模型” ↓</a></article>
    <article class="position-card" data-position="tester"><div class="position-heading"><span class="position-number">03</span><div><h4>测试工程师</h4><p>复杂协作后检查正确性、边界和风险，有问题就交回研发工程师修正。</p></div></div><div class="policy-table">${stage('reviewer')}</div><label class="check position-option"><input type="checkbox" data-policy="review_by_risk" ${config.policy.review_by_risk !== false ? 'checked' : ''} ${Object.hasOwn(config.policy, 'review_by_risk') ? '' : 'disabled'}>复杂或高风险任务，使用对应难度的模型复核</label><p class="small-note">开启后，复杂开发和高风险任务使用“复杂开发”档，算法攻坚使用“算法攻坚”档；推理强度采用测试工程师的设置。</p></article>
    <article class="position-card" data-position="assistant"><div class="position-heading"><span class="position-number">04</span><div><h4>项目助理</h4><p>需要合并多个成果时整理交付；简单对话直接交付研发工程师的回答。</p></div></div><div class="policy-table">${stage('summarizer')}</div><label class="check position-option"><input type="checkbox" data-policy="summarize_single_task" ${config.policy.summarize_single_task ? 'checked' : ''} ${Object.hasOwn(config.policy, 'summarize_single_task') ? '' : 'disabled'}>单个子任务复核通过后，也请项目助理整理报告</label><p class="small-note">默认关闭，直接采用原成果并附复核说明，节省一次模型调用。</p></article>`;
  $('difficulty-table').innerHTML = roleRows.filter((row) => ['simple', 'standard', 'advanced', 'hard', 'expert'].includes(row[0])).map(assignment).join('');
  $('vision-table').innerHTML = stage('vision');
  $('role-table').querySelector('.position-link').onclick = (event) => {
    event.preventDefault();
    $('difficulty-models').scrollIntoView({block: 'start', behavior: 'smooth'});
  };
  $('view-policy').querySelectorAll('select[data-policy]').forEach((input) => input.onchange = () => {
    config.policy[input.dataset.policy] = input.value;
    syncRoleFields();
    void ensureRoleCatalogues();
  });
  $('view-policy').querySelectorAll('[data-role-model], [data-role-platform], [data-role-model-manual]').forEach((input) => input.oninput = () => {
    const role = input.dataset.roleModel || input.dataset.rolePlatform || input.dataset.roleModelManual;
    const selected = config.models.find((model) => model.id === config.policy[role]);
    if (!selected) return;
    selected[input.dataset.rolePlatform ? 'platform_id' : 'model'] = input.value.trim();
    syncRoleFields();
    if (input.dataset.rolePlatform) void ensureRoleCatalogues();
  });
  $('view-policy').querySelectorAll('[data-role-catalog]').forEach((button) => button.onclick = () => {
    const selected = config.models.find((model) => model.id === config.policy[button.dataset.roleCatalog]);
    void readPlatformCatalogue(selected?.platform_id, true).catch(() => {});
  });
  $('view-policy').querySelectorAll('[data-role-manual-toggle]').forEach((button) => button.onclick = () => {
    const input = document.querySelector(`[data-role-model-manual="${button.dataset.roleManualToggle}"]`);
    const open = input.classList.contains('hidden');
    input.classList.toggle('hidden', !open);
    button.textContent = open ? '收起手动填写' : '手动填写';
    button.setAttribute('aria-expanded', String(open));
    if (open) input.focus();
  });
  syncRoleFields();
  const settings = [
    ['max_parallel', '子任务最大并行数', 1, 8], ['max_tasks', '最多拆分子任务数', 1, 12],
    ['max_escalations', '单阶段最多升级次数', 0, 2], ['repair_rounds', '复核后最多修正轮数', 0, 1],
  ];
  $('policy-fields').innerHTML = settings.map(([key,label,min,max]) => `<div class="field"><label>${label}</label><input type="number" data-policy="${key}" value="${config.policy[key] ?? (key === 'conversation_context_chars' ? 24000 : '')}" min="${min}" max="${max}"></div>`).join('') + `
    <div class="field"><label>价格与金额预算使用的币种</label><select data-policy="currency"><option value="CNY" ${config.policy.currency === 'CNY' ? 'selected' : ''}>人民币 CNY</option><option value="USD" ${config.policy.currency === 'USD' ? 'selected' : ''}>美元 USD</option></select></div>
    <div class="field"><label>单任务费用上限（可留空）</label><input type="number" data-policy="money_budget" value="${config.policy.money_budget ?? ''}" min="0.0001" step="any" placeholder="留空不设置费用上限"></div>
    <div class="field"><label>美元兑人民币预算换算汇率（可选）</label><input type="number" data-policy="usd_cny_rate" value="${config.policy.usd_cny_rate ?? ''}" min="0.0001" step="any" placeholder="跨币种费用预算时填写"></div>`;
}

function syncRoleFields() {
  roleRows.forEach(([role]) => {
    const selected = config.models.find((model) => model.id === config.policy[role]);
    const idInput = document.querySelector(`[data-role-model="${role}"]`);
    if (!idInput) return;
    const platform = cataloguePlatform(selected?.platform_id);
    const identity = platform ? catalogueIdentity(platform) : '';
    const catalogue = platformCatalogues.get(identity);
    const loading = catalogueRequests.has(identity);
    const error = catalogueErrors.get(identity);
    const models = catalogue?.models || [];
    const current = selected?.model || '';
    idInput.innerHTML = `<option value="">${loading ? '正在读取模型列表…' : models.length ? '请选择实际模型' : '暂无列表，请刷新或手动填写'}</option>`
      + (current && !models.includes(current) ? `<option value="${esc(current)}">${esc(current)}（当前配置${catalogue ? '，未列出' : ''}）</option>` : '')
      + models.map((model) => `<option value="${esc(model)}">${esc(model)}</option>`).join('');
    idInput.value = current;
    const manual = document.querySelector(`[data-role-model-manual="${role}"]`);
    if (manual.value.trim() !== current) manual.value = current;
    document.querySelector(`[data-role-platform="${role}"]`).value = selected?.platform_id || '';
    document.querySelector(`[data-role-catalog="${role}"]`).disabled = !platform || loading;
    const status = document.querySelector(`[data-role-catalog-status="${role}"]`);
    status.classList.toggle('error', !!error);
    status.textContent = loading ? '正在读取平台列表…' : error ? `${error}；可重试或手动填写，当前配置已保留。` : catalogue ? `${models.length} 个模型可选 · ${catalogue.cached === false ? '仅本次打开有效' : '已在本机缓存'}` : platform ? '配置地址和密钥后自动读取，也可手动填写。' : '请先选择平台。';
  });
}

function cataloguePlatform(id) {
  const platform = config.platforms.find((platform) => platform.id === id);
  if (!platform) return null;
  const card = document.querySelector(`[data-platform="${id}"]`);
  return {...platform,
    base_url: card?.querySelector('[data-platform-field="base_url"]').value.trim() ?? platform.base_url,
    protocol: card?.querySelector('[data-platform-field="protocol"]').value ?? platform.protocol};
}

function catalogueIdentity(platform) {
  return JSON.stringify([platform.id, platform.base_url.trim().replace(/\/+$/, ''), platform.protocol]);
}

function catalogueModelIds(models) {
  return [...new Set((Array.isArray(models) ? models : []).filter((model) => typeof model === 'string' && model.trim() && model.length <= 180))].sort().slice(0, 1000);
}

function restoreCatalogues() {
  try {
    const entries = JSON.parse(localStorage.getItem(catalogueStorageKey) || '[]');
    if (!Array.isArray(entries)) return;
    entries.slice(-40).forEach((entry) => {
      if (!entry || typeof entry.id !== 'string' || typeof entry.base_url !== 'string' || typeof entry.protocol !== 'string') return;
      const models = catalogueModelIds(entry.models);
      if (models.length) platformCatalogues.set(catalogueIdentity(entry), {...entry, models});
    });
  } catch (_) { /* 缓存不可用时仍可重新读取模型列表。 */ }
}

async function readPlatformCatalogue(id, force = false) {
  const platform = cataloguePlatform(id);
  if (!platform) return;
  const identity = catalogueIdentity(platform);
  if (catalogueRequests.has(identity)) return catalogueRequests.get(identity);
  if (!force && (platformCatalogues.has(identity) || catalogueErrors.has(identity))) return;
  const request = (async () => {
    // 先登记请求，多个职位共用一个平台时只读取一次。
    await Promise.resolve();
    try {
      const card = document.querySelector(`[data-platform="${id}"]`);
      const draft = collectPlatformDraft(card);
      const result = await api(`/platforms/${id}/catalog`, {method: 'POST', body: JSON.stringify({draft})});
      const models = catalogueModelIds(result.models);
      if (!models.length) throw new Error('平台未返回可用的模型 ID');
      const catalogue = {id, base_url: platform.base_url, protocol: platform.protocol, models};
      platformCatalogues.set(identity, catalogue);
      let cached = true;
      try {
        const entries = [...platformCatalogues.values()].slice(-40).map(({id, base_url, protocol, models}) => ({id, base_url, protocol, models}));
        localStorage.setItem(catalogueStorageKey, JSON.stringify(entries));
      } catch (_) { cached = false; }
      catalogue.cached = cached;
      catalogueErrors.delete(identity);
      return {...catalogue, cached};
    } catch (error) {
      catalogueErrors.set(identity, error.message);
      throw error;
    } finally {
      catalogueRequests.delete(identity);
      syncRoleFields();
    }
  })();
  catalogueRequests.set(identity, request);
  catalogueErrors.delete(identity);
  syncRoleFields();
  return request;
}

async function ensureRoleCatalogues() {
  const ids = new Set(roleRows.map(([role]) => config.models.find((model) => model.id === config.policy[role])?.platform_id));
  await Promise.allSettled([...ids].filter((id) => {
    const platform = cataloguePlatform(id);
    const card = platform && document.querySelector(`[data-platform="${id}"]`);
    return platform?.base_url && [...(card?.querySelectorAll('.key-entry') || [])].some((row) =>
      row.querySelector('[data-key-field="enabled"]').checked && (row.dataset.saved === 'true' || row.querySelector('[data-key-field="secret"]').value.trim()));
  }).map((id) => readPlatformCatalogue(id)));
}

function collectConfig() {
  const models = config.models.map((model) => {
    const card = document.querySelector(`[data-model="${model.id}"]`);
    const item = { ...model };
    card.querySelectorAll('[data-field]').forEach((input) => {
      const key = input.dataset.field;
      item[key] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value.trim();
    });
    return item;
  });
  const policy = { ...config.policy, reasoning: { ...(config.policy.reasoning || {}) } };
  document.querySelectorAll('[data-policy]').forEach((input) => {
    policy[input.dataset.policy] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? (input.value === '' ? null : Number(input.value)) : input.value;
  });
  document.querySelectorAll('[data-role-model]').forEach((input) => {
    const role = input.dataset.roleModel;
    const selected = models.find((item) => item.id === policy[role]);
    if (selected) selected.model = input.value.trim();
  });
  document.querySelectorAll('[data-reasoning]').forEach((input) => { policy.reasoning[input.dataset.reasoning] = input.value; });
  const platforms = (config.platforms || []).filter((platform) => platform.id !== 'economy-platform').map((platform) => {
    const card = document.querySelector(`[data-platform="${platform.id}"]`);
    if (!card) return platform;
    return { ...platform, ...collectPlatformDraft(card) };
  });
  models.forEach((item) => { if (item.platform_id === 'economy-platform') item.platform_id = ''; });
  return { platforms, models, policy };
}

async function saveConfig(silent = false) {
  if (saving) throw new Error('配置正在保存，请稍候。');
  saving = true;
  document.querySelectorAll('.save-config').forEach((button) => button.disabled = true);
  try {
    config = await api('/config', { method: 'PUT', body: JSON.stringify(collectConfig()) });
    renderModels(); renderPolicy(); renderSummary();
    if (!silent) notice('配置已保存，密钥已在本机加密。', 'success');
    catalogueErrors.clear();
    void ensureRoleCatalogues();
  } finally {
    saving = false;
    document.querySelectorAll('.save-config').forEach((button) => button.disabled = false);
  }
}

function renderSummary() {
  const p = config.policy;
  const ready = config.models.filter((model) => model.ready);
  $('ready-hint').textContent = ready.length ? `${ready.length} 个模型已配置` : '请先在“平台与密钥”填写接口';
  $('parallel-pill').textContent = `最多并行 ${p.max_parallel} 个`;
  $('budget-pill').textContent = p.money_budget == null ? '费用上限待填写' : `本任务上限 ${money(p.money_budget, p.currency)}`;
  const groups = [
    ['独立难度判断', p.router, '只分类不求解，输出简短的难度和分流理由。'],
    ['小任务执行', p.simple, '由一个模型执行一次，不再拆解、复核和整合。'],
    ['拆解与常规分析', p.planner, '先把目标拆成能独立验收的工作。'],
    ['日常代码', p.advanced, '常规实现、代码设计与问题定位。'],
    ['复杂实现', p.hard, '复杂逻辑和多模块方案。'],
    ['最高难度推导', p.expert, '算法攻坚与必要的关键复核。'],
    ['图片后备模型', p.vision, '只有任务含图片且没有更合适模型时使用。'],
  ];
  $('route-summary').innerHTML = groups.map(([label, id, desc]) => {
    const m = config.models.find((entry) => entry.id === id);
    return `<div class="route-card"><span>${esc(label)}</span><strong>${esc(m?.name || id)}</strong><small>${esc(desc)}${m?.ready ? '' : ' · 待配置'}</small></div>`;
  }).join('');
}
async function platformAction(button) {
  const card = button.closest('[data-platform]');
  const id = card.dataset.platform;
  const resultNode = card.querySelector('.model-result');
  button.disabled = true;
  resultNode.textContent = '正在读取平台模型列表';
  try {
    const result = await readPlatformCatalogue(id, true);
    resultNode.textContent = `读取到 ${result.models.length} 个模型 ID。请在分工页的“实际模型”下拉框选择。`;
    notice(result.cached ? '平台模型列表已读取并缓存，可在分工页下拉选择。' : '平台模型列表已读取；本机缓存保存失败，重新打开后可再次读取。', 'success');
  } catch (error) {
    resultNode.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
async function refreshHistory() {
  const [jobsResult, conversationsResult] = await Promise.allSettled([api('/jobs'), api('/conversations')]);
  if (jobsResult.status !== 'fulfilled') throw jobsResult.reason;
  conversationReady = conversationsResult.status === 'fulfilled';
  $('conversation-upgrade').classList.toggle('hidden', conversationReady);
  const conversations = conversationReady ? conversationsResult.value.conversations : [];
  const jobs = jobsResult.value.jobs.filter((job) => !job.conversation_id);
  const chats = conversations.map((chat) => `<button data-conversation="${esc(chat.id)}" class="${currentConversation?.id === chat.id ? 'selected' : ''}">${esc(chat.title)}<small>${number(chat.turn_count)} 轮 · ${esc(phases[chat.status] || '等待消息')}</small></button>`).join('');
  const legacy = jobs.length ? `<details class="legacy-history"><summary>历史任务</summary>${jobs.map((job) => `<button data-job="${job.id}" class="${currentJob?.id === job.id && !currentConversation ? 'selected' : ''}">${esc(job.title)}<small>${esc(phases[job.status] || job.status)} · ${new Date(job.created_at).toLocaleDateString('zh-CN')}</small></button>`).join('')}</details>` : '';
  $('history').innerHTML = chats + legacy || '<p class="muted">对话会保存在这里，可随时打开继续聊。</p>';
  $('history').querySelectorAll('button').forEach((button) => button.onclick = () => openJob(button.dataset.job));
  $('history').querySelectorAll('[data-conversation]').forEach((button) => button.onclick = () => openConversation(button.dataset.conversation).catch((error) => notice(error.message, 'error')));
  updateSendState();
}

function updateSendState() {
  const active = currentConversation && activePhases.has(currentConversation.status);
  $('run-task').disabled = !conversationReady || sending || !!active;
  $('run-task').textContent = $('execute-project').checked ? '发送并执行 →' : '发送 →';
  $('project-mcp-options').classList.toggle('hidden', !$('execute-project').checked || !$('project-mcp-options').childElementCount);
  $('stop-reply').classList.toggle('hidden', !active);
}

function conversationScroller() {
  const main = document.querySelector('main');
  return getComputedStyle(main).overflowY === 'auto' ? main : document.scrollingElement;
}

function scrollConversationEnd() {
  const scroll = conversationScroller();
  scroll.scrollTop = scroll.scrollHeight;
}

function renderConversation() {
  const chat = currentConversation;
  $('conversation-panel').classList.toggle('hidden', !chat);
  if (!chat) return;
  const scroll = conversationScroller();
  const follow = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 150;
  $('task-intro').classList.toggle('hidden', chat.messages.length > 0);
  $('conversation-title').textContent = chat.title;
  const costs = Object.entries(chat.usage.actual_costs || {}).map(([currency, cost]) => money(cost, currency)).join(' ＋ ');
  $('conversation-usage').textContent = `${number(chat.job_ids.length)} 轮 · ${number(chat.usage.input_tokens + chat.usage.output_tokens)} Token · 平台消费变化：${costs || '尚未核对'}`;
  $('conversation-messages').innerHTML = chat.messages.map((message) => {
    const assistant = message.role === 'assistant';
    const active = assistant && activePhases.has(message.status);
    const route = message.routing?.route === 'collaboration' ? '多模型协作' : message.routing?.route === 'direct' ? '单模型回答' : '自动判断';
    const content = message.content || (active ? (phases[message.status] || '正在处理') + '…' : message.error || '本轮未生成完整回复，可在下方补充要求后继续。');
    return `<article class="chat-message ${assistant ? 'assistant-message' : 'user-message'}"><div class="chat-message-heading"><strong>${assistant ? 'AgentScope' : '你'}</strong>${assistant ? `<span class="tag">${route}</span>` : ''}</div><pre class="chat-text">${esc(content)}</pre>${assistant && message.error && message.content ? `<p class="chat-error">${esc(message.error)}</p>` : ''}${!assistant && (message.image_names?.length || message.has_context) ? `<small class="muted">${message.has_context ? '附有补充材料 ' : ''}${esc((message.image_names || []).join('、'))}</small>` : ''}${assistant ? `<div class="chat-message-footer"><button data-open-job="${esc(message.job_id)}">${active ? '查看执行进度' : '执行明细与费用'}</button><small>${number(message.call_count)} 次调用${message.history_truncated ? ' · 较长历史已截取，必要时请重新附上完整代码' : ''}</small></div>` : ''}</article>`;
  }).join('');
  $('conversation-messages').querySelectorAll('[data-open-job]').forEach((button) => button.onclick = () => openJob(button.dataset.openJob));
  updateSendState();
  if (follow) scrollConversationEnd();
}

async function refreshConversation() {
  const id = currentConversation?.id;
  if (!id) return;
  const chat = await api('/conversations/' + id);
  if (currentConversation?.id !== id) return;
  currentConversation = chat;
  renderConversation();
}

function rememberDraft() {
  conversationDrafts.set(currentConversation?.id || 'new', {prompt: $('prompt').value, context: $('context').value, images: [...images]});
}

function restoreDraft(id) {
  const draft = conversationDrafts.get(id) || {prompt: '', context: '', images: []};
  $('prompt').value = draft.prompt;
  $('context').value = draft.context;
  $('execute-project').checked = false;
  $('project-mcp-options').querySelectorAll('input').forEach((input) => { input.checked = false; });
  images = [...draft.images];
  $('file-count').textContent = draft.context ? '已附补充材料' : '';
  $('image-names').textContent = images.map((image) => image.name).join('、');
  $('clear-images').classList.toggle('hidden', !images.length);
}

async function openConversation(id) {
  rememberDraft();
  const generation = ++conversationGeneration;
  clearTimeout(pollTimer);
  const chat = await api('/conversations/' + id);
  if (generation !== conversationGeneration) return;
  currentConversation = chat;
  restoreDraft(id);
  currentJob = null;
  $('job-changes').open = false;
  renderTerminalRequest(null);
  localStorage.setItem('agentscope_active_conversation', id);
  view('tasks');
  $('composer').classList.remove('hidden');
  $('execution-details').classList.add('hidden');
  $('execution-details').open = false;
  renderConversation();
  const latest = chat.job_ids.at(-1);
  if (latest) {
    const job = await api('/jobs/' + latest);
    if (generation !== conversationGeneration) return;
    currentJob = job;
    $('job-changes').open = false;
    renderJob();
    schedulePoll();
  }
  await refreshHistory();
  scrollConversationEnd();
}

async function openJob(id) {
  clearTimeout(pollTimer);
  try {
    const job = await api('/jobs/' + id);
    if (job.conversation_id && currentConversation?.id !== job.conversation_id) await openConversation(job.conversation_id);
    currentJob = job;
    if (!job.conversation_id) {
      currentConversation = null;
      renderConversation();
    }
    view('tasks');
    $('composer').classList.remove('hidden'); $('task-intro').classList.add('hidden'); $('route-summary').classList.add('hidden');
    $('execution-details').classList.remove('hidden');
    $('execution-details').open = true;
    $('job-panel').classList.remove('hidden');
    renderJob(); await refreshHistory(); schedulePoll();
    $('execution-details').scrollIntoView({block: 'start'});
  } catch (error) { notice(error.message, 'error'); }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (!currentJob || !activePhases.has(currentJob.status)) return;
  const id = currentJob.id;
  pollTimer = setTimeout(async () => {
    try {
      const job = await api('/jobs/' + id);
      if (currentJob?.id !== id) return;
      currentJob = job; renderJob();
      if (job.conversation_id === currentConversation?.id) await refreshConversation();
      if (!activePhases.has(job.status)) await refreshHistory();
      schedulePoll();
    } catch (error) { notice(error.message, 'error'); }
  }, 1800);
}

function money(cost, currency) {
  return `${currency === 'USD' ? '$' : '¥'}${Number(cost || 0).toLocaleString('zh-CN', {minimumFractionDigits: 5, maximumFractionDigits: 12, useGrouping: false})}`;
}

function renderJob() {
  const job = currentJob;
  $('execution-details').classList.remove('hidden');
  $('job-panel').classList.remove('hidden');
  $('job-title').textContent = job.title;
  $('job-phase').textContent = phases[job.status] || job.status;
  $('cancel-job').classList.toggle('hidden', !activePhases.has(job.status));
  $('export-job').href = '/dispatch/api/jobs/' + job.id + '/report';
  $('job-error').classList.toggle('hidden', !job.error);
  $('job-error').textContent = job.error;
  $('routing-reason').classList.toggle('hidden', !job.routing);
  $('routing-reason').textContent = job.routing ? `${job.routing.route === 'direct' ? '小任务单模型执行' : '需要多模型协作'}${job.routing.independent ? ` · 独立判断：${job.routing.model_name || job.routing.model_id}` : ''} · ${job.routing.reason}` : '';
  $('job-sources').classList.toggle('hidden', !job.knowledge_sources?.length);
  $('source-list').innerHTML = (job.knowledge_sources || []).map((source, index) => `<p>[资料${index + 1}] ${esc(source.name)} · 片段 ${number(source.chunk_index + 1)}</p>`).join('');
  $('job-code-sources').classList.toggle('hidden', !job.code_sources?.length);
  $('code-source-list').innerHTML = (job.code_sources || []).map((source, index) => `<p>[代码${index + 1}] ${esc(source.repository)}/${esc(source.path)}:${number(source.line)}${source.index_stale ? ' · 文件有新修改，建议更新索引' : ''}</p>`).join('');
  renderToolRuns(job);
  renderTerminalRequest(job);
  $('job-changes').classList.toggle('hidden', !job.execution_root);
  if ($('job-changes').open && job.execution_root && !activePhases.has(job.status)) void loadJobChanges(job);
  $('job-memory').classList.toggle('hidden', !job.shared_memory);
  if (job.shared_memory) {
    const memory = job.shared_memory;
    $('memory-summary').textContent = memory.plan_summary || '本任务的目标、资料和成果记录';
    $('memory-sources').textContent = `本任务共享 ${number(memory.knowledge_sources)} 个资料片段、${number(memory.code_sources)} 个代码片段${memory.visual_available ? '，以及图片分析记录' : ''}。执行者接收分工总览和相关依赖成果；复核与整合接收完整成果。`;
    $('memory-artifacts').innerHTML = (memory.artifacts || []).map((artifact) => `<div class="memory-artifact"><strong>${esc(artifact.title)}</strong><span>${esc({pending: '待执行', done: '已完成', running: '执行中', failed: '失败', interrupted: '已中断'}[artifact.status] || artifact.status)} · 版本 ${number(artifact.revision)} · ${artifact.available ? '当前可引用' : '待更新，不作为当前依据'}</span></div>`).join('');
    $('memory-findings').innerHTML = (memory.review?.findings || []).map((finding) => `<p>修正 ${esc(finding.task_id)}：${esc(finding.issue)}</p>`).join('');
  }
  $('stat-progress').textContent = `${job.tasks.filter((task) => task.status === 'done').length} / ${job.tasks.length}`;
  $('stat-tokens').textContent = `${number(job.usage.input_tokens)} / ${number(job.usage.output_tokens)}`;
  $('stat-cost').textContent = Object.entries(job.usage.actual_costs || {}).map(([unit, value]) => money(value, unit)).join(' ＋ ') || '尚未核对';
  $('billing-state').textContent = job.billing ? Object.values(job.billing.errors || {}).join('；') || '按平台累计实际消费或账户余额变化核对。' : '历史记录尚无平台账单数据。';
  $('stat-calls').textContent = `${job.calls.length} 次${job.usage.estimated_calls ? ' · 含估算' : ''}`;
  const expanded = new Set([...$('task-list').querySelectorAll('details[open]')].map((node) => node.dataset.id));
  const visual = job.visual_analysis ? `<details class="task-step" data-id="_vision" ${expanded.has('_vision') ? 'open' : ''}><summary><span>✓</span><strong>图片分析</strong><span class="meta">视觉模型 · 已完成</span></summary><div class="step-content"><pre>${esc(job.visual_analysis)}</pre></div></details>` : '';
  const statusNames = {done: '已完成', pending: '待执行', running: '执行中', failed: '未完成', interrupted: '已中断', blocked: '依赖未完成'};
  const taskHtml = job.tasks.length ? job.tasks.map((task) => {
    const icon = task.status === 'done' ? '✓' : task.status === 'running' ? '●' : '○';
    const dependency = task.depends_on.join('、') || '无，可独立执行';
    const modelName = job.calls.find((call) => call.model_id === task.model_id)?.name || task.model_id || '待分配';
    return `<details class="task-step" data-id="${task.id}" ${expanded.has(task.id) ? 'open' : ''}><summary><span>${icon}</span><strong>${esc(task.title)}</strong><span class="meta">${esc(difficulty[task.difficulty])} · ${esc(statusNames[task.status] || task.status)}</span></summary><div class="step-content"><p>验收：${esc(task.acceptance)}</p><p>依赖：${esc(dependency)} · 使用：${esc(modelName)}</p><pre>${esc(task.output || task.instruction)}</pre></div></details>`;
  }).join('') : '<div class="panel"><p>负责人正在分析目标和输入材料，生成子任务及依赖关系。</p></div>';
  $('task-list').innerHTML = visual + taskHtml;
  $('events').innerHTML = job.events.slice(-35).map((event) => `<div class="event"><time>${new Date(event.time).toLocaleTimeString('zh-CN')}</time>${esc(event.message)}</div>`).join('');
  const callStatus = {done: '已完成', failed: '失败', running: '执行中', cancelled: '已停止'};
  $('call-list').innerHTML = job.calls.map((call) => `<div class="call"><strong>${esc(call.position ? call.position + ' · ' + call.label : call.label)}</strong><br>${esc(call.name)}<br>${esc(call.model)}${call.key_label ? `<br>使用密钥：${esc(call.key_label)}` : ''}<br>输入 ${number(call.input_tokens)} / 输出 ${number(call.output_tokens)}<br>${call.observed_cost != null ? money(call.observed_cost, call.observed_currency) + '（平台消费变化）' : '平台消费尚未核对'}${call.input_price != null ? `<br>输入价 ${number(call.input_price)} / 输出价 ${number(call.output_price)} / 百万 Token` : ''}${call.estimated ? ' · 用量估算' : ''}${call.uncertain ? ' · 计费待核实' : ''}<br>${esc(callStatus[call.status] || call.status)}${call.error ? '：' + esc(call.error) : ''}</div>`).join('');
  $('final-report').classList.toggle('hidden', !job.report);
  $('final-report').querySelector('pre').textContent = job.report;
}

function renderToolRuns(job) {
  $('job-tool-runs').classList.toggle('hidden', !job.execute_project);
  if (!job.execute_project) return;
  const names = {project_search: '搜索代码', project_read: '读取文件', project_edit: '修改文件',
    project_create: '新建文件', project_powershell: 'PowerShell', project_build: '编译', project_test: '测试',
    project_files: '查找文件', project_git: 'Git', project_rules: '项目规则', project_skill: '技能',
    project_diagnostics: 'AOI 工具', project_knowledge: '知识库', project_mcp: 'MCP', project_plan: '计划更新', project_terminal: '终端'};
  const statuses = {waiting: '等待确认', running: '运行中', done: '成功', failed: '失败', cancelled: '已停止'};
  const open = new Set([...$('tool-run-list').querySelectorAll('details[open]')].map((item) => item.dataset.run));
  const summary = job.execution_summary;
  const header = summary
    ? `<p class="small-note">修改 ${number(summary.modified.length)} 个文件 · 编译 ${esc(summary.build)} · 测试 ${esc(summary.test)}</p>`
    : '';
  const stepStatuses = {pending: '待处理', working: '进行中', done: '已完成'};
  const plans = Object.entries(job.work_plans || {}).map(([label, steps]) =>
    `<div class="execution-plan"><strong>${esc(label)}</strong><ul>${steps.map((step) =>
      `<li>${esc(step.title)} <small>${esc(stepStatuses[step.status] || step.status)}</small></li>`).join('')}</ul></div>`).join('');
  const entries = (job.tool_runs || []).map((row, index) => {
    const detail = row.arguments.path || row.arguments.query || row.arguments.name || row.arguments.tool || row.arguments.action || '';
    const caption = `${names[row.tool] || row.tool} ${detail} · ${statuses[row.status] || row.status}`;
    const output = [row.command, row.exit_code != null ? '退出码 ' + row.exit_code : '', row.error, row.output].filter(Boolean).join('\n');
    return `<details class="tool-run" data-run="${index}" ${open.has(String(index)) ? 'open' : ''}><summary>${esc(caption)}</summary><pre>${esc(output || row.label)}</pre></details>`;
  });
  $('tool-run-list').innerHTML = plans + header + (entries.join('') || '<p class="small-note">本轮暂无本机工具调用。</p>');
}

function renderTerminalRequest(job) {
  const area = $('terminal-request');
  const entry = job?.tool_runs?.find((row) => row.status === 'waiting' && row.request_id);
  if (!entry || !activePhases.has(job.status) || job.status === 'cancelling') {
    area.classList.add('hidden');
    area.dataset.request = '';
    return;
  }
  if (area.dataset.request === entry.request_id) return;
  area.dataset.request = entry.request_id;
  area.classList.remove('hidden');
  area.innerHTML = `<strong>确认运行 PowerShell 命令</strong>
    <p>${esc(entry.description)}</p>
    <p class="small-note">工作目录 ${esc(entry.directory)}。命令使用当前 Windows 账户，直接产生的改动不计入逐文件撤销。</p>
    <pre>${esc(entry.command)}</pre>
    <div class="actions"><button class="primary" data-terminal="run">运行一次</button><button data-terminal="skip">跳过</button></div>
    <p class="small-note" data-terminal-message></p>`;
  const buttons = area.querySelectorAll('[data-terminal]');
  buttons.forEach((button) => {
    button.disabled = terminalDecision === entry.request_id;
    button.onclick = async () => {
      terminalDecision = entry.request_id;
      buttons.forEach((control) => { control.disabled = true; });
      const message = area.querySelector('[data-terminal-message]');
      try {
        await api('/jobs/' + job.id + '/terminal/' + entry.request_id, {method: 'POST',
          body: JSON.stringify({approved: button.dataset.terminal === 'run'})});
        message.textContent = '已提交。';
      } catch (error) {
        terminalDecision = null;
        message.textContent = error.message;
        buttons.forEach((control) => { control.disabled = false; });
      }
    };
  });
}

$('job-changes').ontoggle = () => {
  if ($('job-changes').open && currentJob) void loadJobChanges(currentJob);
};

async function loadJobChanges(job) {
  const sequence = ++changeRequest;
  const area = $('job-change-files');
  try {
    const {files} = await api('/jobs/' + job.id + '/changes');
    if (sequence !== changeRequest || currentJob?.id !== job.id) return;
    area.innerHTML = '';
    if (!files.length) {
      area.textContent = '本轮暂无已记录的文件改动。';
      return;
    }
    for (const file of files) {
      const row = document.createElement('details');
      row.className = 'tool-run';
      row.innerHTML = `<summary>${esc(file.path)}${file.restored ? ' · 已撤销' : ''}</summary><div class="change-content"></div>`;
      row.ontoggle = async () => {
        const content = row.querySelector('.change-content');
        if (!row.open || content.childElementCount) return;
        const endpoint = '/jobs/' + job.id + '/changes/' + encodeURIComponent(file.id);
        try {
          const detail = await api(endpoint);
          const diff = document.createElement('pre');
          diff.textContent = detail.diff || '无行级差异';
          content.append(diff);
          if (!detail.restored && !activePhases.has(job.status)) {
            const button = document.createElement('button');
            button.textContent = '撤销本轮对此文件的改动';
            button.onclick = async () => {
              if (!window.confirm('恢复本轮开始前的文件版本？')) return;
              button.disabled = true;
              try {
                await api(endpoint + '/restore', {method: 'POST', body: '{}'});
                await loadJobChanges(job);
              } catch (error) {
                const message = document.createElement('p');
                message.textContent = error.message;
                content.append(message);
                button.disabled = false;
              }
            };
            content.append(button);
          }
        } catch (error) {
          content.textContent = error.message;
        }
      };
      area.append(row);
    }
  } catch (error) {
    if (sequence === changeRequest && currentJob?.id === job.id) area.textContent = error.message;
  }
}
document.querySelectorAll('[data-view]').forEach((button) => button.onclick = () => view(button.dataset.view));
document.querySelectorAll('.save-config').forEach((button) => button.onclick = () => saveConfig().catch((error) => notice(error.message, 'error')));
$('refresh-history').onclick = () => refreshHistory().catch((error) => notice(error.message, 'error'));
$('new-task').onclick = () => {
  rememberDraft();
  ++conversationGeneration;
  clearTimeout(pollTimer);
  currentJob = null;
  renderTerminalRequest(null);
  currentConversation = null;
  restoreDraft('new');
  localStorage.removeItem('agentscope_active_conversation');
  view('tasks');
  renderConversation();
  $('composer').classList.remove('hidden');
  $('task-intro').classList.remove('hidden');
  $('route-summary').classList.add('hidden');
  $('execution-details').classList.add('hidden');
  $('execution-details').open = false;
  $('job-panel').classList.add('hidden');
  refreshHistory().catch(() => {});
  $('prompt').focus();
};
$('add-platform').onclick = async () => {
  try {
    await saveConfig(true);
    if ((config.platforms || []).length >= 20) throw new Error('最多保存 20 个平台。');
    config.platforms.push({id: 'platform-' + Date.now().toString(36), name: '自定义平台', enabled: false, base_url: '', protocol: 'chat', has_keys: false, key_count: 0, model_count: 0});
    renderModels();
    renderPolicy();
    renderSummary();
    notice('已增加平台。填写地址和备用 key 后保存；模型 ID 与推理强度在分工页设置。');
    $('platform-cards').lastElementChild.scrollIntoView({behavior: 'smooth', block: 'center'});
  } catch (error) {
    notice(error.message, 'error');
  }
};
$('files').onchange = async () => {
  const files = [...$('files').files];
  try {
    if (files.reduce((sum, file) => sum + file.size, 0) > 700000) throw new Error('附件总大小请控制在 700 KB 内。');
    const texts = await Promise.all(files.map(async (file) => `\n\n文件：${file.name}\n${await file.text()}`));
    const combined = $('context').value + texts.join('');
    if (combined.length > 180000) throw new Error('输入材料超过 18 万字符，请减少附件。');
    $('context').value = combined;
    $('file-count').textContent = `已添加 ${files.length} 个文件`;
    document.querySelector('.context-box').open = true;
  } catch (error) {
    notice(error.message, 'error');
  }
  $('files').value = '';
};
$('images').onchange = async () => {
  try {
    const files = [...$('images').files];
    if (images.length + files.length > 3) throw new Error('最多添加 3 张图片。');
    const added = [];
    for (const file of files) {
      if (file.size > 15000000) throw new Error('原始图片请控制在 15 MB 内。');
      const bitmap = await createImageBitmap(file);
      try {
        const ratio = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
        canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'white';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        added.push({name: file.name, media_type: 'image/jpeg', data: canvas.toDataURL('image/jpeg', .88).split(',')[1]});
      } finally {
        bitmap.close();
      }
    }
    images.push(...added);
    $('image-names').textContent = images.map((image) => image.name).join('、');
    $('clear-images').classList.toggle('hidden', !images.length);
  } catch (error) {
    notice(error.message, 'error');
  }
  $('images').value = '';
};
$('clear-images').onclick = () => {
  images = [];
  $('image-names').textContent = '';
  $('clear-images').classList.add('hidden');
};
$('run-task').onclick = async () => {
  const prompt = $('prompt').value.trim();
  if (!prompt) {
    notice('请先填写消息。');
    return;
  }
  if (sending || !conversationReady || (currentConversation && activePhases.has(currentConversation.status))) return;
  const executeProject = $('execute-project').checked;
  const repository = $('repository').value || null;
  if (executeProject && !repository) {
    notice('请选择本轮要操作的代码仓库。', 'error');
    return;
  }
  sending = true;
  const generation = conversationGeneration;
  const startedNewConversation = !currentConversation;
  updateSendState();
  try {
    if (!currentConversation) {
      const created = await api('/conversations', {method: 'POST', body: '{}'});
      if (generation !== conversationGeneration) { await refreshHistory(); return; }
      currentConversation = created;
    }
    const job = await api(`/conversations/${currentConversation.id}/messages`, {method: 'POST', body: JSON.stringify({
      prompt,
      context: $('context').value,
      images,
      use_knowledge: $('use-knowledge').checked,
      use_code: $('use-code').checked,
      repository,
      execute_project: executeProject,
      mcp_ids: executeProject ? [...$('project-mcp-options').querySelectorAll('input:checked')].map((input) => input.value) : [],
    })});
    if (generation !== conversationGeneration) { await refreshHistory(); return; }
    if (startedNewConversation) conversationDrafts.delete('new');
    $('prompt').value = '';
    $('context').value = '';
    $('execute-project').checked = false;
    images = [];
    $('image-names').textContent = '';
    $('file-count').textContent = '';
    $('clear-images').classList.add('hidden');
    await openConversation(job.conversation_id);
    scrollConversationEnd();
  } catch (error) {
    notice(error.message, 'error');
  } finally {
    sending = false;
    updateSendState();
  }
};
$('execute-project').onchange = updateSendState;

async function loadProjectMCPs() {
  const area = $('project-mcp-options');
  try {
    const {plugins} = await api('/project-mcps');
    area.innerHTML = plugins.length
      ? '<p class="small-note">可选 MCP（只读工具）</p>' + plugins.map((plugin) =>
        `<label class="check"><input type="checkbox" value="${esc(plugin.id)}">${esc(plugin.name)}</label>`).join('')
      : '';
    updateSendState();
  } catch (_) {
    area.textContent = '';
  }
}
$('prompt').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$('run-task').disabled) $('run-task').click();
  }
});
$('cancel-job').onclick = async () => {
  if (!currentJob) return;
  $('cancel-job').disabled = true;
  try {
    currentJob = await api(`/jobs/${currentJob.id}/cancel`, {method: 'POST', body: '{}'});
    renderJob();
    if (currentJob.conversation_id === currentConversation?.id) await refreshConversation();
    await refreshHistory();
  } catch (error) {
    notice(error.message, 'error');
  } finally {
    $('cancel-job').disabled = false;
  }
};
$('stop-reply').onclick = () => $('cancel-job').click();

function renderProjects() {
  const entries = projectCatalog.repositories || [];
  const selected = $('repository').value || projectCatalog.default_repository;
  const optionsHtml = entries.map((entry) => `<option value="${esc(entry.id)}">${esc(entry.id)} · ${esc(entry.branch || '本地目录')}</option>`).join('');
  $('repository').innerHTML = optionsHtml;
  $('tool-repository').innerHTML = optionsHtml;
  if (entries.some((entry) => entry.id === selected)) {
    $('repository').value = selected;
    $('tool-repository').value = selected;
  }
  $('project-hint').classList.toggle('hidden', !projectCatalog.enabled);
  $('use-code').checked = !!projectCatalog.enabled;
  $('project-summary').textContent = projectCatalog.enabled ? `${entries.length} 个工程 · ${number(projectCatalog.source_files)} 个代码与工程文件 · ${number(projectCatalog.symbols)} 个函数与类型` : '尚未建立项目代码索引';
  $('project-cards').innerHTML = entries.map((entry) => `<div class="route-card"><span>${esc(entry.id)}</span><strong>${esc(entry.branch || '本地目录')} · ${number(entry.source_files)} 个文件</strong><small>${esc(entry.path)}<br>${entry.git ? `${esc((entry.head || '').slice(0, 10))} · 索引时有 ${number(entry.changes?.length)} 项修改` : '未使用 Git'}</small></div>`).join('');
}

async function loadToolStatus() {
  try {
    const tools = await api('/tools');
    $('tool-status').innerHTML = Object.entries(tools).map(([name, tool]) => `<div class="route-card"><span>${esc(name)}</span><strong>${tool.available ? '已就绪' : '未找到'}</strong><small>${esc(tool.version || tool.path || '可做静态与工程配置检查')}</small></div>`).join('');
  } catch (error) {
    notice(error.message, 'error');
  }
}

async function projectAction(button, path, body) {
  button.disabled = true;
  lastToolResult = '';
  $('code-result').textContent = '正在读取本机工程';
  try {
    const repository = $('tool-repository').value;
    const result = await api(path, {method: 'POST', body: JSON.stringify({repository, ...body})});
    lastToolResult = JSON.stringify(result, null, 2);
    lastToolRepository = repository;
    $('code-result').textContent = lastToolResult;
  } catch (error) {
    $('code-result').textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
$('search-code').onclick = () => projectAction($('search-code'), '/projects/search', {query: $('code-query').value});
$('read-code').onclick = () => projectAction($('read-code'), '/projects/read', {path: $('code-path').value, start_line: 1, end_line: 100});
$('check-code').onclick = () => projectAction($('check-code'), '/projects/check', {paths: [$('code-path').value]});
$('profile-code').onclick = () => projectAction($('profile-code'), '/projects/profile', {path: $('code-path').value});
$('attach-code-result').onclick = () => {
  if (!lastToolResult) {
    notice('请先完成一次代码读取或工具检查。');
    return;
  }
  $('context').value = $('context').value + `\n\n本机工具结果（${lastToolRepository}）：\n` + lastToolResult;
  $('repository').value = lastToolRepository;
  document.querySelector('.context-box').open = true;
  view('tasks');
  notice('工具结果已加入任务材料，尚未调用模型。', 'success');
};
$('refresh-projects').onclick = async () => {
  $('refresh-projects').disabled = true;
  try {
    notice('正在重新读取本机代码，源文件保持不变');
    projectCatalog = await api('/projects/refresh', {method: 'POST', body: '{}'});
    renderProjects();
    notice('代码索引已更新。', 'success');
  } catch (error) {
    notice(error.message, 'error');
  } finally {
    $('refresh-projects').disabled = false;
  }
};

(async () => {
  try {
    config = await api('/config');
    restoreCatalogues();
    renderModels();
    renderPolicy();
    renderSummary();
    await refreshHistory();
    const [library, projects] = await Promise.all([api('/library'), api('/projects')]);
    projectCatalog = projects;
    renderProjects();
    void loadProjectMCPs();
    $('library-hint').classList.toggle('hidden', !library.enabled);
    $('use-knowledge').checked = !!library.enabled;
    $('library-name').textContent = `检索本机资料：${library.name} · ${number(library.source_files)} 个来源文件`;
  } catch (error) {
    notice(error.message, 'error');
  }
  const initial = location.hash.slice(1);
  view(['projects', 'models', 'policy'].includes(initial) ? initial : settingsOnly ? 'models' : 'tasks');
  const remembered = localStorage.getItem('agentscope_active_conversation');
  if (!settingsOnly && (!initial || initial === 'tasks') && remembered && conversationReady) {
    try { await openConversation(remembered); }
    catch (error) { localStorage.removeItem('agentscope_active_conversation'); notice(error.message, 'error'); }
  }
})();
