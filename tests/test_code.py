"""Code blocks: tokenizer, wrapping, line numbers, copy."""

from __future__ import annotations

from harness import make
from PySide6.QtWidgets import QApplication

from pixelref.builtins.code import tokenize

LONG = "result = compute(" + ", ".join(f"argument_{i}" for i in range(40)) + ")"


def kinds(code: str, language: str) -> dict[str, str]:
    return {code[s:e]: k for s, e, k in tokenize(code, language)}


def test_tokenizer_finds_comments_strings_numbers_keywords():
    found = kinds("def f(x):  # note\n    return 'hi' + 42", "python")
    assert found["def"] == "keyword" and found["return"] == "keyword"
    assert found["# note"] == "comment" and found["'hi'"] == "string" and found["42"] == "number"
    assert found["f"] == "function"
    assert tokenize("anything", "plain") == []
    # a keyword inside a string or comment is not a keyword
    assert [k for _s, _e, k in tokenize("# if\n'for'", "python")] == ["comment", "string"]
    assert kinds("/* a */ int x = 0x1F;", "c")["/* a */"] == "comment"


def test_code_block_wraps_and_toggles():
    h = make()
    try:
        name = h.run("code.create", LONG, [0, 0])
        h.refresh()
        item = h.item(name)
        wrapped = item.boundingRect()
        assert abs(wrapped.width() - 480.0) < 1e-6 and wrapped.height() > 60
        h.run("code.set_attrs", {"wrap": False}, [name])
        h.refresh()
        unwrapped = h.item(name).boundingRect()
        assert unwrapped.width() > wrapped.width() and unwrapped.height() < wrapped.height()
        h.run("code.set_attrs", {"wrap": True, "width": 200.0}, [name])
        h.refresh()
        assert abs(h.item(name).boundingRect().width() - 200.0) < 1e-6
        before = h.item(name).boundingRect().width()
        h.run("code.set_attrs", {"line_numbers": True}, [name])
        h.refresh()
        assert h.item(name).boundingRect().width() == before  # the gutter fits inside the width
        h.undo()
        assert h.attr(name, "line_numbers") is False
    finally:
        h.close()


def test_copy_code_and_save_round_trip():
    from harness import tmp_dir

    h = make()
    try:
        name = h.run("code.create", "a = 1\nb = 2", [10, 20], "python", theme="light")
        assert h.run("code.copy") == "a = 1\nb = 2"
        assert QApplication.clipboard().text() == "a = 1\nb = 2"
        path = tmp_dir() / "code.pxref"
        h.save_to(path)
        assert h.open(path)
        assert h.attr(name, "code") == "a = 1\nb = 2" and h.attr(name, "theme") == "light"
    finally:
        h.close()


def test_add_code_block_starts_with_hello_world_not_the_clipboard():
    from pixelref.builtins.code import HELLO_WORLD

    h = make()
    try:
        QApplication.clipboard().setText("──────")
        name = h.run("code.add")
        assert h.attr(name, "code") == HELLO_WORLD and h.attr(name, "language") == "python"
        name = h.run("code.add_from_clipboard")
        assert h.attr(name, "code") == "──────"
    finally:
        h.close()
