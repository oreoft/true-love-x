/**
 * 监听管理（微信专属）：看每个监听是否健康，测活、重置、移除、添加
 *
 * 这些操作都要调 base，base 离线时按钮变灰。
 */

import { botApi } from '../api.js';
import { $, $$, attempt, closeModal, confirmModal, esc, modal, toast } from '../ui.js';
import { offlineBanner } from './schedule.js';

const REASONS = { window_not_found: '窗口丢失', get_windows_failed: '取不到窗口', chat_info_failed: '窗口无响应' };

export async function show(root, ctx) {
    const bot = ctx.bot;
    const api = botApi(bot.bot_id);
    const online = ctx.isOnline(bot);
    const disabled = online ? '' : 'disabled';
    let status = { listeners: [], summary: { healthy: 0, unhealthy: 0 } };
    let statusError = '';
    try {
        status = await api.listenStatus();
    } catch (e) {
        statusError = e.message;
    }
    const { listeners, summary } = status;
    const reload = () => show(root, ctx);

    root.innerHTML = `
        ${online ? '' : offlineBanner(bot)}
        <div class="head"><h2 class="grow">监听管理</h2><span class="tag">微信专属</span>
            <button class="btn" id="refresh" ${disabled}>智能刷新</button>
            <button class="btn" id="resetAll" ${disabled}>全部重置</button></div>
        ${statusError ? `<div class="banner">没取到监听状态：${esc(statusError)}</div>` : `
        <div class="muted">${summary.healthy}/${listeners.length} 健康${summary.unhealthy ? `，<span class="warn-text">${summary.unhealthy} 个需要处理</span>` : ''}</div>`}
        <div class="listen-grid">
            ${listeners.map((item, i) => `
                <div class="listen">
                    <div class="row"><b>${esc(item.chat)}</b>
                        <span class="pill ${item.status === 'healthy' ? 'ok' : 'bad'}">${item.status === 'healthy' ? '健康' : esc(REASONS[item.reason] || item.reason || '异常')}</span></div>
                    <div class="actions">
                        <button class="btn sm" data-probe="${i}" ${disabled}>测活</button>
                        <button class="btn sm" data-reset="${i}" ${disabled}>重置</button>
                        <button class="btn sm danger" data-remove="${i}" ${disabled}>移除</button>
                    </div>
                </div>`).join('')}
            <button class="add" id="add" ${disabled}>＋ 添加监听</button>
        </div>`;

    $('#refresh', root).onclick = async (e) => {
        e.target.disabled = true;
        const result = await attempt(() => api.listenRefresh());
        if (result) toast(`已刷新：${result.success_count}/${result.total} 正常`);
        reload();
    };
    $('#resetAll', root).onclick = () => confirmModal('重置全部监听？',
        '会关掉所有子窗口再逐个重新监听，期间可能漏收几秒消息。', async () => {
            const result = await attempt(() => api.listenResetAll());
            if (result) toast(result.message);
            reload();
        }, '重置');
    $$('[data-probe]', root).forEach((el) => {
        const chat = listeners[el.dataset.probe].chat;
        el.onclick = async () => {
            const result = await attempt(() => api.listenProbe(chat));
            if (result) toast(`${chat} 能取到 ${(result.data || []).length} 条消息`);
        };
    });
    $$('[data-reset]', root).forEach((el) => {
        const chat = listeners[el.dataset.reset].chat;
        el.onclick = async () => {
            el.disabled = true;
            await attempt(() => api.listenReset(chat), `${chat} 已重置`);
            reload();
        };
    });
    $$('[data-remove]', root).forEach((el) => {
        const chat = listeners[el.dataset.remove].chat;
        el.onclick = () => confirmModal('移除这个监听？', `移除后不再收「${chat}」的消息。`, async () => {
            await attempt(() => api.listenRemove(chat), `${chat} 已移除`);
            reload();
        }, '移除');
    });
    $('#add', root).onclick = () => {
        modal(`<h3>添加监听 · ${esc(bot.name || bot.bot_id)}</h3>
            <div class="field"><label for="fName">群名或好友昵称</label><input id="fName" placeholder="和微信里显示的一致"></div>
            <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">添加</button></div>`);
        $('#save').onclick = async (e) => {
            const name = $('#fName').value.trim();
            if (!name) { toast('名称不能为空', 'error'); return; }
            e.target.disabled = true;
            const done = await attempt(() => api.listenAdd(name), `${name} 已添加`);
            e.target.disabled = false;
            if (done !== undefined) { closeModal(); reload(); }
        };
    };
}
