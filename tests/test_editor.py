import pytest
import regex

from herdr_agent_rename.editor import KEY_OPERATIONS, TextEditor, normalize_input

GRAPHEME_TEXTS = ["abcdefghijklmnopqrstuvwxyz", "é中👩‍💻xyz", "́abc"]


def boundaries(text):
    out, index = [], 0
    for grapheme in regex.findall(r"\X", text):
        out.append(index)
        index += len(grapheme)
    return out + [len(text)]


def key(editor, name):
    result = editor.handle_key(name)
    assert result is not None, name
    assert editor.cursor in boundaries(editor.text)
    return result


def at(text, cursor, replace=False):
    editor = TextEditor(text, replace)
    editor.cursor = cursor
    return editor


# --- ported from text_editor.rs --------------------------------------------


@pytest.mark.parametrize(
    "name,text,cursor,killed",
    [
        ("left", "one two", 3, ""),
        ("c-b", "one two", 3, ""),
        ("right", "one two", 5, ""),
        ("c-f", "one two", 5, ""),
        ("home", "one two", 0, ""),
        ("c-a", "one two", 0, ""),
        ("end", "one two", 7, ""),
        ("c-e", "one two", 7, ""),
        ("backspace", "onetwo", 3, ""),
        ("c-h", "onetwo", 3, ""),
        ("delete", "one wo", 4, ""),
        ("c-d", "one wo", 4, ""),
        ("escape b", "one two", 0, ""),
        ("escape f", "one two", 7, ""),
        ("c-u", "two", 0, "one "),
        ("c-k", "one ", 4, "two"),
        ("c-w", "two", 0, "one "),
        ("escape backspace", "two", 0, "one "),
        ("escape c-h", "two", 0, "one "),
        ("escape d", "one ", 4, "two"),
        ("c-y", "one two", 4, ""),
        ("X", "one Xtwo", 5, ""),
    ],
)
def test_every_binding_edits_at_the_cursor(name, text, cursor, killed):
    editor = at("one two", 4)
    result = key(editor, name)
    assert (editor.text, editor.cursor, editor.killed) == (text, cursor, killed)
    assert result == (text != "one two")
    key(TextEditor(), name)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("left", "defaulxt"),
        ("right", "defaultx"),
        ("home", "xdefault"),
        ("end", "defaultx"),
    ],
)
def test_movement_disables_replace_on_type(name, expected):
    editor = TextEditor("default", True)
    key(editor, name)
    editor.insert("x")
    assert editor.text == expected


def test_replace_on_type_kills_and_yank():
    editor = TextEditor("default", True)
    editor.insert("new")
    assert editor.text == "new"
    for replacement, changed in [("default", False), ("another", True)]:
        editor = TextEditor("default", True)
        assert editor.insert(replacement) is changed
        assert editor.text == replacement
        assert not editor.replace_on_type
    editor = TextEditor("default", True)
    key(editor, "backspace")
    assert editor.text == ""
    editor = TextEditor("one two", True)
    key(editor, "c-w")
    assert editor.text == "one "
    assert not editor.replace_on_type
    key(editor, "c-k")
    key(editor, "backspace")
    key(editor, "c-y")
    key(editor, "c-y")
    assert editor.text == "onetwotwo"
    key(editor, "c-u")
    assert editor.killed == "onetwotwo"
    other = TextEditor()
    key(other, "c-y")
    assert other.text == ""


def test_unicode_graphemes_and_boundary_changing_edits():
    editor = TextEditor("é中👩‍💻")
    for expected in ["é中", "é", ""]:
        key(editor, "backspace")
        assert editor.text == expected
    editor = TextEditor("👩💻")
    key(editor, "left")
    editor.insert("‍")
    assert editor.cursor == len(editor.text)
    key(editor, "backspace")
    assert editor.text == ""
    editor = TextEditor("́x")
    key(editor, "home")
    editor.insert("e")
    assert editor.cursor == len("é")
    key(editor, "delete")
    assert editor.text == "é"


def test_accepting_trimmed_text_preserves_cursor_and_kill_buffer():
    editor = TextEditor("  feature/name  ", True)
    editor.trim_and_accept()
    assert editor.text == "feature/name"
    assert not editor.replace_on_type
    assert editor.cursor == len(editor.text)
    key(editor, "c-w")
    editor.trim_and_accept()
    key(editor, "c-y")
    assert editor.text == "feature/name"


def test_words_distinguish_paths_punctuation_and_whitespace():
    editor = TextEditor("src/foo_bar.rs  é中 👩‍💻")
    for expected in [
        "src/foo_bar.rs  é中 ",
        "src/foo_bar.rs  ",
        "src/foo_bar.",
        "src/foo_bar",
        "src/",
        "src",
        "",
    ]:
        key(editor, "c-w")
        assert editor.text == expected
    editor = TextEditor(" /tmp/foo_bar.rs")
    key(editor, "home")
    for expected in [2, 5, 6, 13, 14, 16]:
        key(editor, "escape f")
        assert editor.cursor == expected


def test_insertion_normalizes_controls():
    editor = TextEditor("ab")
    key(editor, "left")
    editor.insert("中\r\n\r\n\t\x00\x1b\x7fé")
    assert editor.text == "a中   ���éb"
    assert editor.handle_key("β") is True
    assert editor.text == "a中   ���éβb"


@pytest.mark.parametrize("name", ["enter", "escape", "c-c", "tab", "f1", "escape x"])
def test_overlay_keys_are_not_handled(name):
    editor = TextEditor("default", True)
    assert editor.handle_key(name) is None
    assert (editor.text, editor.cursor, editor.replace_on_type) == ("default", 7, True)


@pytest.mark.parametrize("text", GRAPHEME_TEXTS)
def test_viewport_is_pure_and_grapheme_safe(text):
    editor = TextEditor(text)
    for cursor in boundaries(text):
        editor.cursor = cursor
        for width in [0, 1, 2, 3, 8, 80]:
            visible, col = editor.viewport(width)
            assert (editor.text, editor.cursor) == (text, cursor)
            assert sum(editor_width(g) for g in regex.findall(r"\X", visible)) <= width
            assert width == 0 or col < width
            assert visible in text


def editor_width(grapheme):
    from herdr_agent_rename.editor import _width

    return _width(grapheme)


def test_viewport_examples():
    editor = TextEditor("abcdef")
    assert editor.viewport(4) == ("def", 3)
    editor.cursor = 2
    assert editor.viewport(4) == ("abcd", 2)
    editor.cursor = 0
    assert editor.viewport(4) == ("abcd", 0)


# --- additional coverage ----------------------------------------------------


def test_key_table_contents():
    assert set(KEY_OPERATIONS) == {
        "left", "c-b", "right", "c-f", "home", "c-a", "end", "c-e",
        "escape b", "escape f", "backspace", "c-h", "delete", "c-d",
        "c-u", "c-k", "c-w", "escape backspace", "escape c-h", "escape d", "c-y",
    }  # fmt: skip


def test_backspace_and_ctrl_h_are_equivalent():
    a, b = at("abc", 2), at("abc", 2)
    key(a, "backspace")
    key(b, "c-h")
    assert (a.text, a.cursor) == (b.text, b.cursor) == ("ac", 1)


def test_movement_returns_false_and_edges_do_not_move():
    editor = TextEditor("ab")
    assert key(editor, "right") is False
    key(editor, "home")
    assert key(editor, "left") is False and editor.cursor == 0
    assert key(editor, "delete") is True
    assert key(TextEditor(), "backspace") is False
    assert key(TextEditor(), "c-d") is False


def test_cursor_after_insert_moves_past_text():
    editor = TextEditor("ac")
    editor.cursor = 1
    assert editor.insert("b") is True
    assert (editor.text, editor.cursor) == ("abc", 2)


def test_movement_over_clusters():
    editor = TextEditor("é中👩‍💻")
    expected = [len("é中"), len("é"), 0]
    for position in expected:
        key(editor, "left")
        assert editor.cursor == position
    for position in [len("é"), len("é中"), len(editor.text)]:
        key(editor, "right")
        assert editor.cursor == position


def test_delete_forward_over_clusters():
    editor = TextEditor("👩‍💻中é")
    key(editor, "home")
    for expected in ["中é", "é", ""]:
        key(editor, "delete")
        assert editor.text == expected


def test_word_classes_backward_and_forward():
    editor = TextEditor("foo  bar")
    key(editor, "escape b")
    assert editor.cursor == 5
    key(editor, "escape b")
    assert editor.cursor == 0
    editor = TextEditor("  foo")
    key(editor, "home")
    key(editor, "escape f")
    assert editor.cursor == 5  # whitespace is absorbed together with the word
    editor = TextEditor("a_b-c")
    key(editor, "home")
    for expected in [3, 4, 5]:
        key(editor, "escape f")
        assert editor.cursor == expected
    editor = TextEditor("日本語 テスト")
    key(editor, "escape b")
    assert editor.cursor == 4
    key(editor, "escape b")
    assert editor.cursor == 0


def test_kill_word_right_and_kill_buffer_overwrite():
    editor = TextEditor("one two")
    key(editor, "home")
    key(editor, "escape d")
    assert (editor.text, editor.killed) == (" two", "one")
    key(editor, "c-k")
    assert editor.killed == " two"
    key(editor, "c-k")  # empty range: buffer retained
    assert editor.killed == " two"
    key(editor, "c-u")
    assert editor.killed == " two"
    assert editor.text == ""


def test_backspace_and_delete_do_not_touch_kill_buffer():
    editor = TextEditor("abc")
    editor.killed = "keep"
    key(editor, "backspace")
    key(editor, "home")
    key(editor, "delete")
    assert editor.killed == "keep"


def test_yank_inserts_at_cursor_and_reports_empty():
    editor = TextEditor("ab")
    assert key(editor, "c-y") is False
    editor.killed = "XY"
    editor.cursor = 1
    assert key(editor, "c-y") is True
    assert (editor.text, editor.cursor) == ("aXYb", 3)


def test_replace_on_type_yank_and_kill_clear_flag():
    editor = TextEditor("default", True)
    editor.killed = "new"
    key(editor, "c-y")
    assert (editor.text, editor.replace_on_type) == ("new", False)
    editor = TextEditor("default", True)
    key(editor, "c-k")  # empty range at end still disables replace
    assert editor.replace_on_type is False and editor.text == "default"


def test_replace_on_type_delete_does_not_clear_all():
    editor = TextEditor("default", True)
    key(editor, "delete")
    assert editor.text == "default" and not editor.replace_on_type


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a\r\nb", "a b"),
        ("a\rb", "a b"),
        ("a\nb", "a b"),
        ("a\tb", "a b"),
        ("a\n\rb", "a  b"),
        ("a\x00b", "a�b"),
        ("a\x1bb", "a�b"),
        ("a\x7fb", "a�b"),
        ("a\x85b", "a�b"),
        ("中é", "中é"),
    ],
)
def test_normalize_input(raw, expected):
    assert normalize_input(raw) == expected


def test_insert_empty_after_normalization_is_false():
    editor = TextEditor("abc")
    assert editor.insert("") is False
    assert editor.text == "abc"
    replace = TextEditor("abc", True)
    assert replace.insert("") is False
    assert replace.replace_on_type is True and replace.text == "abc"


def test_control_characters_stay_visible_for_validation():
    editor = TextEditor()
    editor.insert("a\x00b")
    assert editor.text == "a�b"


def test_single_control_character_key_is_not_inserted():
    editor = TextEditor("a")
    assert editor.handle_key("\x01") is None
    assert editor.text == "a"
    assert editor.handle_key(" ") is True
    assert editor.text == "a "


def test_trim_and_accept_cursor_adjustment():
    editor = TextEditor("  ab  ")
    editor.cursor = 1
    editor.trim_and_accept()
    assert (editor.text, editor.cursor) == ("ab", 0)
    editor = TextEditor("  ab  ")
    editor.cursor = 3
    editor.trim_and_accept()
    assert (editor.text, editor.cursor) == ("ab", 1)
    editor = TextEditor("  ab  ")
    editor.trim_and_accept()
    assert (editor.text, editor.cursor) == ("ab", 2)
    editor = TextEditor("   ")
    editor.trim_and_accept()
    assert (editor.text, editor.cursor) == ("", 0)


def test_trim_and_accept_repairs_cursor_inside_cluster():
    editor = TextEditor(" ́x")
    editor.cursor = 2
    editor.trim_and_accept()
    assert editor.cursor in boundaries(editor.text)


def test_clear_resets_state_but_keeps_kill_buffer():
    editor = TextEditor("abc", True)
    editor.killed = "k"
    editor.clear()
    assert (editor.text, editor.cursor, editor.replace_on_type, editor.killed) == (
        "", 0, False, "k",
    )  # fmt: skip


def test_viewport_wide_cjk_and_width_zero():
    editor = TextEditor("中文字符")
    assert editor.viewport(0) == ("", 0)
    assert editor.viewport(1) == ("", 0)
    editor.cursor = 0
    assert editor.viewport(5) == ("中文", 0)
    editor.cursor = 2
    assert editor.viewport(5) == ("中文", 4)
    editor.cursor = 4
    assert editor.viewport(5) == ("字符", 4)


def test_viewport_keeps_cursor_cell_free_at_end():
    editor = TextEditor("abcdefgh")
    assert editor.viewport(3) == ("gh", 2)
    editor.cursor = 5
    assert editor.viewport(3) == ("def", 2)


def test_viewport_emoji_and_combining_not_split():
    editor = TextEditor("é👩‍💻é")
    for cursor in boundaries(editor.text):
        editor.cursor = cursor
        for width in range(0, 8):
            visible, col = editor.viewport(width)
            assert visible in editor.text
            assert visible == "" or visible[0:1] != "́" or editor.text.startswith(visible)
            assert width == 0 or col < width
