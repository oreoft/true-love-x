/**
 * 人设：机器人回复用的 system prompt 和语音风格，存在 AI 库里；请求经 server 转发给 AI 的管理接口
 *
 * 三层，从小到大找，每一项各自取第一个填了的：
 *   这个 bot 里的某个群或人 → 这个 bot 的默认 → 所有 bot 共用的默认
 * prompt 里写 {name}，回复时换成 bot 的昵称。
 */

import { botApi } from '../api.js';
import { $, $$, attempt, closeModal, confirmModal, esc, modal, toast } from '../ui.js';

const ALL_BOTS = '*';

export async function show(root, ctx) {
    const bot = ctx.bot;
    const api = botApi(bot.bot_id);
    const { personas, fallback_prompt: fallbackPrompt } = await api.personas();
    const shared = personas.find((p) => p.bot_id === ALL_BOTS && !p.chat);
    const own = personas.find((p) => p.bot_id === bot.bot_id && !p.chat);
    const chats = personas.filter((p) => p.bot_id === bot.bot_id && p.chat);
    const botName = bot.name || bot.bot_id;
    const reload = () => show(root, ctx);

    const text = (value, empty) => value ? `<div class="wrap" style="white-space:pre-wrap">${esc(value)}</div>` : `<div class="muted">${empty}</div>`;
    const block = (title, tag, persona, key, emptyPrompt) => `
        <div class="card" style="cursor:default">
            <div class="card-top"><b>${title}</b>${tag}
                <div class="actions" style="margin-left:auto"><button class="btn sm" data-edit="${key}">修改</button>
                ${persona ? `<button class="btn sm danger" data-delete="${key}">清空</button>` : ''}</div></div>
            <div class="muted" style="font-size:12px">prompt</div>${text(persona && persona.prompt, emptyPrompt)}
            <div class="muted" style="font-size:12px">语音风格</div>${text(persona && persona.voice_style, '没填，沿用上一级')}
        </div>`;

    root.innerHTML = `
        <div class="head"><h2 class="grow">人设 · ${esc(botName)}</h2>
            <button class="btn primary" id="add">给群或人指定</button></div>
        <div class="cards">
            ${block(`${esc(botName)} 的默认`, '', own, 'own', '没填，用所有 bot 共用的')}
            ${block('所有 bot 共用的默认', '<span class="tag">所有 bot</span>', shared, 'shared', `没填，用代码里的兜底：${esc(fallbackPrompt)}`)}
        </div>
        <h3>按群或人指定</h3>
        ${chats.length ? `<div class="table-wrap"><table>
            <thead><tr><th>群或人</th><th>prompt</th><th>语音风格</th><th>操作</th></tr></thead>
            <tbody>${chats.map((p, i) => `<tr>
                <td><b>${esc(p.chat)}</b></td>
                <td class="wrap">${esc(p.prompt) || '<span class="muted">沿用默认</span>'}</td>
                <td class="wrap">${esc(p.voice_style) || '<span class="muted">沿用默认</span>'}</td>
                <td><div class="actions"><button class="btn sm" data-edit="chat-${i}">修改</button>
                    <button class="btn sm danger" data-delete="chat-${i}">删除</button></div></td>
            </tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">还没有单独指定的群或人，都用默认人设。</div>'}
        <div class="note">prompt 里写 {name}，回复时换成 bot 的昵称（现在是「${esc(bot.name || '还不知道')}」），同一份人设给不同的号用不会叫错名字。
            群或人按微信里显示的群名、昵称填。改完下一条消息就生效。</div>`;

    const targets = {
        own: { persona: own, scope: {} },
        shared: { persona: shared, scope: { all_bots: true } },
    };
    chats.forEach((p, i) => { targets[`chat-${i}`] = { persona: p, scope: { chat: p.chat } }; });

    $('#add', root).onclick = () => form(api, { title: '给群或人指定人设', scope: {}, newChat: true }, reload);
    $$('[data-edit]', root).forEach((el) => {
        const key = el.dataset.edit;
        const target = targets[key];
        const title = key === 'own' ? `${botName} 的默认` : key === 'shared' ? '所有 bot 共用的默认' : `群或人：${target.scope.chat}`;
        el.onclick = () => form(api, { ...target, title }, reload);
    });
    $$('[data-delete]', root).forEach((el) => {
        const target = targets[el.dataset.delete];
        const what = target.scope.chat ? `「${target.scope.chat}」的人设` : '这份默认人设';
        el.onclick = () => confirmModal('删除人设', `删除${what}后会沿用上一级。`, async () => {
            if (await attempt(() => api.personaDelete(target.scope), '已删除') !== undefined) reload();
        }, '删除');
    });
}

/** target：{ title, scope（all_bots 或 chat）, persona（已有的内容）, newChat（新指定一个群或人） } */
function form(api, { title, scope, persona, newChat }, reload) {
    modal(`<h3>${esc(title)}</h3>
        ${newChat ? '<div class="field"><label for="fChat">群名或好友昵称</label><input id="fChat" placeholder="和微信里显示的一致"></div>' : ''}
        <div class="field"><label for="fPrompt">prompt（空表示沿用上一级）</label>
            <textarea id="fPrompt" rows="8" placeholder="你是智能聊天机器人，你的名字叫{name}……">${esc(persona ? persona.prompt : '')}</textarea></div>
        <div class="field"><label for="fVoice">语音风格（空表示沿用上一级）</label>
            <textarea id="fVoice" rows="3" placeholder="请用……的音色朗读：">${esc(persona ? persona.voice_style : '')}</textarea></div>
        <div class="foot"><button class="btn" data-close>取消</button><button class="btn primary" id="save">保存</button></div>`);
    $('#save').onclick = async (e) => {
        const payload = { ...scope, prompt: $('#fPrompt').value.trim(), voice_style: $('#fVoice').value.trim() };
        if (newChat) payload.chat = $('#fChat').value.trim();
        if (newChat && !payload.chat) { toast('群名或昵称不能为空', 'error'); return; }
        if (!payload.prompt && !payload.voice_style) { toast('prompt 和语音风格至少填一个', 'error'); return; }
        e.target.disabled = true;
        const done = await attempt(() => api.personaSave(payload), '人设已保存');
        e.target.disabled = false;
        if (done !== undefined) { closeModal(); reload(); }
    };
}
