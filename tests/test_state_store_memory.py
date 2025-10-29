from app.runtime.state_store import InMemoryRunStateStore, RunStateRecord


def test_in_memory_store_round_trip():
    store = InMemoryRunStateStore()
    state = {
        "run_id": "run-1",
        "messages": [{"role": "user", "content": "hi"}],
        "result": {"output_text": "ok"},
    }
    metadata = {"invocations": 1}
    store.put_state(RunStateRecord(run_id="run-1", state=state, metadata=metadata))

    loaded = store.get_state("run-1")
    assert loaded is not None
    assert loaded.state["messages"][0]["content"] == "hi"
    assert loaded.metadata["invocations"] == 1

    # Mutating the retrieved record should not leak into the store.
    loaded.state["messages"][0]["content"] = "mutated"
    loaded.metadata["invocations"] = 99

    fresh = store.get_state("run-1")
    assert fresh is not None
    assert fresh.state["messages"][0]["content"] == "hi"
    assert fresh.metadata["invocations"] == 1

    store.delete_state("run-1")
    assert store.get_state("run-1") is None
