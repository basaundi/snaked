import time
import weakref

import gtk

tab_bar_pos_mapping = {
    'top': gtk.POS_TOP,
    'bottom': gtk.POS_BOTTOM,
    'left': gtk.POS_LEFT,
    'right': gtk.POS_RIGHT
}

def init(injector):
    injector.add_context('editor', 'window', Window.get_editor_context)

    with injector.on('window') as ctx:
        ctx.bind_accel('close-editor', '_Window/Close _tab', '<ctrl>w', Window.close_editor)
        ctx.bind('close-window', '_Window/_Close', Window.close)

        #ctx.bind_accel('save-all', '_File/Save _all', '<ctrl><shift>s', Window.save_all)
        ctx.bind_accel('next-editor', '_Window/_Next tab', '<ctrl>Page_Up', Window.switch_to, 1)
        ctx.bind_accel('prev-editor', '_Window/_Prev tab', '<ctrl>Page_Down', Window.switch_to, -1)
        #ctx.bind_accel('new-file', Window.new_file_action)
        ctx.bind_accel('fullscreen', '_Window/Toggle _fullscreen', 'F11', Window.fullscreen)
        ctx.bind_accel('toggle-tabs-visibility', '_Window/Toggle ta_bs', '<Alt>F11', Window.toggle_tabs)

        #ctx.bind_accel('place-spot', self.add_spot_with_feedback)
        #ctx.bind_accel('goto-last-spot', self.goto_last_spot)
        #ctx.bind_accel('goto-next-spot', self.goto_next_prev_spot, True)
        #ctx.bind_accel('goto-prev-spot', self.goto_next_prev_spot, False)

        ctx.bind_accel('move-tab-left', '_Window/Move tab to _left',
            '<shift><ctrl>Page_Up', Window.move_tab, False)
        ctx.bind_accel('move-tab-right', '_Window/Move tab to _right',
            '<shift><ctrl>Page_Down', Window.move_tab, True)

class Window(gtk.Window):
    def __init__(self, manager, window_conf):
        gtk.Window.__init__(self, gtk.WINDOW_TOPLEVEL)

        self.editors = []
        self.last_switch_time = None
        self.panels = weakref.WeakKeyDictionary()

        self.set_name('SnakedWindow')
        self.set_role('Editor')
        self.connect('delete-event', self.on_delete_event)

        # set border width handling, see self.on_state_event for more details
        self.connect('window-state-event', self.on_state_event)

        self.set_default_size(800, 500)

        manager.activator.attach(self)

        self.main_pane = gtk.VPaned()
        self.main_pane_position_set = False
        self.add(self.main_pane)

        conf = manager.conf
        self.window_conf = window_conf
        self.manager = manager

        self.note = gtk.Notebook()
        self.note.set_show_tabs(
            conf['SHOW_TABS'] if conf['SHOW_TABS'] is not None else window_conf.get('show-tabs', True))
        self.note.set_scrollable(True)
        self.note.set_property('tab-hborder', 10)
        self.note.set_property('homogeneous', False)
        self.note.set_show_border(False)
        self.note.connect_after('switch-page', self.on_switch_page)
        self.note.connect('page_removed', self.on_page_removed)
        self.note.connect('page_reordered', self.on_page_reordered)
        self.note.props.tab_pos = tab_bar_pos_mapping.get(conf['TAB_BAR_PLACEMENT'], gtk.POS_TOP)
        self.main_pane.add1(self.note)

        if conf['RESTORE_POSITION'] and 'last-position' in window_conf:
            pos, size = window_conf['last-position']
            self.move(*pos)
            self.resize(*size)

        self.show_all()

        if window_conf.get('fullscreen', False):
            self.fullscreen()

    def get_editor_context(self):
        widget = self.note.get_nth_page(self.note.get_current_page())
        for e in self.editors:
            if e.widget is widget:
                return e

        return None

    def manage_editor(self, editor):
        self.editors.append(editor)
        label = gtk.Label('Unknown')
        self.note.append_page(editor.widget, label)
        self.note.set_tab_reorderable(editor.widget, True)
        self.focus_editor(editor)
        editor.view.grab_focus()

    def focus_editor(self, editor):
        idx = self.note.page_num(editor.widget)
        self.note.set_current_page(idx)

    def update_top_level_title(self):
        idx = self.note.get_current_page()
        if idx < 0:
            return

        editor = self.get_editor_context()
        if editor:
            title = editor.get_window_title.emit()

        if not title:
            title = self.note.get_tab_label_text(self.note.get_nth_page(idx))

        if title is not None:
            self.set_title(title)

    def set_editor_title(self, editor, title):
        self.note.set_tab_label_text(editor.widget, title)
        if self.note.get_current_page() == self.note.page_num(editor.widget):
            self.update_top_level_title()

    def on_delete_event(self, *args):
        self.quit()

    def on_state_event(self, widget, event):
        """Sets the window border depending on state

        The window border eases the resizing of the window using the mouse.
        In maximized and fullscreen state the this use case is irrelevant.
        Removing the border in this cases makes it easier to hit the scrollbar.

        Unfortunately this currently only works with tabs hidden.
        """
        state = event.new_window_state
        if state & gtk.gdk.WINDOW_STATE_MAXIMIZED or state & gtk.gdk.WINDOW_STATE_FULLSCREEN:
            self.set_border_width(0)
        else:
            self.set_border_width(self.manager.conf['WINDOW_BORDER_WIDTH'])

    def close_editor(self):
        editor = self.get_editor_context()
        if editor:
            idx = self.note.page_num(editor.widget)
            self.note.remove_page(idx)
            editor.editor_closed.emit()

    def close(self):
        pass

    def quit(self):
        self.window_conf['last-position'] = self.get_position(), self.get_size()

        if self.main_pane_position_set:
            _, _, _, wh, _ = self.window.get_geometry()
            self.window_conf['panel-height'] = wh - self.main_pane.get_position()

        self.hide()

    def save(self, editor):
        editor.save()

    def on_switch_page(self, *args):
        self.update_top_level_title()

    def on_page_removed(self, note, child, idx):
        for e in self.editors:
            if e.widget is child:
                spot = self.manager.get_last_spot(None, e)
                if spot:
                    note.set_current_page(note.page_num(spot.editor().widget))
                    return

        if idx > 0:
            note.set_current_page(idx - 1)

    def switch_to(self, editor, dir):
        if self.last_switch_time is None or time.time() - self.last_switch_time > 5:
            self.manager.add_spot(editor)

        self.last_switch_time = time.time()

        idx = ( self.note.get_current_page() + dir ) % self.note.get_n_pages()
        self.note.set_current_page(idx)

    def fullscreen(self):
        self.window_conf['fullscreen'] = not self.conf.get('fullscreen', False)
        if self.window_conf['fullscreen']:
            self.fullscreen()
        else:
            self.unfullscreen()

    def toggle_tabs(self):
        self.note.set_show_tabs(not self.note.get_show_tabs())
        self.window_conf['show-tabs'] = self.note.get_show_tabs()

    #@snaked.core.editor.Editor.stack_add_request
    def on_stack_add_request(self, editor, widget, on_popup):
        self.panels[widget] = on_popup

    #@snaked.core.editor.Editor.stack_popup_request
    def on_stack_popup_request(self, editor, widget):
        if widget in self.panels:
            for w in self.panels:
                if w is not widget and w is self.main_pane.get_child2():
                    self.main_pane.remove(w)

            if not self.main_pane_position_set:
                self.main_pane_position_set = True
                _, _, _, wh, _ = self.window.get_geometry()
                self.main_pane.set_position(wh - self.window_conf.get('panel-height', 200))

            self.main_pane.add2(widget)
            widget.show()

            if self.panels[widget]:
                self.panels[widget](widget, editor)

    def toggle_console(self, editor):
        from snaked.core.console import toggle_console
        toggle_console(editor)

    def send_to_console(self, editor):
        from snaked.core.console import send_to_console
        send_to_console(editor)

    def on_page_reordered(self, note, child, num):
        for i, e in enumerate(self.editors):
            if e.widget is child:
                self.editors[i], self.editors[num] = self.editors[num], self.editors[i]
                break

    def move_tab(self, editor, is_right):
        pos = self.note.page_num(editor.widget)
        if is_right:
            pos += 1
        else:
            pos -= 1

        if pos < 0 or pos >= self.note.get_n_pages():
            editor.message('This is dead end')
            return

        self.note.reorder_child(editor.widget, pos)

    def activate_main_window(self):
        self.present()
        #self.present_with_time(gtk.get_current_event_time())
