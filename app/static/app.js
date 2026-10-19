const $ = (id) => document.getElementById(id);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = { users: [], userId: null, runId: null, runs: [], status: null, events: null, dirty: false, detailKey: '', polling: false };
const statuses = { queued: '等待生成', running: '正在生成', completed: '已完成', failed: '生成失败' };
const deliveryLabels = { pending: '等待推送', sending: '正在推送', sent: '已推送', failed: '推送失败', unknown: '推送状态待核对', not_sent: '未推送' };
let toastTimer;

function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5000); }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...options.headers } });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(x => x.msg).join('；') : data.detail;
    throw new Error(detail || '请求失败，请稍后重试');
  }
  return data;
}
function currentUser() { return state.users.find(x => x.id === state.userId); }
function dateText(iso, options = {}) {
  return new Date(iso).toLocaleString('zh-CN', { timeZone: currentUser()?.timezone || 'Asia/Shanghai', month: '2-digit', day: '2-digit', ...options });
}
function terms(value) { return [...new Set(value.split(/[,，、\n]/).map(x => x.trim()).filter(Boolean))]; }
function articleDates(item) {
  const first = item.first_published_at;
  if (item.source_id === 'arxiv') {
    const announced = item.announced_at || item.feed_entry_at;
    const parts = [`RSS 公告 ${announced ? dateText(announced, {hour:'2-digit',minute:'2-digit'}) : '未知'}`,
      `首次提交 ${first ? first.slice(0,10) : '未知'}`];
    if (item.updated_at) parts.push(`修订 ${item.updated_at.slice(0,10)}`);
    return parts.join(' · ');
  }
  const date = first || item.published_at;
  return date ? `来源发布 ${dateText(date, {hour:'2-digit',minute:'2-digit'})}` : '发布时间未知';
}
function syncEmail() { $('email-field').hidden = $('channel').value !== 'email'; $('email').required = $('channel').value === 'email'; }

function populateUsers() {
  $('user-select').innerHTML = '<option value="">新建订阅</option>' + state.users.map(u => `<option value="${u.id}">${escapeHTML(u.name)}</option>`).join('');
  $('user-select').value = state.userId || '';
}
function fillForm(user) {
  const value = user || { name: '', role: '工程师', topics: ['AI 编程','Agent','开源模型'], keywords: [], excludes: ['融资'], delivery_time: '08:00', max_items: 5, timezone: 'Asia/Shanghai', channel: 'inbox', email: '', enabled: false };
  for (const key of ['name','role','delivery_time','max_items','timezone','channel','email']) $(key).value = value[key];
  if (!$('timezone').value) { const option = new Option(value.timezone, value.timezone); $('timezone').add(option); $('timezone').value = value.timezone; }
  for (const key of ['topics','keywords','excludes']) $(key).value = value[key].join('、');
  $('enabled').checked = value.enabled;
  state.dirty = false; $('save-message').textContent = ''; syncEmail(); updateSchedule();
}
function updateSchedule() {
  const user = currentUser();
  $('schedule-text').textContent = !user ? '保存订阅后，即可开始生成简报' :
    user.enabled && state.status.scheduler_enabled ? `每天 ${user.delivery_time} · ${user.timezone} · 最多 ${user.max_items} 条` : '自动简报已暂停 · 仍可手动生成';
  const busy = state.runs.some(x => ['running','queued'].includes(x.status));
  $('generate-button').disabled = !user || busy || !state.status.model_ready;
  $('generate-button').textContent = busy ? '正在生成…' : '✦ 立即生成';
}
function emptyPanel() {
  $('brief-panel').innerHTML = '<div class="empty-state"><span class="empty-icon">☷</span><h3>把你关心的，留在这里。</h3><p>设置左侧订阅，再点击「立即生成」。<br>每条新闻都有摘要、推荐理由和来源。</p></div>';
  $('trace-count').textContent = '0 次工具调用'; $('traces').innerHTML = '<p class="muted">还没有执行记录。</p>'; $('trace-mode').textContent = '';
}
async function selectUser(id) {
  state.events?.close(); state.events = null; state.userId = id || null; state.runId = null; state.runs = []; state.detailKey = '';
  localStorage.setItem('daily-ai-user', id || ''); populateUsers(); fillForm(currentUser());
  $('history').innerHTML = ''; $('brief-count').textContent = '0'; emptyPanel();
  if (state.userId) { connectEvents(state.userId); await refresh(); }
}

function renderHistory() {
  $('brief-count').textContent = state.runs.filter(r => r.status === 'completed').length;
  $('history').innerHTML = state.runs.map(run => `<button type="button" class="history-item ${run.id === state.runId ? 'active' : ''}" data-run="${run.id}">
    <time>${escapeHTML(dateText(run.created_at))}</time><small>${escapeHTML(dateText(run.created_at, {hour:'2-digit', minute:'2-digit'}))} · ${run.trigger === 'scheduled' ? '自动' : '手动'}</small>
    <span class="history-status">${statuses[run.status]}${run.brief ? ` · ${run.brief.items.length} 条` : ''}</span>
  </button>`).join('');
  for (const button of $('history').querySelectorAll('[data-run]')) button.onclick = async () => { state.runId = button.dataset.run; state.detailKey = ''; renderHistory(); await loadDetail(); };
}
async function refresh(preferRun = null) {
  const id = state.userId; if (!id) return;
  const runs = await api(`/api/users/${id}/runs`); if (id !== state.userId) return;
  state.runs = runs;
  if (preferRun || !state.runId) state.runId = preferRun || runs[0]?.id || null;
  renderHistory(); updateSchedule(); if (state.runId) await loadDetail();
}
async function loadDetail() {
  const id = state.runId; const userId = state.userId; if (!id) return;
  const run = await api(`/api/runs/${id}`);
  if (id !== state.runId || userId !== state.userId) return;
  const key = JSON.stringify(run); if (key === state.detailKey) return;
  state.detailKey = key; renderBrief(run); renderTraces(run);
}
function renderBrief(run) {
  if (['queued','running'].includes(run.status)) {
    $('brief-panel').innerHTML = `<div class="empty-state"><span class="empty-icon">✦</span><h3>${statuses[run.status]}</h3><p>${run.mode === 'demo' ? '正在使用本地虚构样例演示完整流程。' : 'Agent 正在根据你的偏好获取、阅读和筛选新闻。'}<br>下方可以查看已发生的工具调用。</p><div class="loading-bar"></div></div>`; return;
  }
  if (run.status === 'failed') {
    $('brief-panel').innerHTML = `<div class="error-box"><span class="badge">生成失败</span><h3>这次简报没有生成成功</h3><p>${escapeHTML(run.error)}</p><p class="muted">失败不代表今天没有相关新闻。可查看执行记录，排查后点击「立即生成」重试。</p></div>`; return;
  }
  const brief = run.brief;
  if (!brief) { emptyPanel(); return; }
  const warnings = [...brief.warnings];
  if (brief.demo) warnings.unshift('演示样例：以下内容为虚构数据，不是今日真实新闻。');
  if (run.delivery_error) warnings.push(run.delivery_error);
  const items = brief.items.map((item, index) => `<section class="article-card">
    <div class="article-top"><span class="article-number">${String(index+1).padStart(2,'0')}</span><span class="topic-tag">${escapeHTML(item.topic)}</span></div>
    <h3>${escapeHTML(item.title)}</h3><p class="article-summary">${escapeHTML(item.summary)}</p>
    <div class="why"><b>与你的关系</b>　${escapeHTML(item.why_it_matters)}</div>
    <div class="article-source"><span>${escapeHTML(item.source)} · ${escapeHTML(articleDates(item))}${item.evidence_kind === 'feed_summary' ? ' · 依据 RSS 摘要' : item.evidence_kind === 'abstract' ? ' · 依据论文摘要' : ''}</span>${/^https:\/\//.test(item.url) ? `<a href="${escapeHTML(item.url)}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>` : '<span>虚构样例</span>'}</div>
  </section>`).join('');
  const sources = brief.sources_checked.map(id => state.status.sources.find(x => x.id === id)?.name || id).join('、');
  $('brief-panel').innerHTML = `<div class="brief-date">${escapeHTML(dateText(run.created_at, {year:'numeric',hour:'2-digit',minute:'2-digit'}))} · ${deliveryLabels[run.delivery_status]}</div>
    <h2 class="brief-title">${escapeHTML(brief.title)}</h2><p class="brief-overview">${escapeHTML(brief.overview)}</p>
    <div class="brief-meta"><span>${brief.items.length} 条精选</span><span>约 ${Math.max(1, Math.ceil(brief.items.length/2))} 分钟</span>${brief.summary_review?.status === 'checked' ? '<span>摘要已复核</span>' : ''}${brief.preferences.topics.map(t=>`<span>${escapeHTML(t)}</span>`).join('')}</div>
    ${warnings.map(w=>`<div class="warning">${escapeHTML(w)}</div>`).join('')}
    ${['failed','unknown'].includes(run.delivery_status) ? '<button class="primary retry-button" id="retry-delivery" type="button">重试推送</button>' : ''}
    ${items || '<div class="empty-state"><h3>今天暂时没有新的匹配内容</h3><p>可能没有符合偏好的更新，或相关内容已经推送。<br>有来源失败时，覆盖范围也会受到影响。</p></div>'}
    <div class="brief-end">${brief.demo ? '样例仅用于演示。真实模式会从 RSS 获取新闻。' : `本次成功检查：${escapeHTML(sources)}。`} · 历史简报保留生成时的订阅偏好。</div>`;
  if ($('retry-delivery')) $('retry-delivery').onclick = async () => {
    $('retry-delivery').disabled = true;
    try { await api(`/api/runs/${run.id}/retry-delivery`, {method:'POST'}); toast('已提交推送重试'); await refresh(); } catch (e) { toast(e.message); $('retry-delivery').disabled = false; }
  };
}
function renderTraces(run) {
  $('trace-count').textContent = `${run.traces.length} 次工具调用`;
  $('trace-mode').textContent = run.mode === 'demo' ? '离线演示器按固定脚本调用真实工具，不代表 LLM 决策。切换真实模式后由 LangChain create_agent 中的模型自主选择工具。' : '以下为实际工具名称、入参、结果与耗时；不展示模型内部推理。';
  $('traces').innerHTML = run.traces.map((trace, i) => `<details class="trace-row"><summary>${i+1}. ${escapeHTML(trace.tool)} <small>${trace.status === 'ok' ? '✓' : '错误'} · ${trace.duration_ms} ms</small></summary><pre>参数\n${escapeHTML(JSON.stringify(trace.arguments,null,2))}</pre><pre>结果\n${escapeHTML(trace.result)}</pre></details>`).join('') || '<p class="muted">尚未发生工具调用。</p>';
}
function connectEvents(userId) {
  const key = `daily-ai-cursor-${userId}`; const cursor = Number(localStorage.getItem(key) || 0);
  const events = new EventSource(`/api/users/${userId}/events?after=${cursor}`); state.events = events;
  events.addEventListener('brief', async event => {
    if (state.userId !== userId) return;
    const item = JSON.parse(event.data); localStorage.setItem(key, item.id);
    toast('你的 AI 简报已送达收件箱');
    if ('Notification' in window && Notification.permission === 'granted' && document.hidden) new Notification(item.title, { body:'你的个性化 AI 简报已准备好。', tag:item.run_id });
    try { await refresh(); } catch (e) { toast(e.message); }
  });
}

$('subscription-form').addEventListener('input', () => { state.dirty = true; $('save-message').textContent = '修改后请保存，以便下次生成使用新偏好。'; });
$('channel').addEventListener('change', syncEmail);
$('subscription-form').onsubmit = async event => {
  event.preventDefault(); $('save-button').disabled = true; $('save-message').classList.remove('error');
  const profile = Object.fromEntries(['name','role','email','delivery_time','timezone','channel'].map(k => [k,$(k).value]));
  for (const key of ['topics','keywords','excludes']) profile[key] = terms($(key).value);
  profile.max_items = Number($('max_items').value); profile.enabled = $('enabled').checked;
  try {
    const user = await api(state.userId ? `/api/users/${state.userId}` : '/api/users', {method:state.userId?'PUT':'POST',body:JSON.stringify(profile)});
    state.users = await api('/api/users'); await selectUser(user.id); $('save-message').textContent = '订阅已保存'; toast('订阅已保存');
  } catch(e) { $('save-message').classList.add('error'); $('save-message').textContent = e.message; }
  finally { $('save-button').disabled = false; }
};
$('new-user').onclick = () => selectUser(null).catch(e=>toast(e.message));
$('user-select').onchange = e => selectUser(e.target.value).catch(e=>toast(e.message));
$('generate-button').onclick = async () => {
  if (state.dirty) { toast('请先保存修改后的订阅'); return; }
  const id = state.userId; if (!id) return; $('generate-button').disabled = true;
  try { const run = await api(`/api/users/${id}/generate`,{method:'POST'}); if (id === state.userId) await refresh(run.id); }
  catch(e) { toast(e.message); updateSchedule(); }
};
for (const button of document.querySelectorAll('[data-topic]')) button.onclick = () => {
  $('topics').value = [...new Set([...terms($('topics').value),button.dataset.topic])].join('、');
  $('topics').dispatchEvent(new Event('input',{bubbles:true}));
};
$('notifications-button').onclick = async () => {
  if (!('Notification' in window)) { toast('当前浏览器不支持通知，站内推送仍然可用。'); return; }
  const permission = await Notification.requestPermission(); toast(permission === 'granted' ? '浏览器通知已开启（页面需保持打开）' : '浏览器通知未开启，简报仍会送达站内收件箱');
};

async function init() {
  $('today').innerHTML = `<strong>${new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',timeZone:'Asia/Shanghai'})}</strong>${new Date().toLocaleDateString('zh-CN',{weekday:'long',timeZone:'Asia/Shanghai'})} · 每天都有新发现`;
  state.status = await api('/api/status'); state.users = await api('/api/users');
  const demo = state.status.mode === 'demo';
  $('mode-badge').textContent = demo ? '● 演示模式' : '● 真实模型'; $('mode-badge').classList.toggle('demo', demo);
  $('mode-note').textContent = demo ? '当前为离线演示：使用虚构样例，不调用大模型，也不抓取实时新闻。配置 .env 中的 APP_MODE=live 与模型 API Key 后即可接入真实新闻。' : state.status.model_ready ? '真实模式：按你的订阅获取过去 24 小时的新闻。每条内容保留来源，工具调用过程可查看。' : '真实模式尚未配置 OPENAI_API_KEY。请在项目 .env 中配置后重启服务。';
  $('channel').querySelector('[value="email"]').disabled = !state.status.smtp_ready;
  if (!state.status.smtp_ready) $('channel').querySelector('[value="email"]').textContent = '邮件（需先配置 SMTP）';
  $('source-list').innerHTML = state.status.sources.map(s=>`<a href="${escapeHTML(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(s.name)} ↗</a>`).join('');
  const saved = localStorage.getItem('daily-ai-user'); await selectUser(state.users.find(x=>x.id===saved)?.id || state.users[0]?.id || null);
  setInterval(async () => { if (!state.userId || state.polling) return; state.polling=true; try { await refresh(); } catch { /* EventSource reconnects; keep readable history during transient outages. */ } finally { state.polling=false; } }, 4000);
}
init().catch(e => { $('mode-note').textContent = `连接失败：${e.message}。请确认本地服务正在运行。`; $('generate-button').disabled = true; });
