# llm_bootstrap.py
import logging

from true_love_ai.core.model_registry import get_model_registry

LOG = logging.getLogger(__name__)


def init_llm():
    # 模型存在 AI 库里，调用前库要先初始化好（main 里先 init_db）
    get_model_registry().load()

    # 提前实例化客户端，config 错误在启动时暴露
    from true_love_ai.llm.router import get_openai_client
    get_openai_client()
    LOG.info("OpenAI client 初始化完成")
