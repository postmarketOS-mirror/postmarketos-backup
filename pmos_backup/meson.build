pkgdatadir = join_paths(get_option('prefix'), get_option('datadir'), meson.project_name())
moduledir = join_paths(pkgdatadir, 'pmos_backup')
gnome = import('gnome')

gnome.compile_resources('backup',
    'backup.gresource.xml',
    gresource_bundle: true,
    install: true,
    install_dir: pkgdatadir,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', join_paths(get_option('prefix'), get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
    input: 'pmos-backup.in',
    output: 'pmos-backup',
    configuration: conf,
    install: true,
    install_dir: get_option('bindir')
)

configure_file(
    input: 'pmos-backup-gui.in',
    output: 'pmos-backup-gui',
    configuration: conf,
    install: true,
    install_dir: get_option('bindir')
)

sources = [
    '__init__.py',
    '__main__.py',
    'window.py',
    'state.py',
]

install_data(sources, install_dir: moduledir)
