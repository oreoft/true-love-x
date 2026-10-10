/**
 * 微信机器人的聊天页，长得像电脑版微信：最左边一列切换「聊天 / 好友 / 记录」
 *
 * - 聊天：会话列表和消息都是现场从机器人的微信里读的。右键头像拍一拍、@ 对方，右键消息引用；
 *   图片、视频、文件和引用里的图片点一下才加载（要在微信里点开下载，占用界面）；
 *   「加载更早」翻出来的消息在微信里已经滚出窗口了，只能看，不能拍一拍、引用、加载（actionable 是 false）；
 *   底下的输入框发文字和文件，群里可以 @ 人。不自动刷新：会话列表和消息点刷新按钮才重读，发完消息自动重读一次。
 * - 好友：新的朋友（通过时可以填备注）、加好友、改好友备注。标签不做：SDK 改标签在 ser 上找不到标签控件。
 * - 记录：server 库里存的聊天记录，可以按关键词搜，点名字看 AI 记下的画像（就是原来的聊天记录页）。
 *
 * 每个操作都要占用机器人的微信窗口，期间机器人收发消息会排队，所以不做频繁的自动刷新。
 */

import { botApi } from '../api.js';
import { $, $$, attempt, busy, esc, modal, closeModal, toast } from '../ui.js';
import * as records from './messages.js';

const TABS = [
    { key: 'chat', label: '聊天', icon: '💬' },
    { key: 'friends', label: '好友', icon: '👤' },
    { key: 'records', label: '记录', icon: '🗂' },
];
// 「加载更早」每次多往上翻这么多条；ser 实测翻 15 条要 9~12 秒
const HISTORY_STEP = 20;
const TYPE_NAMES = {
    image: '图片', voice: '语音', video: '视频', file: '文件', link: '链接', location: '位置',
    emotion: '表情', merge: '聊天记录', personal_card: '名片', note: '笔记',
};
const LAST_TAB = 'tl-admin.wx-tab';
// 点了才加载的消息类型；引用的内容是这些字时，引用的是图片或视频
const MEDIA_TYPES = new Set(['image', 'video', 'file']);
const QUOTED_MEDIA = new Set(['图片', '[图片]', '视频', '[视频]']);

export async function show(root, ctx) {
    const api = botApi(ctx.bot.bot_id);
    root.innerHTML = `
        <div class="wx">
            <div class="wx-rail" role="tablist" aria-label="功能">${TABS.map((tab) => `
                <button class="wx-tab" role="tab" data-tab="${tab.key}" title="${tab.label}">
                    <span aria-hidden="true">${tab.icon}</span><small>${tab.label}</small></button>`).join('')}
            </div>
            <div class="wx-pane" id="wxPane"></div>
        </div>`;
    const pane = $('#wxPane', root);
    let cleanup = null;

    const open = async (key) => {
        if (cleanup) { cleanup(); cleanup = null; }
        $$('.wx-tab', root).forEach((el) => el.setAttribute('aria-selected', String(el.dataset.tab === key)));
        try { localStorage.setItem(LAST_TAB, key); } catch (e) { /* 浏览器不让存就算了 */ }
        // 每个功能一个新容器，切走后晚到的结果写不进来
        const box = document.createElement('div');
        box.className = `wx-body tab-${key}`;
        pane.replaceChildren(box);
        if (key === 'chat') cleanup = chatTab(box, api, ctx);
        else if (key === 'friends') friendsTab(box, api);
        else {
            box.innerHTML = '<div class="loader">加载中…</div>';
            try {
                await records.show(box, ctx);
            } catch (e) {
                box.innerHTML = `<div class="banner">${esc(e.message)}</div>`;
            }
        }
    };
    $$('.wx-tab', root).forEach((el) => { el.onclick = () => open(el.dataset.tab); });
    let first = 'chat';
    try { first = TABS.some((t) => t.key === localStorage.getItem(LAST_TAB)) ? localStorage.getItem(LAST_TAB) : 'chat'; } catch (e) { /* 用默认 */ }
    await open(first);
    return () => { if (cleanup) cleanup(); };
}

// ==================== 聊天 ====================

function chatTab(box, api, ctx) {
    const state = {
        sessions: [], filter: '', current: null, chat: null, messages: [], history: 0,
        // 点开过的图片、视频、文件：{消息 id 或 "q:"+消息 id: {url, name, type}}，换会话时清空
        media: {},
        at: [], quote: null, seq: 0, loading: false, closed: false,
    };
    box.innerHTML = `
        <div class="wx-list">
            <div class="wx-search">
                <input id="wxFilter" placeholder="搜索会话" aria-label="搜索会话">
                <button class="btn sm" id="wxReloadSessions" title="刷新会话列表">↻</button>
            </div>
            <div class="wx-sessions" id="wxSessions"><div class="loader">读取会话列表…</div></div>
        </div>
        <div class="wx-chat" id="wxChat"><div class="wx-empty">选一个会话开始聊天</div></div>`;
    const list = $('#wxSessions', box);
    const chatBox = $('#wxChat', box);

    // ---------- 会话列表 ----------

    const renderSessions = () => {
        const keyword = state.filter.toLowerCase();
        const shown = state.sessions.filter((s) => !keyword || s.name.toLowerCase().includes(keyword));
        list.innerHTML = shown.length ? shown.map((s) => `
            <button class="wx-session" data-name="${esc(s.name)}" aria-current="${s.name === state.current}">
                ${avatar(s.name)}
                <span class="wx-session-main">
                    <span class="wx-session-top"><b>${esc(s.name)}</b><small>${esc(sessionTime(s.time))}</small></span>
                    <span class="wx-session-bottom"><span class="wx-preview">${esc(s.content || '')}</span>
                        ${s.listening ? '<span class="tag" title="机器人在监听这个会话">监听</span>' : ''}
                        ${s.ismute ? '<span class="wx-mute" title="消息免打扰">🔕</span>' : ''}</span>
                </span>
                ${s.new_count ? `<span class="wx-badge ${s.ismute ? 'muted' : ''}">${s.new_count > 99 ? '99+' : s.new_count}</span>` : ''}
            </button>`).join('') : `<div class="loader">${state.sessions.length ? '没有匹配的会话' : '会话列表是空的'}</div>`;
    };

    const loadSessions = async ({ quiet = false } = {}) => {
        let data;
        try {
            data = await api.wxSessions();
        } catch (e) {
            if (!quiet) list.innerHTML = `<div class="banner">${esc(e.message)}</div>`;
            return;
        }
        if (state.closed) return;
        state.sessions = data.sessions;
        renderSessions();
    };

    // ---------- 消息 ----------

    const renderChat = () => {
        const session = state.sessions.find((s) => s.name === state.current) || {};
        const chat = state.chat || {};
        const isGroup = chat.chat_type === 'group';
        chatBox.innerHTML = `
            <div class="wx-head">
                <b>${esc(state.current)}</b>${chat.member_count ? `<span class="muted">(${chat.member_count})</span>` : ''}
                ${session.listening ? '<span class="tag">监听中</span>' : ''}
                <span class="grow"></span>
                ${chat.chat_type === 'friend' && state.current !== '文件传输助手' ? '<button class="btn sm" id="wxEditFriend">改备注</button>' : ''}
                <button class="btn sm" id="wxOlder">加载更早</button>
                <button class="btn sm" id="wxReload" title="重新读消息">↻</button>
            </div>
            <div class="wx-msgs" id="wxMsgs"><div class="loader">读取消息…</div></div>
            <div class="wx-compose">
                <div class="wx-extra" id="wxExtra" hidden></div>
                <div class="wx-tools">
                    <label class="wx-tool" title="发文件或图片">📎<input type="file" id="wxFile" hidden></label>
                    ${isGroup ? '<button class="wx-tool" id="wxAtBtn" title="@ 群成员">@</button>' : ''}
                    <span class="grow"></span>
                    <span class="muted wx-hint">Enter 发送，Shift+Enter 换行</span>
                </div>
                <textarea id="wxInput" rows="3" aria-label="输入消息"></textarea>
                <div class="wx-send-row"><button class="btn primary" id="wxSend">发送</button></div>
            </div>`;
        renderMessages();
        renderExtra();
        bindChat();
    };

    const renderMessages = (keepScroll = false) => {
        const area = $('#wxMsgs', chatBox);
        if (!area) return;
        if (state.loading && !state.messages.length) {
            area.innerHTML = '<div class="loader">读取消息…</div>';
            return;
        }
        const previousHeight = area.scrollHeight;
        const previousTop = area.scrollTop;
        area.innerHTML = state.messages.length
            ? state.messages.map(renderMessage).join('')
            : '<div class="loader">这个会话里没有能看到的消息</div>';
        area.scrollTop = keepScroll ? area.scrollHeight - previousHeight + previousTop : area.scrollHeight;
    };

    const renderMessage = (msg, index) => {
        if (msg.kind === 'time') return `<div class="wx-time">${esc(msg.content)}</div>`;
        if (msg.kind === 'system') return `<div class="wx-system">${esc(msg.content)}</div>`;
        const self = msg.kind === 'self';
        const showName = !self && (state.chat || {}).chat_type === 'group';
        const label = TYPE_NAMES[msg.type];
        // 图片、文件这类消息的内容开头就是「图片」「文件」，标签里已经有了
        const content = label && (msg.content || '').startsWith(label) ? msg.content.slice(label.length).trim() : msg.content || '';
        let body = msg.type === 'text' || !label
            ? esc(content)
            : `<span class="wx-type">[${label}]</span> ${esc(content)}`;
        if (MEDIA_TYPES.has(msg.type)) body = mediaBody(msg, index, false, body);
        if (msg.quote) {
            const quoted = esc(`${msg.quote.sender ? `${msg.quote.sender}：` : ''}${msg.quote.content}`);
            body += `<div class="wx-quote">${QUOTED_MEDIA.has(msg.quote.content)
                ? mediaBody(msg, index, true, quoted) : quoted}</div>`;
        }
        return `
            <div class="wx-msg ${self ? 'self' : ''}" data-index="${index}">
                ${avatar(self ? (ctx.bot.status && ctx.bot.status.self_name) || ctx.bot.name || '我' : msg.sender, 'data-avatar')}
                <div class="wx-msg-main">
                    ${showName ? `<div class="wx-sender">${esc(msg.sender)}</div>` : ''}
                    <div class="wx-bubble" data-bubble>${body}</div>
                </div>
            </div>`;
    };

    /** 图片、视频、文件：加载过的直接显示，没加载过的显示一个「点击加载」 */
    const mediaBody = (msg, index, quoted, text) => {
        const loaded = state.media[(quoted ? 'q:' : '') + msg.id];
        if (!loaded && msg.actionable === false) return text;
        if (!loaded) {
            return `${text} <button class="wx-load" data-load="${index}" data-quoted="${quoted}">点击加载</button>`;
        }
        if (loaded.type.startsWith('image/')) return `<img class="wx-media" src="${loaded.url}" alt="${esc(loaded.name)}">`;
        if (loaded.type.startsWith('video/')) return `<video class="wx-media" src="${loaded.url}" controls></video>`;
        return `${text} <a class="wx-load" href="${loaded.url}" download="${esc(loaded.name)}">下载 ${esc(loaded.name)}</a>`;
    };

    // 一次只加载一个，点了好几个就排队
    let mediaQueue = Promise.resolve();
    const loadMedia = async (button) => {
        const msg = state.messages[Number(button.dataset.load)];
        const quoted = button.dataset.quoted === 'true';
        const name = state.current;
        button.disabled = true;
        button.textContent = '排队中…';
        const turn = mediaQueue.then(() => {
            if (button.isConnected) button.textContent = '加载中…';
            return attempt(() => api.wxMedia(name, msg.id, quoted));
        });
        mediaQueue = turn.catch(() => {});
        const loaded = await turn;
        if (!loaded && button.isConnected) {
            button.disabled = false;
            button.textContent = '点击加载';
        }
        if (!loaded || state.current !== name) return;
        state.media[(quoted ? 'q:' : '') + msg.id] = loaded;
        renderMessages(true);
    };

    const loadMessages = async ({ keepScroll = false } = {}) => {
        const name = state.current;
        const seq = ++state.seq;
        state.loading = true;
        if (!keepScroll) renderMessages();
        try {
            const data = await api.wxMessages(name, state.history);
            if (seq !== state.seq || state.closed) return;
            const typeChanged = (state.chat || {}).chat_type !== data.chat_type;
            state.chat = data;
            state.messages = data.messages || [];
            state.loading = false;
            if (typeChanged) renderChat(); else renderMessages(keepScroll);
        } catch (e) {
            if (seq !== state.seq || state.closed) return;
            state.loading = false;
            const area = $('#wxMsgs', chatBox);
            if (area) area.innerHTML = `<div class="banner">${esc(e.message)}</div>`;
        }
    };

    const selectChat = (name) => {
        if (state.current === name) return;
        const draft = $('#wxInput', chatBox);
        state.current = name;
        state.chat = null;
        state.messages = [];
        state.history = 0;
        Object.values(state.media).forEach((item) => URL.revokeObjectURL(item.url));
        state.media = {};
        state.at = [];
        state.quote = null;
        renderSessions();
        renderChat();
        if (draft) $('#wxInput', chatBox).value = '';
        loadMessages();
    };

    // ---------- @、引用 ----------

    const renderExtra = () => {
        const extra = $('#wxExtra', chatBox);
        if (!extra) return;
        const parts = [];
        if (state.quote) {
            parts.push(`<span class="wx-chip">引用 ${esc(state.quote.sender)}：${esc(short(state.quote.content))}
                <button data-unquote aria-label="取消引用">×</button></span>`);
        }
        for (const name of state.at) {
            parts.push(`<span class="wx-chip">@${esc(name)}<button data-unat="${esc(name)}" aria-label="不 @ ${esc(name)}">×</button></span>`);
        }
        extra.innerHTML = parts.join('');
        extra.hidden = !parts.length;
    };

    const addAt = (name) => {
        if (name && !state.at.includes(name)) state.at.push(name);
        state.quote = null;  // 引用回复不带 @
        renderExtra();
        $('#wxInput', chatBox).focus();
    };

    const startQuote = (msg) => {
        state.quote = msg;
        state.at = [];
        renderExtra();
        $('#wxInput', chatBox).focus();
    };

    const pickAt = () => {
        // 群成员名单 SDK 拿不到，用这次读到的消息里说过话的人，也可以手填
        const names = [...new Set(state.messages.filter((m) => m.kind === 'friend' && m.sender).map((m) => m.sender))];
        const box = modal(`<h3>@ 群成员</h3>
            <div class="receivers">${names.map((n) => `<button class="chip" data-pick="${esc(n)}">${esc(n)}</button>`).join('')
                || '<span class="muted">这次读到的消息里没人说话，手动填名字吧</span>'}</div>
            <div class="field"><label for="atName">名字（群昵称）</label><input id="atName"></div>
            <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="atOk">@ TA</button></div>`);
        box.onclick = (e) => {
            const pick = e.target.closest('[data-pick]');
            if (pick) { closeModal(); addAt(pick.dataset.pick); }
        };
        $('#atOk', box).onclick = () => {
            const name = $('#atName', box).value.trim();
            if (name) { closeModal(); addAt(name); }
        };
    };

    // ---------- 右键菜单 ----------

    const menu = (event, items) => {
        event.preventDefault();
        closeMenu();
        const el = document.createElement('div');
        el.className = 'wx-menu';
        el.setAttribute('role', 'menu');
        el.innerHTML = items.map((item, i) => `<button role="menuitem" data-i="${i}">${esc(item.label)}</button>`).join('');
        document.body.appendChild(el);
        const { innerWidth, innerHeight } = window;
        el.style.left = `${Math.min(event.clientX, innerWidth - el.offsetWidth - 8)}px`;
        el.style.top = `${Math.min(event.clientY, innerHeight - el.offsetHeight - 8)}px`;
        el.onclick = (e) => {
            const button = e.target.closest('[data-i]');
            if (!button) return;
            closeMenu();
            items[Number(button.dataset.i)].action();
        };
        el.querySelector('button').focus();
    };

    const tickle = (msg) => attempt(async () => {
        toast(`正在拍一拍 ${msg.sender}…`);
        await api.wxTickle(state.current, msg.id);
        await loadMessages({ keepScroll: false });
    }, `拍了拍 ${msg.sender}`);

    const copy = (text) => attempt(() => navigator.clipboard.writeText(text), '已复制');

    // ---------- 发送 ----------

    const send = async () => {
        const input = $('#wxInput', chatBox);
        const content = input.value;
        if (!content.trim()) return;
        const button = $('#wxSend', chatBox);
        const name = state.current;
        const ok = await busy(button, '发送中…', () => attempt(async () => {
            if (state.quote) await api.wxQuote(name, state.quote.id, content);
            else await api.wxSendText(name, content, state.at);
            return true;
        }));
        if (!ok || state.current !== name) return;
        input.value = '';
        state.at = [];
        state.quote = null;
        renderExtra();
        loadMessages();
        loadSessions({ quiet: true });
    };

    const sendFile = async (file) => {
        if (!file) return;
        const name = state.current;
        toast(`正在发送 ${file.name}…`);
        const ok = await attempt(() => api.wxSendFile(name, file), `已发送 ${file.name}`);
        if (ok !== undefined && state.current === name) loadMessages();
    };

    const bindChat = () => {
        const input = $('#wxInput', chatBox);
        $('#wxSend', chatBox).onclick = send;
        input.onkeydown = (e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
        };
        $('#wxFile', chatBox).onchange = (e) => { sendFile(e.target.files[0]); e.target.value = ''; };
        const atButton = $('#wxAtBtn', chatBox);
        if (atButton) atButton.onclick = pickAt;
        $('#wxReload', chatBox).onclick = (e) => busy(e.target, '…', () => loadMessages());
        $('#wxOlder', chatBox).onclick = (e) => busy(e.target, '加载中…', () => {
            state.history += HISTORY_STEP;
            return loadMessages({ keepScroll: true });
        });
        const edit = $('#wxEditFriend', chatBox);
        if (edit) {
            edit.onclick = () => editFriend(api, state.current, [], (remark) => {
                state.current = null;
                selectChat(remark);
                loadSessions({ quiet: true });
            });
        }
        $('#wxExtra', chatBox).onclick = (e) => {
            if (e.target.closest('[data-unquote]')) state.quote = null;
            const unat = e.target.closest('[data-unat]');
            if (unat) state.at = state.at.filter((n) => n !== unat.dataset.unat);
            renderExtra();
        };
        const area = $('#wxMsgs', chatBox);
        area.oncontextmenu = (e) => {
            const row = e.target.closest('.wx-msg');
            if (!row) return;
            const msg = state.messages[Number(row.dataset.index)];
            const isGroup = (state.chat || {}).chat_type === 'group';
            const live = msg.actionable !== false;
            if (e.target.closest('[data-avatar]') && msg.kind === 'friend') {
                const items = [
                    ...(live ? [{ label: '拍一拍', action: () => tickle(msg) }] : []),
                    ...(isGroup ? [{ label: `@ ${msg.sender}`, action: () => addAt(msg.sender) }] : []),
                ];
                if (items.length) menu(e, items);
            } else if (e.target.closest('[data-bubble]')) {
                menu(e, [
                    ...(live ? [{ label: '引用', action: () => startQuote(msg) }] : []),
                    { label: '复制', action: () => copy(msg.content || '') },
                ]);
            }
        };
        // 手机和触控板上不方便右键，点头像也弹同一个菜单
        area.onclick = (e) => {
            const load = e.target.closest('button[data-load]');
            if (load) loadMedia(load);
            else if (e.target.closest('[data-avatar]')) area.oncontextmenu(e);
        };
    };

    // ---------- 事件和自动刷新 ----------

    list.onclick = (e) => {
        const item = e.target.closest('.wx-session');
        if (item) selectChat(item.dataset.name);
    };
    $('#wxFilter', box).oninput = (e) => { state.filter = e.target.value.trim(); renderSessions(); };
    $('#wxReloadSessions', box).onclick = (e) => busy(e.target, '…', () => loadSessions());

    document.addEventListener('click', closeMenuOutside);
    loadSessions();

    return () => {
        state.closed = true;
        document.removeEventListener('click', closeMenuOutside);
        closeMenu();
    };
}

function closeMenu() {
    $$('.wx-menu').forEach((el) => el.remove());
}

function closeMenuOutside(e) {
    if (!e.target.closest('.wx-menu') && !e.target.closest('[data-avatar]')) closeMenu();
}

// ==================== 好友 ====================

function friendsTab(box, api) {
    box.innerHTML = `
        <section class="wx-section">
            <div class="head"><h2>新的朋友</h2><span class="muted">通讯录里待处理的好友申请</span><span class="grow"></span>
                <button class="btn sm" id="frReload">刷新</button></div>
            <div id="frList"><div class="loader">读取好友申请…</div></div>
        </section>
        <section class="wx-section">
            <div class="head"><h2>添加朋友</h2><span class="muted">按微信号或手机号搜索，发好友申请</span></div>
            <form class="wx-form" id="frAdd">
                <div class="field"><label for="frKeywords">微信号 / 手机号</label><input id="frKeywords" required></div>
                <div class="field"><label for="frMsg">验证消息</label><input id="frMsg" placeholder="不填用微信默认的"></div>
                <div class="field"><label for="frRemark">备注</label><input id="frRemark"></div>
                <div><button class="btn primary" type="submit">发送申请</button></div>
            </form>
        </section>
        <section class="wx-section">
            <div class="head"><h2>修改备注</h2><span class="muted">也可以在聊天里打开好友的会话，点右上角「改备注」</span></div>
            <div><button class="btn" id="frEdit">选择好友…</button></div>
        </section>`;

    const listBox = $('#frList', box);
    const load = async () => {
        listBox.innerHTML = '<div class="loader">读取好友申请…</div>';
        let data;
        try {
            data = await api.wxFriendRequests();
        } catch (e) {
            if (listBox.isConnected) listBox.innerHTML = `<div class="banner">${esc(e.message)}</div>`;
            return;
        }
        if (!listBox.isConnected) return;
        const requests = data.requests || [];
        listBox.innerHTML = requests.length ? `<div class="wx-requests">${requests.map((r, i) => `
            <div class="wx-request">
                ${avatar(r.name || r.content)}
                <div class="wx-request-main"><b>${esc(r.name || '')}</b><span class="muted">${esc(r.msg || r.content)}</span></div>
                ${r.acceptable ? `<button class="btn sm primary" data-accept="${i}">通过</button>`
                    : `<span class="muted">${esc(r.status || '已处理')}</span>`}
            </div>`).join('')}</div>` : '<div class="empty">没有待处理的好友申请</div>';
        listBox.onclick = (e) => {
            const button = e.target.closest('[data-accept]');
            if (button) acceptFriend(api, requests[Number(button.dataset.accept)], load);
        };
    };
    $('#frReload', box).onclick = (e) => busy(e.target, '读取中…', load);

    $('#frAdd', box).onsubmit = async (e) => {
        e.preventDefault();
        const form = e.target;
        const request = {
            keywords: $('#frKeywords', form).value.trim(), addmsg: $('#frMsg', form).value.trim(),
            remark: $('#frRemark', form).value.trim(),
        };
        const result = await busy($('button[type=submit]', form), '发送中…',
            () => attempt(() => api.wxFriendAdd(request)));
        if (result === undefined) return;
        toast(result && result.message ? result.message : '已发送好友申请');
        form.reset();
    };
    $('#frEdit', box).onclick = async (e) => {
        const sessions = await busy(e.target, '读取会话…', () => attempt(() => api.wxSessions()));
        if (sessions) editFriend(api, '', sessions.sessions.map((s) => s.name));
    };
    load();
}

function acceptFriend(api, request, reload) {
    const box = modal(`<h3>通过好友申请</h3><div class="muted">${esc(request.content)}</div>
        <div class="field"><label for="acRemark">备注</label><input id="acRemark" value="${esc(request.name || '')}"></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="acOk">通过</button></div>`);
    $('#acOk', box).onclick = async (e) => {
        const payload = { content: request.content, remark: $('#acRemark', box).value.trim() };
        const result = await busy(e.target, '通过中…', () => attempt(() => api.wxFriendAccept(payload), '已通过'));
        if (result === undefined) return;
        closeModal();
        reload();
    };
}

/** 改好友的备注；name 为空时先从 choices 里选一个好友。改完会话名也跟着变，onDone 收到新名字 */
function editFriend(api, name, choices = [], onDone = () => {}) {
    const box = modal(`<h3>改备注${name ? ` · ${esc(name)}` : ''}</h3>
        ${name ? '' : `<div class="field"><label for="efName">好友（会话名）</label><input id="efName" list="efNames">
            <datalist id="efNames">${choices.map((n) => `<option value="${esc(n)}">`).join('')}</datalist></div>`}
        <div class="field"><label for="efRemark">新备注</label><input id="efRemark"></div>
        <div class="field"><span class="hint">改了备注以后，会话名也会变成新备注。监听中的好友不能改，先在监听管理里移除。</span></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="efOk">保存</button></div>`);
    $('#efOk', box).onclick = async (e) => {
        const payload = { chat_name: name || $('#efName', box).value.trim(), remark: $('#efRemark', box).value.trim() };
        const result = await busy(e.target, '保存中…', () => attempt(() => api.wxFriendEdit(payload), '已保存'));
        if (result === undefined) return;
        closeModal();
        onDone(payload.remark);
    };
}

// ==================== 小工具 ====================

const AVATAR_COLORS = ['#5b8def', '#2aae67', '#e0884a', '#b46cd9', '#d95c7a', '#3fa6b5', '#8c9a3f', '#c7703c'];

function avatar(name, attr = '') {
    const text = String(name || '?').trim();
    let hash = 0;
    for (const ch of text) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
    const initial = [...text][0] || '?';
    return `<span class="wx-avatar" ${attr} style="background:${AVATAR_COLORS[hash % AVATAR_COLORS.length]}" title="${esc(text)}">${esc(initial)}</span>`;
}

/** 会话列表的时间（微信给的是 "2026-10-09 21:49:00"）：今天只显示时分，昨天显示「昨天」，更早的显示月日 */
function sessionTime(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/.exec(value || '');
    if (!match) return value || '';
    const [, year, month, day, hour, minute] = match;
    const date = new Date(Number(year), Number(month) - 1, Number(day));
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const days = Math.round((today - date) / 86400000);
    if (days <= 0) return `${hour}:${minute}`;
    if (days === 1) return '昨天';
    return year === String(today.getFullYear()) ? `${month}-${day}` : `${year}-${month}-${day}`;
}

function short(text, max = 30) {
    const chars = [...String(text || '')];
    return chars.length > max ? `${chars.slice(0, max).join('')}…` : chars.join('');
}
