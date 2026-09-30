/**
 * 主应用入口
 */

(function() {
    const { createApp, ref, onMounted, onUnmounted } = Vue;

    createApp({
        setup() {
            // Tab state
            const activeTab = ref('list');

            // Toast & Confirm
            const { toasts, showToast } = useToast();
            const { confirmModal, showConfirm } = useConfirm();

            // List page
            const listPage = useListPage(showToast, showConfirm);

            // Loki logs page
            const lokiLogs = useLokiLogs(showToast);

            // Reminder page
            const reminderPage = useReminderPage(showToast, showConfirm);

            // Skill page
            const skillPage = useSkillPage(showToast, showConfirm);

            // Tab 切换
            const switchTab = (tab) => {
                activeTab.value = tab;

                if (tab === 'logs') {
                    lokiLogs.activateLokiLogs();
                } else {
                    lokiLogs.deactivateLokiLogs();
                }

                if (tab === 'reminders') {
                    reminderPage.fetchReminders();
                }
                if (tab === 'skills') {
                    skillPage.fetchSkills();
                }
            };

            // 初始化
            onMounted(() => {
                listPage.fetchStatus();
            });

            // 清理
            onUnmounted(() => {
                lokiLogs.deactivateLokiLogs();
            });

            return {
                // Tab
                activeTab,
                switchTab,

                // Toast & Confirm
                toasts,
                confirmModal,

                // List page
                ...listPage,

                // Loki logs page
                ...lokiLogs,

                // Reminder page
                ...reminderPage,

                // Skill page
                ...skillPage
            };
        }
    }).mount('#app');
})();
