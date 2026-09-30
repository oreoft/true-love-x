/**
 * 提醒与定时任务：都归属当前机器人，到点从这个机器人发出去，表单里不选平台
 *
 * - 提醒：一次性给一个接收者发一句话（AI 在聊天里设的也在这里）
 * - 定时任务：把写好的任务（如摸鱼推送）推给一批接收者，单次或每天
 */

import { botApi, platformApi } from '../api.js';
import { $, $$, attempt, closeModal, confirmModal, esc, isoToLocalInput, localInputToIso, modal, platformName,
    shortTime, toast } from '../ui.js';

let tab = 'reminders';

export async function show(root, ctx) {
    const bot = ctx.bot;
    const api = botApi(bot.bot_id);
    const online = ctx.isOnline(bot);
    const [{ jobs: reminders }, { tasks }, options, { receivers }] = await Promise.all([
        api.reminders(), api.tasks(), platformApi.taskOptions(), api.receivers(),
    ]);
    const timezoneLabel = (value) => (options.timezones.find((tz) => tz.value === value) || {}).label || value;
    const describe = (schedule) => schedule.mode === 'daily'
        ? `每天 ${schedule.time}（${timezoneLabel(schedule.timezone)}）`
        : `单次 ${shortTime(schedule.run_at)}`;
    const reload = () => show(root, ctx);

    const reminderTable = () => reminders.length ? `
        <div class="table-wrap"><table>
            <thead><tr><th>接收者</th><th>@</th><th>内容</th><th>触发时间</th><th>操作</th></tr></thead>
            <tbody>${reminders.map((r, i) => `<tr>
                <td>${esc(r.receiver)}</td><td>${esc(r.at_user) || '—'}</td><td class="wrap">${esc(r.content)}</td>
                <td class="mono">${esc(shortTime(r.next_run_time))}</td>
                <td><div class="actions"><button class="btn sm" data-edit-reminder="${i}">修改</button>
                    <button class="btn sm danger" data-delete-reminder="${i}">删除</button></div></td>
            </tr>`).join('')}</tbody>
        </table></div>` : '<div class="empty">还没有提醒。在聊天里对 bot 说「提醒我…」，或者点右上角添加。</div>';

    const taskTable = () => tasks.length ? `
        <div class="table-wrap"><table>
            <thead><tr><th>任务</th><th>接收者</th><th>触发方式</th><th>下次执行</th><th>操作</th></tr></thead>
            <tbody>${tasks.map((t, i) => `<tr>
                <td class="mono">${esc(t.job_name)}</td><td>${t.receivers.map(esc).join('<br>') || '—'}</td>
                <td>${esc(describe(t.schedule))}</td><td class="mono">${esc(shortTime(t.next_run_time))}</td>
                <td><div class="actions"><button class="btn sm" data-run-task="${i}" ${online ? '' : 'disabled'}>立即执行</button>
                    <button class="btn sm" data-edit-task="${i}">修改</button>
                    <button class="btn sm danger" data-delete-task="${i}">删除</button></div></td>
            </tr>`).join('')}</tbody>
        </table></div>` : '<div class="empty">还没有定时任务，比如每天早上往群里推摸鱼日报。</div>';

    root.innerHTML = `
        ${online ? '' : offlineBanner(bot)}
        <div class="head"><h2 class="grow">提醒与定时任务</h2>
            <button class="btn primary" id="add">${tab === 'reminders' ? '添加提醒' : '添加定时任务'}</button></div>
        <div class="tabs" role="tablist">
            <button class="tab" role="tab" data-tab="reminders" aria-selected="${tab === 'reminders'}">提醒 ${reminders.length}</button>
            <button class="tab" role="tab" data-tab="tasks" aria-selected="${tab === 'tasks'}">定时任务 ${tasks.length}</button>
        </div>
        ${tab === 'reminders' ? reminderTable() : taskTable()}
        <div class="note">都归属当前 bot，到点从 ${esc(bot.name || bot.bot_id)}（${esc(platformName(bot.platform))}）发出。</div>`;

    $$('[data-tab]', root).forEach((el) => { el.onclick = () => { tab = el.dataset.tab; reload(); }; });
    $('#add', root).onclick = () => (tab === 'reminders'
        ? reminderForm(api, bot, receivers, null, reload)
        : taskForm(api, bot, receivers, options, null, reload));
    $$('[data-edit-reminder]', root).forEach((el) => {
        el.onclick = () => reminderForm(api, bot, receivers, reminders[el.dataset.editReminder], reload);
    });
    $$('[data-delete-reminder]', root).forEach((el) => {
        const r = reminders[el.dataset.deleteReminder];
        el.onclick = () => confirmModal('删除提醒', `确定要删除提醒「${r.content}」吗？`, async () => {
            if (await attempt(() => api.reminderDelete(r.job_id), '提醒已删除') !== undefined) reload();
        }, '删除');
    });
    $$('[data-edit-task]', root).forEach((el) => {
        el.onclick = () => taskForm(api, bot, receivers, options, tasks[el.dataset.editTask], reload);
    });
    $$('[data-delete-task]', root).forEach((el) => {
        const t = tasks[el.dataset.deleteTask];
        el.onclick = () => confirmModal('删除定时任务', `确定要删除「${t.job_name}」（${describe(t.schedule)}）吗？`, async () => {
            if (await attempt(() => api.taskDelete(t.task_id), '定时任务已删除') !== undefined) reload();
        }, '删除');
    });
    $$('[data-run-task]', root).forEach((el) => {
        const t = tasks[el.dataset.runTask];
        el.onclick = () => confirmModal('立即执行', `现在就把「${t.job_name}」推给 ${t.receivers.join('、') || '所有人'} 吗？不影响之后的定时。`,
            () => attempt(() => api.taskRun(t.task_id), '已开始执行，推送需要一会儿'));
    });
}

export function offlineBanner(bot) {
    const reason = bot.status && bot.status.reachable ? `${platformName(bot.platform)}离线` : 'base 连不上';
    const detail = bot.status && bot.status.message ? `（${esc(bot.status.message)}）` : '';
    return `<div class="banner">${esc(bot.name || bot.bot_id)} ${reason}${detail}。
        需要调用 base 的操作暂时不可用，已保存的数据照常查看和修改。</div>`;
}

/** 接收者：下拉候选来自监听列表和聊过的会话，也可以手动输入 */
const receiverList = (receivers) =>
    `<datalist id="receiverOptions">${receivers.map((name) => `<option value="${esc(name)}">`).join('')}</datalist>`;

function reminderForm(api, bot, receivers, reminder, reload) {
    modal(`<h3>${reminder ? '修改' : '添加'}提醒 · ${esc(bot.name || bot.bot_id)}</h3>
        <div class="field"><label for="fReceiver">接收者</label>
            <input id="fReceiver" list="receiverOptions" value="${esc(reminder ? reminder.receiver : '')}" placeholder="群名或好友昵称">
            ${receiverList(receivers)}
            <span class="hint">候选来自这个 bot 的监听列表和聊过的会话，也可以直接输入</span></div>
        <div class="field"><label for="fAt">@ 谁（可选）</label>
            <input id="fAt" value="${esc(reminder ? reminder.at_user : '')}" placeholder="群成员昵称"></div>
        <div class="field"><label for="fContent">提醒内容</label>
            <input id="fContent" value="${esc(reminder ? reminder.content : '')}" placeholder="如：去开会"></div>
        <div class="field"><label for="fTime">触发时间</label>
            <input id="fTime" type="datetime-local" value="${reminder ? isoToLocalInput(reminder.next_run_time) : ''}"></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        const form = {
            receiver: $('#fReceiver').value.trim(),
            at_user: $('#fAt').value.trim(),
            content: $('#fContent').value.trim(),
            target_time_iso: localInputToIso($('#fTime').value),
        };
        if (!form.receiver || !form.content || !form.target_time_iso) {
            toast('接收者、内容、触发时间不能为空', 'error');
            return;
        }
        e.target.disabled = true;
        const done = await attempt(() => (reminder
            ? api.reminderUpdate({ ...form, job_id: reminder.job_id })
            : api.reminderAdd(form)), reminder ? '提醒已修改' : '提醒已添加');
        e.target.disabled = false;
        if (done !== undefined) { closeModal(); reload(); }
    };
}

function taskForm(api, bot, receivers, options, task, reload) {
    const chosen = task ? [...task.receivers] : [];
    const schedule = task ? task.schedule : { mode: 'daily', time: '09:00', timezone: options.timezones[0]?.value };
    modal(`<h3>${task ? '修改' : '添加'}定时任务 · ${esc(bot.name || bot.bot_id)}</h3>
        <div class="field"><label for="fJob">任务</label>
            <select id="fJob">${options.jobs.map((job) => `<option ${task && task.job_name === job ? 'selected' : ''}>${esc(job)}</option>`).join('')}</select></div>
        <div class="field"><label for="fNewReceiver">接收者</label>
            <div class="receivers" id="chosen"></div>
            <div class="inline"><input id="fNewReceiver" list="receiverOptions" placeholder="群名或好友昵称">
                <button class="btn sm" id="addReceiver">添加</button></div>
            ${receiverList(receivers)}
            <span class="hint">按顺序逐个推送；不需要接收者的任务可以不填</span></div>
        <div class="field"><label for="fMode">触发方式</label>
            <select id="fMode"><option value="daily">每天</option><option value="once">单次</option></select></div>
        <div class="field" id="dailyFields"><label for="fDaily">每天的时间</label>
            <div class="inline"><input id="fDaily" type="time" value="${esc(schedule.time || '09:00')}">
                <select id="fTz">${options.timezones.map((tz) => `<option value="${esc(tz.value)}" ${tz.value === schedule.timezone ? 'selected' : ''}>${esc(tz.label)}</option>`).join('')}</select></div></div>
        <div class="field" id="onceFields"><label for="fRunAt">执行时间</label>
            <input id="fRunAt" type="datetime-local" value="${schedule.mode === 'once' ? isoToLocalInput(schedule.run_at) : ''}"></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);

    const renderChosen = () => {
        $('#chosen').innerHTML = chosen.map((name, i) =>
            `<span class="chip">${esc(name)}<button data-remove="${i}" aria-label="移除 ${esc(name)}">×</button></span>`).join('');
        $$('[data-remove]').forEach((el) => { el.onclick = () => { chosen.splice(Number(el.dataset.remove), 1); renderChosen(); }; });
    };
    const addReceiver = () => {
        const name = $('#fNewReceiver').value.trim();
        if (!name) return;
        if (chosen.includes(name)) { toast(`${name} 已经在列表里了`, 'error'); return; }
        chosen.push(name);
        $('#fNewReceiver').value = '';
        renderChosen();
    };
    const syncMode = () => {
        const daily = $('#fMode').value === 'daily';
        $('#dailyFields').hidden = !daily;
        $('#onceFields').hidden = daily;
    };
    $('#fMode').value = schedule.mode;
    $('#fMode').onchange = syncMode;
    $('#addReceiver').onclick = (e) => { e.preventDefault(); addReceiver(); };
    $('#fNewReceiver').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); addReceiver(); } };
    renderChosen();
    syncMode();

    $('#save').onclick = async (e) => {
        // 输入框里还没点"添加"的也算上
        const names = [...chosen, $('#fNewReceiver').value.trim()].filter(Boolean);
        const mode = $('#fMode').value;
        const form = {
            job_name: $('#fJob').value,
            receivers: [...new Set(names)],
            schedule: mode === 'once'
                ? { mode, run_at: localInputToIso($('#fRunAt').value) }
                : { mode, time: $('#fDaily').value, timezone: $('#fTz').value },
        };
        e.target.disabled = true;
        const done = await attempt(() => (task
            ? api.taskUpdate({ ...form, task_id: task.task_id })
            : api.taskAdd(form)), task ? '定时任务已修改' : '定时任务已添加');
        e.target.disabled = false;
        if (done !== undefined) { closeModal(); reload(); }
    };
}
