import os
import sys
import subprocess
import shutil
import glob
import json
import pathlib
import shlex
import tarfile
import time
from datetime import datetime

_progress_json = False


def _progress(value, label):
    if _progress_json:
        print(json.dumps({"progress": value, "label": label}))
        sys.stdout.flush()
    else:
        sys.stderr.write(label + "\n")


def _error(message):
    if _progress_json:
        print(json.dumps({"error": message}))
        sys.stdout.flush()
    else:
        sys.stderr.write(message + "\n")


def parse_apk_cache():
    result = {}
    for path in glob.glob('/etc/apk/cache/*.apk'):
        fname = os.path.basename(path)
        pkgname = '-'.join(fname.split('-')[0:-2])
        if pkgname not in result:
            result[pkgname] = []
        result[pkgname].append(path)
    return result


def export_backup(source, target):
    # Count files for progress
    files = 0
    for root, dirs, filenames in os.walk(source):
        files += len(filenames)

    cmd = ['tar', '-czvf', target, '.']
    env = os.environ.copy()
    env['GZIP'] = '-1'  # Fastest gzip compression
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         universal_newlines=True, cwd=source, env=env)

    done = 0
    while True:
        line = p.stdout.readline()
        if not line:
            break
        done += 1

        if done % 3 == 0:
            _progress((done / files) * 100, "Exporting")

    p.wait()
    if p.returncode != 0:
        _error("Exporting the tar archive failed")

    if not os.path.isfile(target):
        return

    # Change owner/group of the resulting backup archive to the
    # owner of the directory it's in so users don't end up with
    # a "locked" file when they save it in their homedir
    stat = os.stat(os.path.dirname(target))
    os.chown(target, stat.st_uid, stat.st_gid)


def import_backup(source, target):
    os.makedirs(target)
    cmd = 'pv -n {} | tar -xzf - -C {}'.format(shlex.quote(source),
                                               shlex.quote(target))

    p = subprocess.Popen(cmd, shell=True, stderr=subprocess.PIPE, universal_newlines=True)

    while True:
        line = p.stderr.readline()
        if not line:
            break

        prog = int(line.strip())
        _progress(prog, "Importing")

    p.wait()
    if p.returncode != 0:
        _error("Importing the tar archive failed")
        exit(1)


def save_system_state(target, version, measure=False, do_config=True, do_system=True, do_apks=True, do_homedirs=True):
    pscale = 1
    if not do_homedirs:
        pscale = 2
    errors = []
    tgz = None
    if not measure:
        os.makedirs(os.path.dirname(target), exist_ok=True)

        with open("/etc/apk/arch") as handle:
            arch = handle.read().strip()

        headers = {
            "arch": str(arch),
            "backup-version": str(version),
            "created": str(datetime.now().isoformat()),
        }

        with open('/etc/os-release') as handle:
            distro = {}
            for line in handle:
                k, v = line.rstrip().split("=")
                distro[k] = v.strip('"')
        headers['os-version'] = distro['VERSION_ID']

        tgz = tarfile.open(target, 'w:gz', pax_headers=headers)

    if not measure:
        # Copy over the apk state and some metadata about the installation
        _progress(10 * pscale, 'Copying metadata')
        tgz.add('/etc/apk/world')
        tgz.add('/etc/apk/repositories')
        tgz.add('/etc/os-release')

    # Check for modified config
    config_size = 0
    if do_config:
        _progress(20 * pscale, "Checking modified config")
        modified_config = subprocess.check_output(['apk', 'audit', '--backup'], universal_newlines=True)
        for line in modified_config.splitlines():
            state, path = line.split(' ', maxsplit=1)
            if state in ['A', 'U']:
                source = os.path.join('/', path)
                if measure:
                    if os.path.exists(source):
                        config_size += os.stat(source).st_size
                else:
                    tgz.add(source)

    # Find modified system files
    system_size = 0
    if do_system:
        _progress(30 * pscale, "Checking modified system files")
        modified_system = subprocess.check_output(['apk', 'audit', '--system'], universal_newlines=True)
        for line in modified_system.splitlines():
            state, path = line.split(' ', maxsplit=1)

            # Don't copy generated python cache files which show up in the system audit
            if '__pycache__' in path:
                continue

            if state in ['A', 'U']:
                source = os.path.join('/', path)

                if measure:
                    system_size += os.stat(source).st_size
                else:
                    tgz.add(source)

    # Try to get sideloaded apks from the apk cache. This is not perfect yet since we can't match
    # up the version hash in the world file to the exact .apk file that was installed since the
    # algorithm is not known. The workaround is copying all the apks from the cache for the same
    # pkgname.
    cache_size = 0
    if do_apks:
        _progress(40 * pscale, "Copying sideloaded packages")
        apk_cache = parse_apk_cache()
        with open('/etc/apk/world', 'r') as handle:
            for line in handle.readlines():
                if '><' in line:
                    pkgname, version = line.split('>', maxsplit=1)
                    if pkgname not in apk_cache:
                        errors.append("Could not backup sideloaded package: {}, "
                                      "not in cache.".format(pkgname))
                        continue
                    for path in apk_cache[pkgname]:
                        if measure:
                            # Path might not exist if it's a broken symlink
                            if os.path.exists(path):
                                cache_size += os.stat(path).st_size
                        else:
                            tgz.add(path)

    if measure:
        return {
            "errors": errors,
            "config": config_size,
            "system": system_size,
            "cache": cache_size,
        }
    else:
        logfile = os.path.join(os.path.dirname(target), 'backup.log')
        with open(logfile, 'a') as handle:
            handle.write('*** Copy system state ***\n')
            for error in errors:
                handle.write(f'{error}\n')
        return tgz


def save_homedirs(target, tgz):
    errors = []
    _progress(50, "Copying homedirs")

    # Count the total files for progress calculations
    count = 0
    for root, dirs, files in os.walk('/home', topdown=True):
        # Skip cache dirs
        dirs[:] = [d for d in dirs if d != ".cache"]
        for fname in files:
            count += 1

    # Do the actual copy
    done = 0
    for root, dirs, files in os.walk('/home', topdown=True):
        dirs[:] = [d for d in dirs if d != ".cache"]

        for fname in files:
            path = os.path.join(root, fname)
            try:
                if path == target:
                    continue
                tgz.add(path)
            except Exception as e:
                errors.append(str(e))

            done += 1

            # Rate limit the progress updates to save resources
            if done % 50 == 0:
                _progress(int(50 + (done / count * 50.0)), "Copying homedirs")

    logfile = os.path.join(os.path.dirname(target), 'backup.log')
    with open(logfile, 'a') as handle:
        handle.write('*** Copy homedir contents ***\n')
        for error in errors:
            handle.write(f'{error}\n')


def removeprefix(data, prefix):
    if data.startswith(prefix):
        return data[len(prefix):]
    return data


def restore_packages(source, restore_sideloaded=True, cross_branch=False):
    _progress(50, "Restoring packages")

    # Don't restore the repositories file when the backup is for another branch since that
    # will cause a dist-upgrade/downgrade on running apk fix
    if not cross_branch:
        shutil.copyfile(os.path.join(source, 'state/repositories'), '/etc/apk/repositories')

    worldfile = os.path.join(source, 'state/world')
    pkgs = []

    with open('/etc/apk/world') as handle:
        # Read existing device-* packages
        for line in handle.readlines():
            if line.startswith('device-'):
                pkgs.append(line.strip())

    with open(worldfile) as handle:
        for line in handle.readlines():
            # Skip sideloaded packages if not requested
            if '><' in line and not restore_sideloaded:
                continue

            # Don't copy over the device package in case
            # it's a different device the backup is from
            if line.startswith('device-'):
                continue
            pkgs.append(line.strip())

    with open('/etc/apk/world', 'w') as handle:
        handle.write('\n'.join(pkgs))

    if restore_sideloaded:
        shutil.copytree(os.path.join(source, 'state/cache'), '/etc/apk/cache',
                        dirs_exist_ok=True)

    subprocess.run(['apk', 'fix'])


def classify(path):
    if path == 'etc/os-release':
        return None
    elif path.startswith('etc/apk/cache'):
        return 'sideloaded'
    elif path.startswith('etc/apk'):
        return 'packages'
    elif path.startswith('etc/'):
        if path.startswith('etc/NetworkManager') or path.startswith('etc/wireguard'):
            return 'config.networks'
        if path in ['etc/passwd', 'etc/group', 'etc/shadow']:
            return 'config.accounts'
        else:
            return 'config.other'
    elif path.startswith('home/') or path.startswith('root/'):
        if path.startswith('root/'):
            return 'homedir.root'
        else:
            part = path.split('/', maxsplit=2)
            return 'homedir.' + part[1]
    return 'system'


def get_archive_info(filename):
    contents = {}
    size = {}
    with tarfile.open(filename, 'r:gz', errorlevel=2) as tgz:
        for fi in tgz:
            cat = classify(fi.name)
            if cat:
                if cat not in contents:
                    contents[cat] = []
                    size[cat] = 0
                contents[cat].append(fi.name)
                size[cat] += fi.size
    return size, contents


def restore(filename, filter, skip_repositories=False):
    errors = []
    size, contents = get_archive_info(filename)
    total_bytes = 0
    current_bytes = 0
    last_bytes = 0
    for key in size:
        if key in filter:
            total_bytes += size[key]

    with tarfile.open(filename, 'r:gz', errorlevel=2) as tgz:
        for fi in tgz:
            try:
                # Never overwrite the distro release info
                if fi.name == "etc/os-release":
                    continue

                cat = classify(fi.name)
                if cat in filter:

                    if cat in ['packages', 'sideloaded']:
                        if fi.name == 'etc/apk/world':
                            pkgs = []
                            sideloaded = 'sideloaded' in filter
                            with open('/etc/apk/world') as handle:
                                for line in handle.readlines():
                                    if line.startswith('device-'):
                                        pkgs.append(line.strip())

                            world = tgz.extractfile(fi).read()
                            for line in world.splitlines():
                                if '><' in line and not sideloaded:
                                    continue
                                if line.startswith('device-'):
                                    continue
                                pkgs.append(line.strip())

                            with open('/etc/apk/world', 'w') as handle:
                                handle.write('\n'.join(pkgs))
                        elif fi.name == 'etc/apk/repositories' and skip_repositories:
                            pass
                        else:
                            tgz.extract(fi, "/")
                    else:
                        tgz.extract(fi, "/")
                    current_bytes += fi.size
                    if current_bytes - last_bytes > 1024 * 1024:
                        _progress(current_bytes / total_bytes * 100, "Restoring backup")
                        last_bytes = current_bytes

            except Exception as e:
                errors.append(e)
    if 'packages' in filter:
        _progress(99, "Running package manager")
        subprocess.run(['apk', 'fix'])


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


def main(version):
    global _progress_json
    import argparse

    parser = argparse.ArgumentParser(description="postmarketOS backup utility backend")
    parser.add_argument("target", help="Target/source .tar.gz for the backup")
    parser.add_argument("--measure", help="Measure backup size instead of storing it",
                        action="store_true")
    parser.add_argument("--restore", help="Restore instead of backup",
                        action="store_true")
    parser.add_argument("--show", help="Show the contents of a backup file",
                        action="store_true")
    parser.add_argument("--json", help="Output json progress", action="store_true")

    # Options to speed up backup, everything defaults to true to ensure you'll get a
    # usable complete backup if you don't read the instructions. Most of these steps
    # only add a few kb of storage and have a time impact of <1min. Only the homedir
    # option really affects the backup size and time.
    parser.add_argument("--no-config", help="Don't backup /etc changes",
                        action="store_false", dest="config")
    parser.add_argument("--no-system", help="Don't backup /usr changes",
                        action="store_false", dest="system")
    parser.add_argument("--no-homedirs", help="Don't backup /home",
                        action="store_false", dest="homedir")
    parser.add_argument("--no-apks", help="Don't backup sideloaded apks",
                        action="store_false", dest="apks")
    parser.add_argument("--cross-branch", help="Don't restore the repositories file",
                        action="store_true", dest="cross_branch")
    parser.add_argument("--filter", help="Custom restore filter",
                        action="append")

    args = parser.parse_args()

    if args.json:
        _progress_json = True
    if args.show:
        size, contents = get_archive_info(args.target)
        tree = {}
        keys = (size.keys())
        for key in keys:
            if '.' in key:
                key, _ = key.split('.', maxsplit=1)
            if key not in tree:
                tree[key] = []
        for key in sorted(keys):
            if '.' not in key:
                continue
            skey = key
            key, subkey = key.split('.', maxsplit=1)
            tree[key].append(subkey)
            if key not in contents:
                contents[key] = []
                size[key] = 0
            contents[key].extend(contents[skey])
            size[key] += size[skey]
        for key in tree:
            print(f'{key} | {len(contents[key])} files | {sizeof_fmt(size[key])}')
            for subkey in tree[key]:
                skey = f'{key}.{subkey}'
                print(f'    {subkey} | {len(contents[skey])} files | {sizeof_fmt(size[skey])}')
    elif args.restore:
        restore(args.target, args.filter, args.cross_branch)
    else:
        tgz = save_system_state(args.target, version, args.measure, args.config, args.system,
                                args.apks, args.homedir)
        if args.homedir:
            save_homedirs(args.target, tgz)

        if not isinstance(tgz, dict):
            tgz.close()


if __name__ == '__main__':
    main(None)
