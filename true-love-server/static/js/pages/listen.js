/**
 * 监听管理（微信专属），分两个标签：
 * - 监听：看每个监听是否健康，测活、重置、移除、添加
 * - 微信设置：私聊轮询（和一键群免打扰）、自动通过好友申请、群回复方式
 *
 * 这些操作都要调 base，base 离线时按钮变灰；设置离线时也能改，base 下次连上微信时生效。
 */

import { botApi } from '../api.js';
import { $, $$, attempt, busy, closeModal, confirmModal, esc, modal, toast } from '../ui.js';
import { offlineBanner } from './schedule.js';

// 功能开关：[开关名, 名称, 说明]
const SWITCHES = [
    ['private_poll', '私聊轮询', '没开子窗口的私聊靠主窗口红点来收；群要设成免打扰，轮询才不会点开它们'],
    ['auto_accept_friends', '自动通过好友申请', '每两分钟看一次新朋友，有申请就通过，并告诉管理员通过了谁'],
];
// 群回复方式：[值, 名称]，勾几个就在其中随机挑
const REPLY_STYLES = [['at', '@ 回复'], ['tickle', '拍一拍'], ['quote', '引用回复']];
const REASONS = { window_not_found: '窗口丢失', get_windows_failed: '取不到窗口', chat_info_failed: '窗口无响应' };

// 当前标签；切标签只重画，不重新拉数据
let tab = 'listen';

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
    let settings = {};
    let settingsError = '';
    try {
        settings = await api.listenSettings();
    } catch (e) {
        settingsError = e.message;
    }
    const dimmed = async (work) => {
        root.style.opacity = '0.5';
        root.style.pointerEvents = 'none';
        try {
            await work();
        } finally {
            root.style.opacity = '';
            root.style.pointerEvents = '';
        }
    };
    // 监听状态要 base 逐个检查窗口，监听多时要等好几秒，所以改设置后只重新拉设置
    const reload = () => dimmed(() => show(root, ctx));
    const reloadSettings = () => dimmed(async () => {
        try {
            settings = await api.listenSettings();
            settingsError = '';
        } catch (e) {
            settingsError = e.message;
        }
        draw();
    });

    const listenTab = () => `
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

    const settingsTab = () => settingsError ? `<div class="banner">没取到微信设置：${esc(settingsError)}</div>` : `
        ${SWITCHES.map(([key, label, hint]) => `
        <div class="listen-settings">
            <span>${label} <span class="pill ${settings[key] ? 'ok' : ''}">${settings[key] ? '开' : '关'}</span></span>
            <span class="muted grow">${hint}</span>
            <button class="btn sm" data-switch="${key}">${settings[key] ? '关闭' : '打开'}</button>
            ${key === 'private_poll' ? `<button class="btn sm" id="muteGroups" ${disabled}>一键群免打扰</button>` : ''}
        </div>`).join('')}
        <div class="listen-settings">
            <span>群回复方式</span>
            ${REPLY_STYLES.map(([value, label]) => `<label><input type="checkbox" data-reply="${value}"
                ${(settings.group_reply || ['at']).includes(value) ? 'checked' : ''}> ${label}</label>`).join('')}
            <span class="muted grow">勾几个就等概率随机用其中一种；拍一拍后直接发回复，引用不了时改用 @。私聊总是直接回复</span>
            <button class="btn sm" id="saveReply">保存</button>
        </div>`;

    // 操作都要等 base 去微信里点，期间按钮显示"…中"、点不了；做完重新拉状态时页面变灰
    const bindListen = () => {
        $('#refresh', root).onclick = async (e) => {
            const result = await busy(e.target, '刷新中…', () => attempt(() => api.listenRefresh()));
            if (result) toast(`已刷新：${result.success_count}/${result.total} 正常`);
            reload();
        };
        $('#resetAll', root).onclick = (e) => confirmModal('重置全部监听？',
            '会关掉所有子窗口再逐个重新监听，期间可能漏收几秒消息。', async () => {
                const result = await busy(e.target, '重置中…', () => attempt(() => api.listenResetAll()));
                if (result) toast(result.message);
                reload();
            }, '重置');
        $$('[data-probe]', root).forEach((el) => {
            const chat = listeners[el.dataset.probe].chat;
            el.onclick = async () => {
                const result = await busy(el, '测活中…', () => attempt(() => api.listenProbe(chat)));
                if (result) toast(`${chat} 能取到 ${(result.data || []).length} 条消息`);
            };
        });
        $$('[data-reset]', root).forEach((el) => {
            const chat = listeners[el.dataset.reset].chat;
            el.onclick = async () => {
                await busy(el, '重置中…', () => attempt(() => api.listenReset(chat), `${chat} 已重置`));
                reload();
            };
        });
        $$('[data-remove]', root).forEach((el) => {
            const chat = listeners[el.dataset.remove].chat;
            el.onclick = () => confirmModal('移除这个监听？', `移除后不再收「${chat}」的消息。`, async () => {
                await busy(el, '移除中…', () => attempt(() => api.listenRemove(chat), `${chat} 已移除`));
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
                const done = await busy(e.target, '添加中…', () => attempt(() => api.listenAdd(name), `${name} 已添加`));
                if (done !== undefined) { closeModal(); reload(); }
            };
        };
    };

    const bindSettings = () => {
        $$('[data-switch]', root).forEach((el) => {
            const [key, label] = SWITCHES.find(([name]) => name === el.dataset.switch);
            el.onclick = async () => {
                const result = await busy(el, '保存中…', () => attempt(() => api.listenSaveSettings({ [key]: !settings[key] })));
                if (result) {
                    const state = result.settings[key] ? '打开' : '关闭';
                    toast(result.applied ? `${label}已${state}` : `${label}已${state}，base 下次连上微信时生效`);
                }
                reloadSettings();
            };
        });
        $('#saveReply', root).onclick = async (e) => {
            const styles = $$('[data-reply]', root).filter((el) => el.checked).map((el) => el.dataset.reply);
            if (!styles.length) { toast('至少勾一种回复方式', 'error'); return; }
            const result = await busy(e.target, '保存中…', () => attempt(() => api.listenSaveSettings({ group_reply: styles })));
            if (result) toast(result.applied ? '群回复方式已保存' : '群回复方式已保存，base 下次连上微信时生效');
            reloadSettings();
        };
        $('#muteGroups', root).onclick = (e) => confirmModal('把会话列表里的群设成免打扰？',
            'base 会逐个点开会话列表里没免打扰的会话，是群就设成免打扰；不在会话列表里的不活跃群不管，'
            + '有未读的会话先跳过。开了子窗口的群照常收消息。', async () => {
                const result = await busy(e.target, '设置中…', () => attempt(() => api.listenMuteAllGroups()));
                if (result) {
                    const failed = result.failed.map((item) => `${item.chat}（${item.reason}）`).join('、');
                    const unread = (result.unread || []).length;
                    toast(`新设 ${result.muted.length} 个群，原本就免打扰 ${result.already.length} 个会话`
                        + (unread ? `，${unread} 个有未读先跳过` : '')
                        + (failed ? `，失败：${failed}` : ''), failed ? 'error' : undefined);
                }
            }, '设置');
    };

    const draw = () => {
        root.innerHTML = `
            ${online ? '' : offlineBanner(bot)}
            <div class="head"><h2 class="grow">监听管理</h2><span class="tag">微信专属</span>
                ${tab === 'listen' ? `<button class="btn" id="refresh" ${disabled}>智能刷新</button>
                <button class="btn" id="resetAll" ${disabled}>全部重置</button>` : ''}</div>
            <div class="tabs" role="tablist">
                <button class="tab" role="tab" data-tab="listen" aria-selected="${tab === 'listen'}">监听 ${listeners.length}</button>
                <button class="tab" role="tab" data-tab="settings" aria-selected="${tab === 'settings'}">微信设置</button>
            </div>
            ${tab === 'listen' ? listenTab() : settingsTab()}`;
        $$('[data-tab]', root).forEach((el) => { el.onclick = () => { tab = el.dataset.tab; draw(); }; });
        if (tab === 'listen') bindListen();
        else if (!settingsError) bindSettings();
    };

    draw();
}
