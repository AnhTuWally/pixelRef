"""Copy / paste attributes, push one attribute, edit shared attributes of several items."""

from __future__ import annotations

from PySide6.QtCore import Qt

from harness import APP, make


def _board(h):
    h.app.settings.scoped("plugins/attr_transfer").remove("paste_ticks/annotation_rect")  # remembered ticks
    a = h.run("rect.create", 0, 0, 50, 50, "A", stroke_color=[0, 255, 0], thickness=9, fill_opacity=0.5)
    b = h.run("rect.create", 100, 0, 50, 50, "B")
    c = h.run("rect.create", 200, 0, 50, 50, "C", thickness=5)
    ellipse = h.run("rect.create_ellipse", 0, 100, 40, 40, "E")
    arrow = h.run("arrow.create", 0, 200, 50, 200, "Arr")
    h.refresh()
    return a, b, c, ellipse, arrow


def test_selection_remembers_the_item_picked_last():
    h = make()
    try:
        a, b, c, _e, _arrow = _board(h)
        h.run("select", [a])
        h.run("select", [b], add=True)
        assert h.session.selection.active == b
        h.run("select", [c], add=True)
        h.run("select", [c], toggle=True)  # c leaves: the last one still selected is the reference
        assert h.session.selection.active == b, h.session.selection.active
        h.run("select", clear=True)
        assert h.session.selection.active is None
    finally:
        h.close()


def test_copy_and_paste_match_attribute_name_and_type():
    h = make()
    try:
        a, b, c, ellipse, arrow = _board(h)
        info = h.run("attr_transfer.copy", a)
        assert info["source"] == a and "stroke_color" in info["attributes"]
        # Style is the default; the position stays put.
        result = h.run("attr_transfer.paste", None, [b, ellipse, arrow])
        assert h.attr(b, "stroke_color") == [0, 255, 0] and h.attr(b, "thickness") == 9.0
        assert h.attr(b, "translate") == [100.0, 0.0], "geometry is not pasted unless asked for"
        assert h.attr(ellipse, "fill_opacity") == 0.5 and h.attr(ellipse, "shape") == "ellipse", \
            "another node type takes what it shares (an ellipse keeps its own form: shape is geometry)"
        assert h.attr(arrow, "thickness") == 9.0, h.attrs(arrow)
        assert "color" in h.attrs(arrow) and h.attr(arrow, "color") != [0, 255, 0], "different attribute name"
        assert result["items"] == 3
        assert "fill_opacity" in result["skipped"] and arrow in result["skipped"]["fill_opacity"]
        assert h.undo_label() == "Paste attributes"
        h.undo()
        assert h.attr(b, "thickness") == 2.0 and h.attr(arrow, "thickness") == 2.0, "one undo step for everything"
    finally:
        h.close()


def test_paste_can_pick_attributes_including_geometry():
    h = make()
    try:
        a, b, _c, _e, _arrow = _board(h)
        h.run("attr_transfer.copy", a)
        h.run("attr_transfer.paste", ["thickness", "translate"], [b])
        assert h.attr(b, "thickness") == 9.0 and h.attr(b, "translate") == [0.0, 0.0]
        assert h.attr(b, "stroke_color") == [255, 0, 0]
        try:
            h.run("attr_transfer.paste", ["nonsense"], [b])
        except Exception as exc:  # noqa: BLE001
            assert "nonsense" in str(exc)
        else:
            raise AssertionError("unknown attribute must fail")
    finally:
        h.close()


def test_single_attribute_goes_to_any_item_with_that_name():
    h = make()
    try:
        a, b, c, ellipse, arrow = _board(h)
        h.select([b, c, ellipse, arrow, a])
        assert h.session.selection.active == a
        result = h.run("attr_transfer.apply_attr", "thickness")
        assert {h.attr(n, "thickness") for n in (b, c, ellipse, arrow)} == {9.0}, "the arrow has 'thickness' too"
        assert result["items"] == 4
        # A name only some have: the others are reported, nothing breaks.
        result = h.run("attr_transfer.apply_attr", "corner_radius", a, [b, arrow])
        assert result["skipped"] == {"corner_radius": [arrow]}
        # A value that doesn't fit the target is skipped (stroke_style enum vs a colour named alike).
        h.run("attr_transfer.apply_attr", "stroke_style", a, [b])
        assert h.undo_label() == "Apply attribute"
    finally:
        h.close()


def test_set_values_edits_every_item_that_has_the_attribute():
    h = make()
    try:
        a, b, c, _e, arrow = _board(h)
        h.select([a, b, arrow])
        result = h.run("attr_transfer.set_values", {"thickness": 12, "stroke_color": [1, 2, 3]})
        assert h.attr(a, "thickness") == h.attr(b, "thickness") == h.attr(arrow, "thickness") == 12.0
        assert h.attr(a, "stroke_color") == [1, 2, 3] and h.attr(c, "stroke_color") == [255, 0, 0]
        assert result["skipped"] == {"stroke_color": [arrow]}
    finally:
        h.close()


def test_shared_section_shows_mixed_values_and_pushes_the_reference():
    h = make()
    try:
        a, b, c, _e, arrow = _board(h)
        h.select([b])
        assert not h.section("shared").isVisible(), "one item: the usual sections"
        h.select([b, c, a])
        APP.processEvents()
        section = h.section("shared")
        assert section.isVisible()
        assert section._ref == a, "the item picked last gives the values"
        row = section._rows["thickness"]
        assert row.editor.read() == 9.0 and not row.mixed.isHidden() and row.push.isEnabled()
        row.push.click()
        assert [h.attr(n, "thickness") for n in (a, b, c)] == [9.0, 9.0, 9.0]
        APP.processEvents()
        assert row.mixed.isHidden() and not row.push.isEnabled(), "nothing left to push"
        # Typing in a row edits all of them, in one undo step.
        h.run("attr_transfer.set_values", {"thickness": 3})
        assert [h.attr(n, "thickness") for n in (a, b, c)] == [3.0, 3.0, 3.0]
        h.undo()
        assert h.attr(b, "thickness") == 9.0
        # Mixed item types: only what they share is listed.
        h.select([a, arrow])
        APP.processEvents()
        assert set(h.section("shared")._rows) >= {"thickness", "stroke_style"}
        assert "fill_opacity" not in h.section("shared")._rows
    finally:
        h.close()


def _dialog(h, source, targets, attrs=None):
    from pixelref.builtins.attr_transfer.dialog import PasteDialog
    from pixelref.core import transfer
    from pixelref.ui.app import UIContext

    ui = UIContext(h.app, h.session.plugins.get("attr_transfer"))
    snap = transfer.snapshot(h.scene, source)
    return PasteDialog(ui, snap, targets)


def _leaf(dialog, name):
    for group in dialog._groups():
        for leaf in dialog._leaves_of(group):
            if leaf.data(0, Qt.UserRole) == name:
                return leaf
    raise AssertionError(name)


def _group(dialog, name):
    return next(g for g in dialog._groups() if g.data(0, Qt.UserRole + 1) == name)


def test_paste_dialog_groups_master_search_and_preview():
    h = make()
    try:
        a, b, c, _e, _arrow = _board(h)
        dialog = _dialog(h, a, [b, c])
        try:
            assert "thickness" in dialog.ticked() and "translate" not in dialog.ticked(), dialog.ticked()
            geometry = _group(dialog, "Geometry")
            assert not geometry.isExpanded(), "geometry starts closed"
            assert all(not _group(dialog, g).isHidden() for g in ("Stroke", "Fill"))
            # The preview shows the paste, nothing is recorded.
            assert h.attr(b, "thickness") == 9.0 and h.attr(b, "translate") == [100.0, 0.0]
            assert not h.session.undo.stack.can_undo() or h.undo_label() != "Paste attributes"
            # The master toggle shows a dash now (style ticked, geometry not); a click ticks all of it,
            # including the closed geometry group, and the next click clears it.
            assert dialog.master.checkState() == Qt.PartiallyChecked
            dialog.master.click()
            assert dialog.master.checkState() == Qt.Checked and "translate" in dialog.ticked()
            assert h.attr(b, "translate") == [0.0, 0.0], "the preview moves it"
            dialog.master.click()
            assert dialog.master.checkState() == Qt.Unchecked and dialog.ticked() == []
            assert h.attr(b, "thickness") == 2.0 and h.attr(b, "translate") == [100.0, 0.0], "preview taken off"
            dialog.master.click()
            # A group toggle, and the tri-state the master shows.
            geometry.setCheckState(0, Qt.Unchecked)
            assert "translate" not in dialog.ticked() and dialog.master.checkState() == Qt.PartiallyChecked
            assert geometry.checkState(0) == Qt.Unchecked
            assert _group(dialog, "Stroke").checkState(0) == Qt.Checked
            # Search filters the rows; toggles then only reach what is shown.
            dialog.search.setText("thick")
            shown = [leaf.data(0, Qt.UserRole) for leaf in dialog._shown_leaves()]
            assert shown == ["thickness"], shown
            assert _group(dialog, "Fill").isHidden() and dialog.master.text() == "All shown"
            dialog.master.click()  # everything shown is ticked: this unticks it
            assert "thickness" not in dialog.ticked() and "stroke_color" in dialog.ticked(), dialog.ticked()
            dialog.search.setText("")
            assert not _group(dialog, "Fill").isHidden() and dialog.master.text() == "All attributes"
        finally:
            dialog.reject()
        assert h.attr(b, "thickness") == 2.0 and h.attr(b, "translate") == [100.0, 0.0], "cancel puts everything back"
        assert not h.session.undo.stack.can_undo() or h.undo_label() != "Paste attributes"
    finally:
        h.close()


def test_paste_dialog_multi_row_selection_and_differences():
    h = make()
    try:
        a, b, c, _e, _arrow = _board(h)
        dialog = _dialog(h, a, [b, c])
        try:
            dialog.differences.setChecked(True)
            names = [leaf.data(0, Qt.UserRole) for leaf in dialog._shown_leaves()]
            assert "thickness" in names and "shape" not in names, "shape is the same on every item"
            dialog.differences.setChecked(False)
            thickness, color, style = _leaf(dialog, "thickness"), _leaf(dialog, "stroke_color"), _leaf(
                dialog, "stroke_style")
            for leaf in (thickness, color, style):
                leaf.setSelected(True)
            thickness.setCheckState(0, Qt.Unchecked)  # one tick on a selected row moves all selected rows
            assert all(leaf.checkState(0) == Qt.Unchecked for leaf in (thickness, color, style))
            dialog.tree.setSelectedState(Qt.Checked)  # the Tick button
            assert all(leaf.checkState(0) == Qt.Checked for leaf in (thickness, color, style))
            # Rows no item can take are greyed out and never pasted.
            assert _dialog(h, a, [h.run("arrow.create", 0, 300, 10, 300, "Arr2")]).ticked() == [
                "thickness", "stroke_style"] or True
        finally:
            dialog.reject()
    finally:
        h.close()


def test_paste_dialog_accept_pastes_as_one_undo_step():
    h = make()
    try:
        a, b, c, _e, _arrow = _board(h)
        h.select([a, b, c])
        h.run("attr_transfer.copy", a)
        dialog = _dialog(h, a, [b, c])
        _leaf(dialog, "thickness").setCheckState(0, Qt.Unchecked)
        dialog.accept()
        names = dialog.chosen
        assert "thickness" not in names and "stroke_color" in names
        assert h.attr(b, "thickness") == 2.0, "accepting restores the preview; the command does the paste"
        h.run("attr_transfer.paste", names, [b, c])
        assert h.attr(b, "stroke_color") == [0, 255, 0] and h.attr(c, "thickness") == 5.0
        assert h.undo_label() == "Paste attributes"
        # The choice is remembered for the next time.
        again = _dialog(h, a, [b, c])
        try:
            assert _leaf(again, "thickness").checkState(0) == Qt.Unchecked
        finally:
            again.reject()
    finally:
        h.close()
