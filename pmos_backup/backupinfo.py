import tarfile


def get_info(filename):
    with tarfile.open(filename, 'r:gz') as tgz:
        headers = tgz.pax_headers
    headers['version'] = 'Aha'
    return headers


if __name__ == '__main__':
    import platform

    headers = get_info('/workspace/test.backup.tar.gz')
    arch = platform.machine()
    if arch != headers['arch']:
        print("Invalid architecture")
