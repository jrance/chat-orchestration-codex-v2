import importlib


def test_inmemory_saver_resolves():
    module = importlib.import_module("app.runtime.state.checkpointer")

    saver_cls = module._resolve_inmemory_saver()
    saver = saver_cls()

    assert saver is not None
