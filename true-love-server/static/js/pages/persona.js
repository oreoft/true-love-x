/**
 * 人设：这个 bot 回复用的 system prompt 和语音风格，存在 AI 库里；请求经 server 转发给 AI 的管理接口
 *
 * 一份默认人设，加上按群或人单独指定的；单独指定里没填的项沿用默认。
 * bot 还没改过默认人设时，用的是上线时从旧配置导入的初始人设（AI 那边 bot_id="*" 的那份），这里只展示不单独管理。
 * prompt 里写 {name}，回复时换成 bot 的昵称。
 */

import { botApi } from '../api.js';
import { $, $$, attempt, closeModal, confirmModal, esc, modal, toast } from '../ui.js';

const ALL_BOTS = '*';

const clip = (text, max) => {
    const flat = String(text || '').replace(/\s+/g, ' ').trim();
    return flat.length > max ? `${flat.slice(0, max)}…` : flat;
};

export async function show(root, ctx) {
    const bot = ctx.bot;
    const api = botApi(bot.bot_id);
    const { personas, fallback_prompt: fallbackPrompt } = await api.personas();
    const initial = personas.find((p) => p.bot_id === ALL_BOTS && !p.chat);
    const own = personas.find((p) => p.bot_id === bot.bot_id && !p.chat);
    const chats = personas.filter((p) => p.bot_id === bot.bot_id && p.chat);
    const botName = bot.name || bot.bot_id;
    const reload = () => show(root, ctx);
    // 默认人设现在实际生效的内容：bot 自己改过的优先，没改的项用初始人设
    const current = {
        prompt: (own && own.prompt) || (initial && initial.prompt) || fallbackPrompt,
        voice_style: (own && own.voice_style) || (initial && initial.voice_style) || '',
    };
    const text = (value, empty) => value
        ? `<div class="wrap" style="white-space:pre-wrap">${esc(value)}</div>` : `<div class="muted">${empty}</div>`;

    root.innerHTML = `
        <div class="head"><h2 class="grow">人设 · ${esc(botName)}</h2></div>
        <div class="card" style="cursor:default">
            <div class="card-top"><b>默认人设</b>${own ? '' : '<span class="tag">初始人设</span>'}
                <div class="actions" style="margin-left:auto"><button class="btn sm" id="editDefault">修改</button>
                ${own ? '<button class="btn sm" id="resetDefault">恢复初始</button>' : ''}</div></div>
            <div class="muted" style="font-size:12px">prompt</div>${text(current.prompt, '—')}
            <div class="muted" style="font-size:12px">语音风格</div>${text(current.voice_style, '没填，按模型默认的音色')}
        </div>

        <div class="head"><h3 class="grow">按群或人单独指定</h3>
            <button class="btn primary" id="add">添加</button></div>
        ${chats.length ? `<div class="table-wrap"><table>
            <thead><tr style="white-space:nowrap"><th>群或人</th><th>prompt</th><th>语音风格</th><th>操作</th></tr></thead>
            <tbody>${chats.map((p, i) => `<tr>
                <td style="white-space:nowrap"><b>${esc(p.chat)}</b></td>
                <td title="${esc(p.prompt)}">${esc(clip(p.prompt, 40)) || '<span class="muted">沿用默认</span>'}</td>
                <td title="${esc(p.voice_style)}">${esc(clip(p.voice_style, 20)) || '<span class="muted">沿用默认</span>'}</td>
                <td><div class="actions" style="flex-wrap:nowrap"><button class="btn sm" data-edit="${i}">修改</button>
                    <button class="btn sm danger" data-delete="${i}">删除</button></div></td>
            </tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">还没有单独指定的群或人，都用默认人设。</div>'}
        <div class="note">prompt 里写 {name}，回复时换成 bot 的昵称（现在是「${esc(bot.name || '还不知道')}」）。
            群或人按微信里显示的群名、昵称填；单独指定里没填的项沿用默认人设。改完下一条消息就生效。</div>`;

    $('#editDefault', root).onclick = () => form(api, { title: '默认人设', scope: {}, persona: current }, reload);
    if ($('#resetDefault', root)) {
        $('#resetDefault', root).onclick = () => confirmModal('恢复初始人设',
            '会删掉这个 bot 改过的默认人设，改回初始人设。', async () => {
                if (await attempt(() => api.personaDelete({}), '已恢复') !== undefined) reload();
            }, '恢复');
    }
    $('#add', root).onclick = () => form(api, { title: '给群或人单独指定人设', scope: {}, newChat: true }, reload);
    $$('[data-edit]', root).forEach((el) => {
        const p = chats[el.dataset.edit];
        el.onclick = () => form(api, { title: `群或人：${p.chat}`, scope: { chat: p.chat }, persona: p }, reload);
    });
    $$('[data-delete]', root).forEach((el) => {
        const p = chats[el.dataset.delete];
        el.onclick = () => confirmModal('删除人设', `删除后「${p.chat}」用默认人设。`, async () => {
            if (await attempt(() => api.personaDelete({ chat: p.chat }), '已删除') !== undefined) reload();
        }, '删除');
    });
}

/** target：{ title, scope（chat 或空）, persona（已有的内容）, newChat（新指定一个群或人） } */
function form(api, { title, scope, persona, newChat }, reload) {
    modal(`<h3>${esc(title)}</h3>
        ${newChat ? '<div class="field"><label for="fChat">群名或好友昵称</label><input id="fChat" placeholder="和微信里显示的一致"></div>' : ''}
        <div class="field"><label for="fPrompt">prompt${newChat || scope.chat ? '（空表示沿用默认）' : ''}</label>
            <textarea id="fPrompt" rows="8" placeholder="你是智能聊天机器人，你的名字叫{name}……">${esc(persona ? persona.prompt : '')}</textarea></div>
        <div class="field"><label for="fVoice">语音风格${newChat || scope.chat ? '（空表示沿用默认）' : ''}</label>
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
