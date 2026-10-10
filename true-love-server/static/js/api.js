/**
 * tl-admin 调 server 的 /admin/* 接口
 *
 * 按机器人的接口都在 /admin/bots/{bot_id}/ 下；技能、模型、日志、任务选项是平台级的，所有机器人共用。
 * 人设按机器人存，带 all_bots: true 时改的是所有机器人共用的那份。
 * 业务失败（code 不是 0）抛出带后端文案的 Error。
 */

async function request(url, { method = 'GET', body } = {}) {
    return parse(() => fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    }));
}

/** 请求体直接是文件内容 */
function upload(url, file) {
    return parse(() => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file }));
}

/** 取一个文件，返回本地链接和文件名；后端报错时回的是 JSON */
async function fetchFile(url) {
    let response;
    try {
        response = await fetch(url);
    } catch (e) {
        throw new Error('无法连接到服务器');
    }
    if (!response.ok || (response.headers.get('Content-Type') || '').includes('application/json')) {
        const data = await response.json().catch(() => ({ message: `服务器返回 ${response.status}` }));
        throw new Error(data.message || '加载失败');
    }
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = /filename\*=UTF-8''([^;]+)/.exec(disposition);
    const blob = await response.blob();
    return { url: URL.createObjectURL(blob), name: match ? decodeURIComponent(match[1]) : 'file', type: blob.type };
}

async function parse(send) {
    let response;
    try {
        response = await send();
    } catch (e) {
        throw new Error('无法连接到服务器');
    }
    const data = await response.json().catch(() => ({ code: -1, message: `服务器返回 ${response.status}` }));
    if (data.code !== 0) {
        throw new Error(data.message || '请求失败');
    }
    return data.data;
}

const query = (params) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
    }
    const text = search.toString();
    return text ? `?${text}` : '';
};

const post = (url, body = {}) => request(url, { method: 'POST', body });

/** 一个机器人的接口 */
export function botApi(botId) {
    const base = `/admin/bots/${encodeURIComponent(botId)}`;
    return {
        card: () => request(base),
        chats: () => request(`${base}/chats`),
        messages: ({ chatId, tailId, keyword, limit = 40 }) =>
            request(`${base}/messages${query({ chat_id: chatId, tail_id: tailId, keyword, limit })}`),
        receivers: () => request(`${base}/receivers`),

        listenStatus: () => request(`${base}/listen/status`),
        listenAdd: (chatName) => post(`${base}/listen/add`, { chat_name: chatName }),
        listenRemove: (chatName) => post(`${base}/listen/remove`, { chat_name: chatName }),
        listenReset: (chatName) => post(`${base}/listen/reset`, { chat_name: chatName }),
        listenRefresh: () => post(`${base}/listen/refresh`),
        listenResetAll: () => post(`${base}/listen/reset-all`),
        listenProbe: (chatName) => post(`${base}/listen/probe`, { chat_name: chatName }),
        listenSettings: () => request(`${base}/listen/settings`),
        listenSaveSettings: (switches) => post(`${base}/listen/settings`, switches),
        listenMuteAllGroups: () => post(`${base}/listen/mute-all-groups`),

        reminders: () => request(`${base}/reminders`),
        reminderAdd: (reminder) => post(`${base}/reminders/add`, reminder),
        reminderUpdate: (reminder) => post(`${base}/reminders/update`, reminder),
        reminderDelete: (jobId) => post(`${base}/reminders/delete`, { job_id: jobId }),

        tasks: () => request(`${base}/tasks`),
        taskAdd: (task) => post(`${base}/tasks/add`, task),
        taskUpdate: (task) => post(`${base}/tasks/update`, task),
        taskDelete: (taskId) => post(`${base}/tasks/delete`, { task_id: taskId }),
        taskRun: (taskId) => post(`${base}/tasks/run`, { task_id: taskId }),

        // 聊天页和好友页：现场操作机器人的微信（只有微信机器人有）
        wxSessions: () => request(`${base}/wechat/sessions`),
        wxMessages: (chatName, history = 0) => request(`${base}/wechat/messages${query({ chat_name: chatName, history })}`),
        wxSendText: (chatName, content, at = []) => post(`${base}/wechat/send-text`, { chat_name: chatName, content, at }),
        wxSendFile: (chatName, file) => upload(`${base}/wechat/send-file${query({ chat_name: chatName, filename: file.name })}`, file),
        wxMedia: (chatName, msgId, quoted = false) =>
            fetchFile(`${base}/wechat/media${query({ chat_name: chatName, msg_id: msgId, quoted: quoted || undefined })}`),
        wxQuote: (chatName, msgId, content) => post(`${base}/wechat/quote`, { chat_name: chatName, msg_id: msgId, content }),
        wxTickle: (chatName, msgId) => post(`${base}/wechat/tickle`, { chat_name: chatName, msg_id: msgId }),
        wxFriendRequests: () => request(`${base}/wechat/friend-requests`),
        wxFriendAccept: (request) => post(`${base}/wechat/friend-accept`, request),
        wxFriendAdd: (request) => post(`${base}/wechat/friend-add`, request),
        wxFriendEdit: (request) => post(`${base}/wechat/friend-edit`, request),

        memory: (chatId, sender) => request(`${base}/memory${query({ chat_id: chatId, sender })}`),
        personas: () => request(`${base}/personas`),
        personaSave: (persona) => post(`${base}/personas/save`, persona),
        personaDelete: (persona) => post(`${base}/personas/delete`, persona),
    };
}

/** 所有机器人共用的接口 */
export const platformApi = {
    bots: () => request('/admin/bots'),
    taskOptions: () => request('/admin/tasks/options'),
    logs: ({ beforeNs, services, keyword, botId, limit = 50 }) =>
        request(`/admin/loki/logs${query({ before_ns: beforeNs, services, keyword, bot_id: botId, limit })}`),
    skills: () => request('/admin/skill/list'),
    skillSave: (skill) => post('/admin/skill/save', skill),
    skillDelete: (id) => post('/admin/skill/delete', { id }),
    skillPermissionsSave: (skill, permissions) => post('/admin/skill/permissions/save', { skill, permissions }),
    defaultPermissionsSave: (permissions) => post('/admin/skill/default-permissions/save', { permissions }),
    models: () => request('/admin/models'),
    modelSave: (category, key, value) => post('/admin/models/save', { category, key, value }),
};
