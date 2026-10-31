from jarvis.store import Store


def test_new_store_is_empty_and_creates_its_folder(tmp_path):
    store = Store(tmp_path / "sub" / "a.db")
    assert store.is_empty()
    assert store.seen_ids() == set()


def test_marked_ids_survive_a_restart(tmp_path):
    path = tmp_path / "a.db"
    first = Store(path)
    first.mark_seen(["a", "b"])
    first.mark_seen(["b", "c"])  # repeats are fine
    first.close()

    second = Store(path)
    assert second.seen_ids() == {"a", "b", "c"}
    assert not second.is_empty()
