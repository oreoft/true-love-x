/**
 * 总览：每个登记过的机器人一张卡片，在线状态是现场问 base 的
 */

import { $$, ago, esc, platformName, platformTag, shortTime } from '../ui.js';

export async function show(root, ctx) {
    const bots = await ctx.refreshBots();
    if (!bots.length) {
        root.innerHTML = `<div class="head"><h2>总览</h2></div>
            <div class="empty">还没有机器人登记。base 连上 server 时会自动登记，不用在后台手动添加。</div>`;
        return;
    }
    root.innerHTML = `
        <div class="head"><h2>总览</h2><span class="muted">共 ${bots.length} 个 bot，点卡片进入</span></div>
        <div class="cards">${bots.map(card).join('')}</div>
        <div class="note">bot 由 base 连上 server 时自动登记。监听这类渠道专属功能，按 server 返回的能力列表决定是否显示。</div>`;
    $$('[data-go]', root).forEach((el) => { el.onclick = () => ctx.selectBot(el.dataset.go); });
}

function card(bot) {
    const online = bot.status && bot.status.online;
    const statusText = online ? '在线' : (bot.status && bot.status.reachable ? `${platformName(bot.platform)}离线` : 'base 连不上');
    return `
        <button class="card" data-go="${esc(bot.bot_id)}">
            <div class="card-top">
                <span class="dot ${online ? 'on' : 'off'}"></span><b>${esc(bot.name || bot.bot_id)}</b>${platformTag(bot.platform)}
                ${bot.is_default ? '<span class="tag">默认</span>' : ''}
                <span class="pill ${online ? 'ok' : 'bad'}" style="margin-left:auto">${statusText}</span>
            </div>
            <div class="mono muted">${esc(bot.bot_id)}</div>
            <div class="kv">
                <div><span>今日消息</span><strong>${Number(bot.today_messages).toLocaleString()}</strong></div>
                <div><span>监听</span><strong>${bot.listen_count === null ? '—' : bot.listen_count}</strong></div>
                <div><span>最后登记</span><strong style="font-size:13px">${esc(ago(bot.last_seen_at))}</strong></div>
            </div>
            <div class="muted" style="font-size:12px">下一个提醒或任务：${esc(shortTime(bot.next_run_time))}</div>
        </button>`;
}
