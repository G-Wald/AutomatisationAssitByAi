import numpy as np

from bot.debug.recorder import Recorder, prune_sessions


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


def test_failure_captures_kept_once_the_routine_ones_are_used_up(tmp_path):
    # 17/10 : 300 captures courantes avant 13:03, puis plus aucune capture des attaques ratées.
    recorder = Recorder(tmp_path, max_images=6)
    image = np.zeros((4, 4, 3), np.uint8)
    # Libellés différents : le quota par libellé (25/10, tests/test_fiabilite_equipe.py) ne joue pas ici.
    assert all(recorder.save(image, f"tour{i}") is not None for i in range(6))
    assert recorder.save(image, "tour6") is None
    assert recorder.save(image, "menu_attaquer_absent", lossless=True).name == "0007_menu_attaquer_absent.png"
    assert recorder.save(image, "echec", lossless=True) is not None
    assert recorder.save(image, "echec2", lossless=True) is None  # 6 // 3 captures d'échec au plus
