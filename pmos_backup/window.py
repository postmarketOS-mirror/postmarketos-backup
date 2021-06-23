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
                if "progress"  in packet:
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
                "can add or remove packages and configuration. This can not be undone.", xalign=0.0)

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

        self.backup_start = builder.get_object("backup_start")
        self.new_backup_label = builder.get_object("new_backup_label")
        self.new_backup_config = builder.get_object("new_backup_config")
        self.new_backup_system = builder.get_object("new_backup_system")
        self.new_backup_apks = builder.get_object("new_backup_apks")
        self.new_backup_homedirs = builder.get_object("new_backup_homedirs")
        self.backups = builder.get_object("backups")
        self.backups_restore = builder.get_object("backups_restore")

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

    def make_backup_list_row(self, path, metadata, distro, arch, current_distro):
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

        row.wrong_arch = False
        row.wrong_branch = False
        if 'arch' in metadata and metadata['arch'] != arch:
            warn = Gtk.Label(label=f"This backup is for another CPU ({metadata['arch']})")
            warn.set_line_wrap(True)
            warn.get_style_context().add_class('error')
            vbox.pack_start(warn, True, True, 0)
            row.wrong_arch = True
        if distro != current_distro:
            warn = Gtk.Label(label=f"This backup is for another version.")
            warn.set_line_wrap(True)
            warn.get_style_context().add_class('error')
            vbox.pack_start(warn, True, True, 0)
            row.wrong_branch = True

        row.path = path
        return row

    def fill_backup_list(self):
        for child in self.backups:
            child.destroy()
        for child in self.backups_restore:
            child.destroy()

        with open('/etc/apk/arch') as handle:
            arch = handle.read().strip()
        with open('/etc/os-release') as handle:
            for line in handle.readlines():
                if line.startswith("PRETTY_NAME="):
                    key, val = line.split('=', maxsplit=1)
                    current_distro = val.replace('"', '').strip()

        for path in sorted(glob.glob('/var/backup/*/metadata.json'), reverse=True):
            with open(path) as handle:
                metadata = json.loads(handle.read())

            distro = "Unknown OS"
            with open(os.path.join(os.path.dirname(path), 'state/os-release')) as handle:
                for line in handle.readlines():
                    if line.startswith("PRETTY_NAME="):
                        key, val = line.split('=', maxsplit=1)
                        distro = val.replace('"', '').strip()

            row = self.make_backup_list_row(os.path.dirname(path), metadata, distro, arch, current_distro)
            self.backups.add(row)
            row = self.make_backup_list_row(os.path.dirname(path), metadata, distro, arch, current_distro)
            self.backups_restore.add(row)

        def header(row, before, user_data):
            if before and not row.get_header():
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                row.set_header(sep)

        self.backups.set_header_func(header, None)
        self.backups_restore.set_header_func(header, None)
        self.backups.show_all()
        self.backups_restore.show_all()

    def progress_update(self, data):
        if data is None:
            self.dialog.destroy()
            self.fill_backup_list()
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
        self.dialog.bar.set_fraction(value/100.0)

    def on_backup_start_clicked(self, widget):
        name = self.new_backup_label.get_text().strip()
        stamp = datetime.datetime.today().strftime('%Y%m%d%H%M')
        target = os.path.join('/var/backup/', f"{stamp} {name}")
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

    def on_backup_row_activate(self, widget, row):
        dialog = Gtk.MessageDialog(transient_for=self.window,
                message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
                text="Do you want to export this backup to a file?")
        
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        dialog.destroy()

        dialog = Gtk.FileChooserDialog("Export Backup", self.window,
                Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                "_Export", Gtk.ResponseType.OK
                )

        file_filter = Gtk.FileFilter()
        file_filter.set_name("postmarketOS Backup")
        file_filter.add_pattern("*.backup.tar.gz")
        dialog.add_filter(file_filter)

        label = os.path.basename(row.path)
        filename = label + ".backup.tar.gz"
        suggested_file = os.path.join(os.path.expanduser('~'), filename)
        dialog.set_file(Gio.File.new_for_path(suggested_file))

        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        save_path = dialog.get_file()
        dialog.destroy()

        if save_path is None:
            return

        save_path = str(save_path.get_path())
        if not save_path.endswith('.backup.tar.gz'):
            save_path += '.backup.tar.gz'

        args = ['--export', save_path]
        thread = BackupThread(row.path, self.progress_update, args)
        thread.start()
        self.dialog = ProgressDialog(self.window, "Restoring backup")
        self.dialog.run()


    def on_restore_row_activate(self, widget, row):
        dialog = RestoreDialog(self.window)

        if row.wrong_arch:
            dialog.do_pkgs.set_sensitive(False)

        if row.wrong_branch:
            dialog.wrong_branch = True
            dialog.do_system.set_sensitive(False)

        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            return

        args = ['--restore']
        if not dialog.do_config.get_active():
            args.append('--no-config')
        if not dialog.do_system.get_active():
            args.append('--no-system')
        if not dialog.do_pkgs.get_active():
            args.append('--no-packages')
        if not dialog.do_apks.get_active():
            args.append('--no-apks')
        if not dialog.do_homedirs.get_active():
            args.append('--no-homedirs')
        dialog.destroy()

        if row.wrong_branch:
            args.append('--cross-branch')

        thread = BackupThread(row.path, self.progress_update, args)
        thread.start()
        self.dialog = ProgressDialog(self.window, "Restoring backup")
        self.dialog.run()

    def on_import_button_clicked(self, widget):
        dialog = Gtk.FileChooserDialog("Import Backup", self.window,
                Gtk.FileChooserAction.SAVE)
        dialog.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                "_Import", Gtk.ResponseType.OK
                )

        file_filter = Gtk.FileFilter()
        file_filter.set_name("postmarketOS Backup")
        file_filter.add_pattern("*.backup.tar.gz")
        dialog.add_filter(file_filter)

        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        source = dialog.get_file()
        dialog.destroy()

        if source is None:
            return

        source = str(source.get_path())

        args = ['--import', source]
        label = os.path.basename(source).replace('.backup.tar.gz', '')
        target = os.path.join('/var/backup/', label)
        thread = BackupThread(target, self.progress_update, args)
        thread.start()
        self.dialog = ProgressDialog(self.window, "Importing backup")
        self.dialog.run()
