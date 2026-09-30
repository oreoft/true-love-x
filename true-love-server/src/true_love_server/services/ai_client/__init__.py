# -*- coding: utf-8 -*-
"""
AI Client - server 调 AI

AI 给 server 开了两类接口，这里按类分开：
- business.py：业务接口，转交消息（/trigger）、取定时任务要用的数据（/data/*）
- admin.py：管理接口（/admin/*），tl-admin 的请求经 server 转发过去
"""
