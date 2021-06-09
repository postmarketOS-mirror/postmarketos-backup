import os
import threading
import datetime
import subprocess
import json

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
        cmd = ['pkexec', 'pmos-backup', '--json', self.target]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)
        while True:
            line = p.stdout.readline()
            if not line:
                print("Backup subprocess ended")
                GLib.idle_add(self.callback, None)
                break
            if line.startswith("{"):
                packet = json.loads(line)
                if "progress"  in packet:
                    self._progress(packet["progress"], packet["label"])
            else:
                print(">>> " + line)

    def _progress(self, value, label):
        GLib.idle_add(self.callback, (value, label))


class ProgressDialog(Gtk.Dialog):
    def __init__(self, parent, title):
        Gtk.Dialog.__init__(self, title=title, transient_for=parent, flags=0)

        self.label = Gtk.Label(label="Starting...")
        self.bar = Gtk.ProgressBar()
        self.bar.set_show_text(True)

        self.label.set_margin_start(18)
        self.label.set_margin_end(18)
        self.label.set_margin_top(18)
        self.label.set_margin_bottom(18)
        self.bar.set_margin_start(18)
        self.bar.set_margin_end(18)
        self.bar.set_margin_top(18)
        self.bar.set_margin_bottom(18)

        box = self.get_content_area()
        box.add(self.label)
        box.add(self.bar)
        self.show_all()


class BackupWindow:
    def __init__(self, application):
        self.application = application
        self.dialog = None
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
        if data is None:
            self.dialog.destroy()
            return
        value, label = data
        self.dialog.label.set_text(label)
        self.dialog.bar.set_fraction(value/100.0)

    def on_backup_start_clicked(self, widget):
        name = self.new_backup_label.get_text().strip()
        stamp = datetime.date.today().strftime('%Y%m%d%H%M')
        target = os.path.join('/var/backup/', f"{stamp} {name}")
        print(f"Starting backup to {target}")
        thread = BackupThread(target, self.progress_update)
        thread.start()
        self.dialog = ProgressDialog(self.window, "Making new backup")
        self.dialog.run()
