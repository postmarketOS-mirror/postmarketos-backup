project('pmos-backup',
    version: '1.0.0',
    meson_version: '>= 0.50.0',
    default_options: ['warning_level=2'])

dependency('libhandy-1', version: '>=1.0.0')

subdir('data')
subdir('pmos_backup')

meson.add_install_script('build-aux/meson/postinstall.py')
