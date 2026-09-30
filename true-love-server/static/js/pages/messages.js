/**
 * 聊天记录：左边选会话，右边往上滚自动加载更早的消息（按消息 id 往前翻页，每页 40 条），只读
 * 点消息上的发送者名字，看 AI 在这个会话里记下的这个人的画像（存在 AI 库里，经 server 转发，一次只取一个人）
 */

import { botApi } from '../api.js';
import { $, $$, esc, highlight, localTime, modal } from '../ui.js';

const PAGE_SIZE = 40;
// 离顶部多少像素时加载更早的一页
const LOAD_THRESHOLD = 60;
const TYPE_NAMES = { image: '图片', voice: '语音', video: '视频', file: '文件', link: '链接', refer: '引用' };

export async function show(root, ctx) {
    const api = botApi(ctx.bot.bot_id);
    const { chats } = await api.chats();
    if (!chats.length) {
        root.innerHTML = `<div class="head"><h2>聊天记录</h2></div><div class="empty">${esc(ctx.bot.name || ctx.bot.bot_id)} 还没有收到过消息。</div>`;
        return;
    }
    const state = { chatId: chats[0].chat_id, keyword: '', tailId: null, done: false, loading: false, seq: 0 };

    root.innerHTML = `
        <div class="head"><h2>聊天记录</h2><span class="muted">只读 · 往上滚自动加载更早的消息</span></div>
        <div class="chat">
            <div class="conv-list">${chats.map((chat) => `
                <button class="conv" data-chat="${esc(chat.chat_id)}">
                    <b>${esc(chat.chat_name)}</b><small>${chat.is_group ? '群聊' : '私聊'} · ${chat.count} 条</small>
                </button>`).join('')}</div>
            <div class="msgs-col">
                <div class="msgs-bar">
                    <input id="keyword" placeholder="搜关键词">
                    <button class="btn sm" id="search">搜索</button>
                    <button class="btn sm" id="clear" hidden>清除</button>
                </div>
                <div class="msgs" id="msgs"></div>
            </div>
        </div>`;
    const box = $('#msgs', root);

    const selectChat = (chatId) => {
        state.chatId = chatId;
        $$('.conv', root).forEach((el) => el.setAttribute('aria-current', String(el.dataset.chat === chatId)));
        $('#keyword', root).placeholder = `在「${chats.find((c) => c.chat_id === chatId).chat_name}」里搜关键词`;
        reload();
    };

    const reload = () => {
        state.seq += 1;
        state.tailId = null;
        state.done = false;
        state.loading = false;
        box.innerHTML = '<div class="loader" id="loader">加载中…</div>';
        $('#clear', root).hidden = !state.keyword;
        loadOlder(true);
    };

    const loadOlder = async (first = false) => {
        if (state.loading || state.done) return;
        state.loading = true;
        const seq = state.seq;
        const loader = $('#loader', box);
        loader.textContent = '加载更早的消息…';
        try {
            const page = await api.messages({ chatId: state.chatId, tailId: state.tailId, keyword: state.keyword, limit: PAGE_SIZE });
            if (seq !== state.seq) return;
            state.tailId = page.next_tail_id;
            state.done = page.next_tail_id === null;
            const previousHeight = box.scrollHeight;
            loader.insertAdjacentHTML('afterend', page.messages.map(renderMessage).join(''));
            loader.textContent = state.done
                ? (box.children.length > 1 ? '没有更早的消息了' : (state.keyword ? '没有找到包含这个关键词的消息' : '这个会话没有消息'))
                : '往上滚加载更早的消息';
            box.scrollTop = first ? box.scrollHeight : box.scrollHeight - previousHeight;
            // 一页没填满就滚不动，接着加载
            if (!state.done && box.scrollHeight <= box.clientHeight) setTimeout(() => loadOlder(), 0);
        } catch (e) {
            if (seq === state.seq) loader.textContent = `加载失败：${e.message}`;
        } finally {
            if (seq === state.seq) state.loading = false;
        }
    };

    const renderMessage = (msg) => `
        <div class="msg ${msg.is_at_me ? 'at' : ''}">
            <div class="meta"><button class="sender" data-sender="${esc(msg.sender_id)}" title="看 AI 记下的画像">${esc(msg.sender_name || msg.sender_id)}</button><span class="mono">${esc(localTime(msg.created_at_iso))}</span>
                ${TYPE_NAMES[msg.msg_type] ? `<span class="tag">${TYPE_NAMES[msg.msg_type]}</span>` : ''}
                <span class="mono">#${msg.id}</span></div>
            <div class="body">${highlight(msg.content || '', state.keyword)}</div>
        </div>`;

    const search = () => { state.keyword = $('#keyword', root).value.trim(); reload(); };
    $('#search', root).onclick = search;
    $('#keyword', root).onkeydown = (e) => { if (e.key === 'Enter') search(); };
    $('#clear', root).onclick = () => { $('#keyword', root).value = ''; search(); };
    // 消息是滚动时陆续加进来的，在容器上统一接点击
    box.onclick = (e) => {
        const sender = e.target.closest('[data-sender]');
        if (sender) showProfile(api, chats.find((c) => c.chat_id === state.chatId), sender.dataset.sender);
    };
    $$('.conv', root).forEach((el) => { el.onclick = () => selectChat(el.dataset.chat); });
    box.onscroll = () => { if (box.scrollTop < LOAD_THRESHOLD) loadOlder(); };
    selectChat(state.chatId);
}

/** 一个人在这个会话里的画像：AI 从聊天里记下的信息 */
async function showProfile(api, chat, sender) {
    const box = modal(`<h3>画像 · ${esc(sender)}</h3><div class="muted" style="font-size:12px">在「${esc(chat.chat_name)}」里记下的</div>
        <div class="loader">加载中…</div>
        <div class="foot"><button class="btn" data-close>关闭</button></div>`);
    let facts;
    try {
        facts = (await api.memory(chat.chat_id, sender)).facts;
    } catch (e) {
        if (box.isConnected) $('.loader', box).outerHTML = `<div class="banner">${esc(e.message)}</div>`;
        return;
    }
    if (!box.isConnected) return;  // 加载时弹窗已经关了
    $('.loader', box).outerHTML = facts.length
        ? `<div style="max-height:60vh;overflow:auto;display:grid;gap:6px">${facts.map((f) => `
            <div style="font-size:13px"><span class="muted">${esc(f.label)}</span>　${esc(f.value)}
                <span class="muted mono" style="font-size:11px">${esc(f.updated_at || "")}</span></div>`).join('')}</div>`
        : '<div class="empty">AI 还没记下这个人的信息。他聊到自己的情况、或者有人让机器人分析他的发言时会记下来。</div>';
}
