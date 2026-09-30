/**
 * API 请求模块
 */

/**
 * 通用请求方法（请求当前页面所在的 server）
 */
async function apiRequest(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options
        });
        const data = await response.json();
        if (data.code !== 0) {
            throw new Error(data.message || '请求失败');
        }
        return data;
    } catch (error) {
        if (error.message === 'Failed to fetch') {
            throw new Error('无法连接到服务器');
        }
        throw error;
    }
}

// 导出为全局对象
window.api = {
    // Listen API
    fetchListenStatus: () => apiRequest('/admin/listen/status'),
    
    addListen: (chatName) => apiRequest('/admin/listen/add', {
        method: 'POST',
        body: JSON.stringify({ chat_name: chatName })
    }),
    
    removeListen: (chatName) => apiRequest('/admin/listen/remove', {
        method: 'POST',
        body: JSON.stringify({ chat_name: chatName })
    }),
    
    resetListen: (chatName) => apiRequest('/admin/listen/reset', {
        method: 'POST',
        body: JSON.stringify({ chat_name: chatName })
    }),
    
    refreshListen: () => apiRequest('/admin/listen/refresh', { method: 'POST' }),
    
    resetAllListen: () => apiRequest('/admin/listen/reset-all', { method: 'POST' }),
    
    getAllMessage: (chatName) => apiRequest('/admin/listen/get-all-message', {
        method: 'POST',
        body: JSON.stringify({ chat_name: chatName })
    }),
    
    // Loki API
    fetchLokiLogs: ({ beforeNs = '', services = '', keyword = '', limit = 50 } = {}) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (beforeNs) params.set('before_ns', beforeNs);
        if (services) params.set('services', services);
        if (keyword) params.set('keyword', keyword);
        return apiRequest(`/admin/loki/logs?${params}`);
    },

    // Reminder API
    fetchReminderList: () => apiRequest('/admin/reminder/list'),

    addReminder: (receiver, content, targetTimeIso, atUser, platform) => apiRequest('/admin/reminder/add', {
        method: 'POST',
        body: JSON.stringify({ receiver, content, target_time_iso: targetTimeIso, at_user: atUser, platform })
    }),

    updateReminder: (jobId, receiver, content, targetTimeIso, atUser, platform) => apiRequest('/admin/reminder/update', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId, receiver, content, target_time_iso: targetTimeIso, at_user: atUser, platform })
    }),

    deleteReminder: (jobId) => apiRequest('/admin/reminder/delete', {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId })
    }),

    // Task API
    fetchTaskList: () => apiRequest('/admin/task/list'),

    addTask: (jobName, receivers, schedule) => apiRequest('/admin/task/add', {
        method: 'POST',
        body: JSON.stringify({ job_name: jobName, receivers, schedule })
    }),

    updateTask: (taskId, jobName, receivers, schedule) => apiRequest('/admin/task/update', {
        method: 'POST',
        body: JSON.stringify({ task_id: taskId, job_name: jobName, receivers, schedule })
    }),

    deleteTask: (taskId) => apiRequest('/admin/task/delete', {
        method: 'POST',
        body: JSON.stringify({ task_id: taskId })
    }),

    runTask: (taskId) => apiRequest('/admin/task/run', {
        method: 'POST',
        body: JSON.stringify({ task_id: taskId })
    }),

    // Skill API
    fetchSkillList: () => apiRequest('/admin/skill/list'),

    saveSkill: (id, name, description, command, parameters, permissions) => apiRequest('/admin/skill/save', {
        method: 'POST',
        body: JSON.stringify({ id, name, description, command, parameters, permissions })
    }),

    deleteSkill: (id) => apiRequest('/admin/skill/delete', {
        method: 'POST',
        body: JSON.stringify({ id })
    }),
};

