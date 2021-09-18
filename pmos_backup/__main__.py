import os
import gi

from pmos_backup.window import BackupWindow

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio

gi.require_version('Handy', '1')
from gi.repository import Handy


class BackupApplication(Gtk.Application):
    def __init__(self, application_id, flags):
        Gtk.Application.__init__(self, application_id=application_id, flags=flags)
        self.connect("activate", self.new_window)

    def new_window(self, *args):
        BackupWindow(self)


def main(version):
    Handy.init()

    if os.path.isfile('backup.gresource'):
        print("Using resources from cwd")
        resource = Gio.resource_load("backup.gresource")
        Gio.Resource._register(resource)

    app = BackupApplication("org.postmarketos.Backup", Gio.ApplicationFlags.FLAGS_NONE)
    app.run()


if __name__ == '__main__':
    main('')
