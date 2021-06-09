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
                target = os.path.join(statedir, path)
                if measure:
                    if os.path.exists(source):
                        config_size += os.stat(source).st_size
                else:
                    if not os.path.isdir(os.path.dirname(target)):
                        os.makedirs(os.path.dirname(target))
                    shutil.copyfile(source, target, follow_symlinks=False)

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
                    target = os.path.join(statedir, path)
                    if not os.path.isdir(os.path.dirname(target)):
                        os.makedirs(os.path.dirname(target))
                    shutil.copyfile(source, target, follow_symlinks=False)

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


def main():
    global _progress_json
    import argparse

    parser = argparse.ArgumentParser(description="postmarketOS backup utility backend")
    parser.add_argument("target", help="Target directory for the backup")
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
    parser.add_argument("--no-homedir", help="Don't backup /home",
            action="store_false", dest="homedir")
    parser.add_argument("--no-apks", help="Don't backup sideloaded apks",
            action="store_false", dest="apks")

    args = parser.parse_args()
    
    if args.json:
        _progress_json = True

    if args.restore:
        print("TODO")
    else:
        save_system_state(args.target, args.measure, args.config, args.system,
                          args.apks, args.homedir)
        if args.homedir:
            save_homedirs(args.target)


if __name__ == '__main__':
    main()
