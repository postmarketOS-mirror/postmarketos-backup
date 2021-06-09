import os
import sys
import subprocess
import shutil
import glob
import json
import pathlib


_progress_json = False

def _progress(value, label):
    if _progress_json:
        print(json.dumps({"progress": value, "label": label})) 
        sys.stdout.flush()
    else:
        sys.stderr.write(label + "\n")


def parse_apk_cache():
    result = {}
    for path in glob.glob('/etc/apk/cache/*.apk'):
        fname = os.path.basename(path)
        pkgname = '-'.join(fname.split('-')[0:-2])
        if pkgname not in result:
            result[pkgname] = []
        result[pkgname].append(path)
    return result


def save_system_state(target, measure=False, do_config=True, do_system=True, do_apks=True, do_homedirs=True):
    pscale = 1
    if not do_homedirs:
        pscale = 2
    errors = []
    if not measure:
        if not os.path.isdir(target):
            os.makedirs(target)
    statedir = os.path.join(target, 'state')
    if not measure:
        os.makedirs(statedir)

        # Copy over the apk state and some metadata about the installation
        _progress(10 * pscale, 'Copying metadata')
        shutil.copyfile('/etc/apk/world', os.path.join(statedir, 'world'))
        shutil.copyfile('/etc/apk/repositories', os.path.join(statedir, 'repositories'))
        shutil.copyfile('/etc/os-release', os.path.join(statedir, 'os-release'))

    # Check for modified config
    config_size = 0
    if do_config:
        _progress(20 * pscale, "Checking modified config")
        modified_config = subprocess.check_output(['apk', 'audit', '--backup'], universal_newlines=True)
        for line in modified_config.splitlines():
            state, path = line.split(' ', maxsplit=1)
            if state in ['A', 'U']:
                source = os.path.join('/', path)
                targetf = os.path.join(statedir, path)
                if measure:
                    if os.path.exists(source):
                        config_size += os.stat(source).st_size
                else:
                    if not os.path.isdir(os.path.dirname(targetf)):
                        os.makedirs(os.path.dirname(targetf))
                    shutil.copyfile(source, targetf, follow_symlinks=False)

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
                    targetf = os.path.join(statedir, path)
                    if not os.path.isdir(os.path.dirname(targetf)):
                        os.makedirs(os.path.dirname(targetf))
                    shutil.copyfile(source, targetf, follow_symlinks=False)

    # Try to get sideloaded apks from the apk cache. This is not perfect yet since we can't match
    # up the version hash in the world file to the exact .apk file that was installed since the
    # algorithm is not known. The workaround is copying all the apks from the cache for the same
    # pkgname.
    cache_size = 0
    if do_apks:
        _progress(40 * pscale, "Copying sideloaded packages")
        apk_cache = parse_apk_cache()
        measure or os.makedirs(os.path.join(statedir, 'cache'))
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
                            shutil.copyfile(path, os.path.join(statedir, 'cache',
                                                               os.path.basename(path)))

    if measure:
        return {
            "errors": errors,
            "config": config_size,
            "system": system_size,
            "cache": cache_size,
        }
    else:
        logfile = os.path.join(target, 'backup.log')
        with open(logfile, 'a') as handle:
            handle.write('*** Copy system state ***\n')
            for error in errors:
                handle.write(f'{error}\n')
        return {
            "errors": errors
        }


def save_homedirs(target):
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

        target_dir = os.path.join(target, root[1:])
        os.makedirs(target_dir)

        for fname in files:
            path = os.path.join(root, fname)
            try:
                shutil.copyfile(path, os.path.join(target_dir, fname), follow_symlinks=False)
            except Exception as e:
                errors.append(str(e))

            done += 1

            # Rate limit the progress updates to save resources
            if done % 50 == 0:
                _progress(int(50 + (done / count * 50.0)), "Copying homedirs")

    logfile = os.path.join(target, 'backup.log')
    with open(logfile, 'a') as handle:
        handle.write('*** Copy homedir contents ***\n')
        for error in errors:
            handle.write(f'{error}\n')


def write_final_metadata(target, version):
    _progress(100, "Writing final metadata")

    # Use rhash to generate a sha1sum compatible checksums file of the entire backup
    hashes = subprocess.run(['rhash', '--sha1', '-r', '.'], cwd=target,
                                    universal_newlines=True, stdout=subprocess.PIPE)
    hashfile = os.path.join(target, 'checksums.sha1')
    with open(hashfile, 'w') as handle:
        handle.write(hashes.stdout)

    # Save backup metadata in a file for quick access in the GUI
    size = subprocess.check_output(['du', '-sh', target], universal_newlines=True)
    size, path = size.split('\t', maxsplit=1)

    with open('/etc/apk/arch') as handle:
        arch = handle.read().strip()

    metadata = {
        "label": os.path.basename(target),
        "size": size,
        "version": version,
        "arch": arch
    }

    metafile = os.path.join(target, 'metadata.json')
    with open(metafile, 'w') as handle:
        handle.write(json.dumps(metadata))


def removeprefix(data, prefix):
    if data.startswith(prefox):
        return data[len(prefix):]
    return data


def restore_config(source):
    _progress(10, "Restoring global config")
    prefix = os.path.join(source, 'state/etc')
    for path in glob.glob(os.path.join(prefix, '*')):
        target_path = os.path.join('/etc', removeprefix(path, prefix))
        os.makedirs(os.path.dirname(target_path))
        shutil.copyfile(path, target_path, follow_symlinks=False)


def restore_system(source):
    _progress(20, "Restoring system files")
    dirs = list(glob.glob(os.path.join(source, 'state/*/')))
    dirs = list(map(os.path.dirname, dirs))
    dirs = list(map(os.path.basename, dirs))
    dirs = filter(lambda x: x not in ['etc', 'cache'], dirs)

    for state_dir in dirs:
        prefix = os.path.join(source, 'state', state_dir)
        for path in glob.glob(os.path.join(prefix, '*')):
            target_path = os.path.join('/', state_dir, removeprefix(path, prefix))
            os.makedirs(os.path.dirname(target_path))
            shutil.copyfile(path, target_path, follow_symlinks=False)


def restore_packages(source, restore_sideloaded=True, cross_branch=False):
    _progress(50, "Restoring packages")

    # Don't restore the repositories file when the backup is for another branch since that
    # will cause a dist-upgrade/downgrade on running apk fix
    if not cross_branch:
        shutil.copyfile(os.path.join(source, 'state/repositories'), '/etc/apk/repositories')

    worldfile = os.path.join(source, 'state/world')
    if restore_sideloaded:
        shutil.copyfile(worldfile, '/etc/apk/world')
        shutil.copytree(os.path.join(source, 'state/cache'), '/etc/apk/cache',
                dirs_exist_ok=True)
    else:
        pkgs = []
        with open(worldfile) as handle:
            for line in handle.readlines():
                if '><' not in line:
                    pkgs.append(line.strip())
        with open('/etc/apk/world', 'w') as handle:
            handle.write('\n'.join(pkgs))

    subprocess.run(['apk', 'fix'])


def restore_homedirs(source):
    errors = []
    _progress(50, "Copying homedirs")

    # Count the total files for progress calculations
    count = 0
    for root, dirs, files in os.walk(os.path.join(source, 'home')):
        for fname in files:
            count += 1
            
    # Do the actual copy
    done = 0
    for root, dirs, files in os.walk(os.path.join(source, 'home'), topdown=True):
        target_dir = removeprefix(root, source)
        os.makedirs(target_dir)

        for fname in files:
            path = os.path.join(root, fname)
            try:
                shutil.copyfile(path, os.path.join(target_dir, fname), follow_symlinks=False)
            except Exception as e:
                errors.append(str(e))

            done += 1

            # Rate limit the progress updates to save resources
            if done % 50 == 0:
                _progress(int(50 + (done / count * 50.0)), "Copying homedirs")


def main(version):
    global _progress_json
    import argparse

    parser = argparse.ArgumentParser(description="postmarketOS backup utility backend")
    parser.add_argument("target", help="Target/source directory for the backup")
    parser.add_argument("--measure", help="Measure backup size instead of storing it", 
            action="store_true")
    parser.add_argument("--restore", help="Restore instead of backup",
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
    parser.add_argument("--no-pkgs", help="Don't restore packages (unused in backup)",
            action="store_false", dest="pkgs")
    parser.add_argument("--cross-branch", help="Don't restore the repositories file",
            action="store_true", dest="cross_branch")

    args = parser.parse_args()
    
    if args.json:
        _progress_json = True

    if args.restore:
        if args.config:
            restore_config(args.target)
        if args.system:
            restore_system(args.target)
        if args.pkgs:
            restore_packages(args.target, args.apks, args.cross_branch)
        if args.homedir:
            restore_homedirs(args.target)
    else:
        save_system_state(args.target, args.measure, args.config, args.system,
                          args.apks, args.homedir)
        if args.homedir:
            save_homedirs(args.target)

        write_final_metadata(args.target, version)


if __name__ == '__main__':
    main(None)
