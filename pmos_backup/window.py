import os
import threading
import datetime
import subprocess
import json
import glob

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
        self.backups = builder.get_object("backups")

        self.apply_css(self.window, self.provider)
        self.window.show()
        self.fill_backup_list()
        Gtk.main()

    def apply_css(self, widget, provider):
        Gtk.StyleContext.add_provider(widget.get_style_context(),
                                      provider,
                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        if isinstance(widget, Gtk.Container):
            widget.forall(self.apply_css, provider)

    def on_main_window_destroy(self, widget):
        Gtk.main_quit()

    def fill_backup_list(self):
        for child in self.backups:
            child.destroy()

        for path in glob.glob('/var/backup/*/metadata.json'):
            with open(path) as handle:
                metadata = json.loads(handle.read())

            distro = "Unknown OS"
            with open(os.path.join(os.path.dirname(path), 'state/os-release')) as handle:
                for line in handle.readlines():
                    if line.startswith("PRETTY_NAME="):
                        key, val = line.split('=', maxsplit=1)
                        distro = val.replace('"', '').strip()

            row = Gtk.ListBoxRow()
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            vbox.set_margin_start(6)
            vbox.set_margin_end(6)
            vbox.set_margin_top(6)
            vbox.set_margin_bottom(6)
            row.add(vbox)

            label = Gtk.Label(label=metadata['label'], xalign=0.0)
            size = Gtk.Label(label=metadata['size'], xalign=0.0)
            distrolabel = Gtk.Label(label=distro, xalign=1.0)
            size.get_style_context().add_class('dim-label')
            distrolabel.get_style_context().add_class('dim-label')
            vbox.pack_start(label, True, True, 0)
            hbox = Gtk.Box()
            vbox.pack_start(hbox, True, True, 0)
            hbox.pack_start(size, True, True, 0)
            hbox.pack_start(distrolabel, True, True, 0)
            self.backups.add(row)

        def header(row, before, user_data):
            if before and not row.get_header():
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                row.set_header(sep)

        self.backups.set_header_func(header, None)
        self.backups.show_all()

    def progress_update(self, data):
        if data is None:
            self.dialog.destroy()
            self.fill_backup_list()
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
