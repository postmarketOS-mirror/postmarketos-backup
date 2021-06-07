import os
import threading
import datetime

import pmos_backup.state as state

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib, GObject, Gio, Gdk, GLib

gi.require_version('Handy', '1')
from gi.repository import Handy



class BackupThread(threading.Thread):
    def __init__(self, target, callback):
        threading.Thread.__init__(self)
        self.target = target
        self.callback = callback

    def run(self):
        state.set_progress_callback(self._progress)
        state.save_system_state(self.target)

    def _progress(value, label):
        GLib.idle_add(self.callback, (value, label))


class BackupWindow:
    def __init__(self, application):
        self.application = application
        Handy.init()

        builder = Gtk.Builder()
        builder.add_from_resource('/org/postmarketos/Backup/ui/backup.glade')
        builder.connect_signals(self)
        css = Gio.resources_lookup_data("/org/postmarketos/Backup/ui/style.css", 0)
        self.provider = Gtk.CssProvider()
        self.provider.load_from_data(css.get_data())

        self.provider = Gtk.CssProvider()
        self.provider.load_from_data(css.get_data())

        self.window = builder.get_object("main_window")
        self.window.set_application(self.application)
        self.mainstack = builder.get_object("mainstack")

        self.backup_start = builder.get_object("backup_start")
        self.new_backup_label = builder.get_object("new_backup_label")

        self.apply_css(self.window, self.provider)
        self.window.show()
        Gtk.main()

    def apply_css(self, widget, provider):
        Gtk.StyleContext.add_provider(widget.get_style_context(),
                                      provider,
                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        if isinstance(widget, Gtk.Container):
            widget.forall(self.apply_css, provider)

    def on_main_window_destroy(self, widget):
        Gtk.main_quit()

    def progress_update(self, data):
        value, label = data
        print(value, label)

    def on_backup_start_clicked(self, widget):
        name = self.new_backup_label.get_text().strip()
        stamp = datetime.date.today().strftime('%Y%m%d%H%M')
        target = os.path.join('/var/backup/', f"{stamp} {name}")
        print(f"Starting backup to {target}")
        os.makedirs(target)
        thread = BackupThread(target, self.progress_update)
        thread.start()
