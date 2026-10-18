"""Explicit publication inventory. Never publish the whole artifacts directory."""
import json
import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent


def release_files():
    manifest = json.loads((ROOT / 'release-files.json').read_text(encoding='utf-8'))
    files = [ROOT / name for name in manifest['root_files']]
    for name in manifest['source_directories']:
        files.extend(p for p in (ROOT / name).rglob('*') if p.is_file() and
                     p.suffix in manifest['allowed_suffixes'] and '__pycache__' not in p.parts)
    for path in files:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('发布文件路径不合法：' + str(path))
    return sorted(set(files))


if __name__ == '__main__':
    for path in release_files(): print(path.relative_to(ROOT).as_posix())
