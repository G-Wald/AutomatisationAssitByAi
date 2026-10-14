from bot.debug.recorder import prune_sessions


def make_session(root, name, size_kb):
    folder = root / name
    folder.mkdir()
    (folder / "bot.log").write_bytes(b"x" * size_kb * 1000)
    return folder


def test_oldest_sessions_removed_beyond_the_count(tmp_path):
    names = ["20260101_100000", "20260102_100000", "20260103_100000", "20260104_100000"]
    folders = [make_session(tmp_path, n, 1) for n in names]
    removed = prune_sessions(tmp_path, keep=2, max_total_mb=100, current=folders[-1])
    assert removed == folders[:2]
    assert sorted(p.name for p in tmp_path.iterdir()) == names[2:]


def test_oldest_sessions_removed_beyond_the_size(tmp_path):
    old, recent = make_session(tmp_path, "20260101_100000", 600), make_session(tmp_path, "20260102_100000", 600)
    current = make_session(tmp_path, "20260103_100000", 1)
    assert prune_sessions(tmp_path, keep=10, max_total_mb=1.0, current=current) == [old]
    assert recent.exists() and current.exists()


def test_other_folders_are_never_removed(tmp_path):
    (tmp_path / "mes_captures").mkdir()
    make_session(tmp_path, "20260101_100000", 1)
    prune_sessions(tmp_path, keep=0, max_total_mb=0)
    assert [p.name for p in tmp_path.iterdir()] == ["mes_captures"]
