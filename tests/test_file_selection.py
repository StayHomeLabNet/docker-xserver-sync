import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from dxsync.file_selection import CheckableFileTree


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_checkable_file_tree_selects_and_clears_nested_files() -> None:
    app()
    tree = CheckableFileTree()
    paths = (
        "wp-content/plugins/example/plugin.php",
        "wp-content/themes/example/style.css",
    )
    tree.set_file_paths(paths)

    assert tree.selected_file_paths() == paths
    tree.set_all_checked(False)
    assert tree.selected_file_paths() == ()
    tree.set_all_checked(True)
    assert tree.selected_file_paths() == paths
