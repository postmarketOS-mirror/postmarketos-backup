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
    def __init__(self, target, callback, args=None):
        threading.Thread.__init__(self)
        self.target = target
        self.callback = callback
        self.args = args or []

    def run(self):
        cmd = ['pkexec', 'pmos-backup', '--json'] + self.args + [self.target]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)
        while True:
            line = p.stdout.readline()
            if not line:
                print("Backup subprocess ended")
                GLib.idle_add(self.callback, None)
                break
            if line.startswith("{"):
                packet = json.loads(line)
                if "progress" in packet:
                    self._progress(packet["progress"], packet["label"])
                elif "error" in packet:
                    self._error(packet["error"])
            else:
                print(">>> " + line)

    def _progress(self, value, label):
        GLib.idle_add(self.callback, (value, label))

    def _error(self, message):
        GLib.idle_add(self.callback, message)


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


class RestoreDialog(Gtk.Dialog):
    def __init__(self, parent):
        Gtk.Dialog.__init__(self, title="Restore", transient_for=parent, flags=0)
        self.wrong_branch = False
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_EXECUTE, Gtk.ResponseType.OK
        )

        self.set_default_size(300, 300)

        self.label = Gtk.Label(label="Restoring a backup will overwrite your existing data and "
                                     "can add or remove packages and configuration. This can not be undone.",
                               xalign=0.0)

        self.label.set_margin_start(18)
        self.label.set_margin_end(18)
        self.label.set_margin_top(18)
        self.label.set_margin_bottom(18)
        self.label.set_line_wrap(True)

        frame = Gtk.Frame()
        frame.get_style_context().add_class('view')
        checks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        checks.set_spacing(6)
        frame.add(checks)

        self.do_config = Gtk.CheckButton.new_with_label("Changed configuration")
        self.do_system = Gtk.CheckButton.new_with_label("Changed system files")
        self.do_pkgs = Gtk.CheckButton.new_with_label("Installed packages")
        self.do_apks = Gtk.CheckButton.new_with_label("Sideloaded packages")
        self.do_homedirs = Gtk.CheckButton.new_with_label("Home directories")

        self.do_apks.set_sensitive(False)
        self.do_pkgs.connect("toggled", self.on_pkgs_toggled)

        checks.pack_start(self.do_config, False, False, 0)
        checks.pack_start(self.do_system, False, False, 0)
        checks.pack_start(self.do_pkgs, False, False, 0)
        checks.pack_start(self.do_apks, False, False, 0)
        checks.pack_start(self.do_homedirs, False, False, 0)

        box = self.get_content_area()
        box.add(self.label)
        box.add(frame)
        self.show_all()

    def on_pkgs_toggled(self, widget):
        active = widget.get_active()
        self.do_apks.set_sensitive(active and not self.wrong_branch)
        if not active:
            self.do_apks.set_active(False)


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

        self.filter_targz = builder.get_object("filter_targz")
        self.filter_targz.set_name("Backup archive")

        self.backup_start = builder.get_object("backup_start")
        self.new_backup_config = builder.get_object("new_backup_config")
        self.new_backup_system = builder.get_object("new_backup_system")
        self.new_backup_apks = builder.get_object("new_backup_apks")
        self.new_backup_homedirs = builder.get_object("new_backup_homedirs")

        self.restore_start = builder.get_object("restore_start")
        self.restore_filepicker = builder.get_object("restore_filepicker")
        self.restore_config = builder.get_object("restore_config")
        self.restore_system = builder.get_object("restore_system")
        self.restore_packages = builder.get_object("restore_packages")
        self.restore_sideloaded = builder.get_object("restore_sideloaded")
        self.restore_homedirs = builder.get_object("restore_homedirs")

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
        if isinstance(data, str):
            err = Gtk.MessageDialog(
                transient_for=self.window,
                flags=0,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CANCEL,
                text=data
            )
            err.run()
            err.destroy()
            return
        value, label = data
        self.dialog.label.set_text(label)
        self.dialog.bar.set_fraction(value / 100.0)

    def on_backup_start_clicked(self, widget):

        dialog = Gtk.FileChooserDialog(
            title="Select a target file", parent=self.window, action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE,
            Gtk.ResponseType.OK,
        )

        dialog.add_filter(self.filter_targz)

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            target = dialog.get_filename()
            dialog.destroy()
        else:
            dialog.destroy()
            return

        if not target.endswith(".backup.tar.gz"):
            target += ".backup.tar.gz"

        print(f"Starting backup to {target}")
        args = []
        if not self.new_backup_config.get_active():
            args.append('--no-config')
        if not self.new_backup_system.get_active():
            args.append('--no-system')
        if not self.new_backup_apks.get_active():
            args.append('--no-apks')
        if not self.new_backup_homedirs.get_active():
            args.append('--no-homedirs')
        thread = BackupThread(target, self.progress_update, args)
        thread.start()
        self.dialog = ProgressDialog(self.window, "Making new backup")
        self.dialog.run()
