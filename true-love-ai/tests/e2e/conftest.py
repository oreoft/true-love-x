import os
import uuid

import pytest

from .stack import Stack


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TL_E2E", "1") == "0":
        skip = pytest.mark.skip(reason="TL_E2E=0")
        for item in items:
            if "e2e" in item.nodeid:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def stack():
    s = Stack().start()
    yield s
    s.stop()


@pytest.fixture
def e2e(stack, request):
    """The running stack with a clean fake LLM and base; prints both services' logs when the test fails"""
    stack.llm.reset()
    stack.base.reset()
    marks = {name: len(stack.log(name)) for name in stack.logs}
    yield stack
    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        for name, start in marks.items():
            print(f"\n===== {name} log =====\n{stack.log(name)[start:][-8000:]}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item.rep_call = rep


@pytest.fixture
def chat():
    """A group name no other test uses, so conversations don't mix"""
    return f"群-{uuid.uuid4().hex[:6]}"
