#!/usr/bin/env python3
"""A (QoL) mode loader for Kaleidoloop.

Swaps modes between the active modes/ directory and others/.

Usage:
    ./load              Interactive mode — lists available swaps and prompts.
    ./load X Y          Swap mode numbered X (from others/) into position Y (in modes/).
    ./load restore      Restore factory mode layout from manifest.
    ./load mirror DIR   Copy active modes (1-6) to DIR on a mounted device.

The numbering scheme: each mode directory is prefixed with its slot number,
e.g. "6-delay". When swapping, the incoming mode takes the target's slot number
and the displaced mode takes the source's slot number.
"""

import os
import re
import shutil
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
MODES_DIR = os.path.join(PROJECT_DIR, "pd", "modes")
OTHERS_DIR = os.path.join(MODES_DIR, "others")
FACTORY_MANIFEST = os.path.join(MODES_DIR, "factory.manifest")


def parse_mode_dir(dirname):
    """Parse a mode directory name into (number, name).

    Pre: dirname is a string like "6-delay" or "7-just-play".
    Post: returns (int, str) tuple of the slot number and mode name.

    >>> parse_mode_dir("6-delay")
    (6, 'delay')
    >>> parse_mode_dir("7-just-play")
    (7, 'just-play')
    >>> parse_mode_dir("12-something-long")
    (12, 'something-long')
    >>> parse_mode_dir("no-number")
    """
    m = re.match(r"^(\d+)-(.+)$", dirname)
    if not m:
        return None
    return (int(m.group(1)), m.group(2))


def list_modes(modes_dir):
    """List numbered mode directories in a given directory.

    Pre: modes_dir exists and contains subdirectories with numbered prefixes.
    Post: returns sorted list of (number, name, full_path) tuples.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> os.makedirs(os.path.join(d, "1-alpha"))
    >>> os.makedirs(os.path.join(d, "3-gamma"))
    >>> os.makedirs(os.path.join(d, "2-beta"))
    >>> os.makedirs(os.path.join(d, "not-a-mode"))
    >>> result = list_modes(d)
    >>> [(n, name) for n, name, _ in result]
    [(1, 'alpha'), (2, 'beta'), (3, 'gamma')]
    >>> shutil.rmtree(d)
    """
    if not os.path.isdir(modes_dir):
        return []
    entries = []
    for entry in os.listdir(modes_dir):
        full = os.path.join(modes_dir, entry)
        if not os.path.isdir(full):
            continue
        parsed = parse_mode_dir(entry)
        if parsed:
            entries.append((parsed[0], parsed[1], full))
    entries.sort(key=lambda x: x[0])
    return entries


def find_mode_by_number(modes_dir, number):
    """Find a mode by its slot number in a directory.

    Pre: number is an int, modes_dir is a valid directory path.
    Post: returns (name, full_path) if exactly one match, None otherwise.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> os.makedirs(os.path.join(d, "6-delay"))
    >>> find_mode_by_number(d, 6)  # doctest: +ELLIPSIS
    ('delay', '...6-delay')
    >>> find_mode_by_number(d, 99) is None
    True
    >>> shutil.rmtree(d)
    """
    matches = [(name, path) for num, name, path in list_modes(modes_dir) if num == number]
    if len(matches) == 1:
        return matches[0]
    return None


def check_conflicts(modes_dir, others_dir):
    """Detect duplicate slot numbers within or across directories.

    Pre: both directories exist.
    Post: returns list of conflict description strings. Empty means no conflicts.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "1-alpha"))
    >>> os.makedirs(os.path.join(m, "1-beta"))
    >>> conflicts = check_conflicts(m, o)
    >>> len(conflicts) > 0
    True
    >>> 'modes' in conflicts[0] and '1' in conflicts[0]
    True
    >>> shutil.rmtree(d)

    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "1-alpha"))
    >>> os.makedirs(os.path.join(o, "1-beta"))
    >>> conflicts = check_conflicts(m, o)
    >>> len(conflicts) > 0
    True
    >>> shutil.rmtree(d)

    No conflicts when slots are disjoint:

    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "1-alpha"))
    >>> os.makedirs(os.path.join(o, "2-beta"))
    >>> check_conflicts(m, o)
    []
    >>> shutil.rmtree(d)
    """
    conflicts = []
    for label, directory in [("modes", modes_dir), ("others", others_dir)]:
        seen = {}
        for num, name, path in list_modes(directory):
            if num in seen:
                conflicts.append(
                    f"Duplicate slot {num} in {label}/: "
                    f"{num}-{seen[num]} and {num}-{name}"
                )
            seen[num] = name

    active_nums = {num for num, _, _ in list_modes(modes_dir)}
    other_nums = {num for num, _, _ in list_modes(others_dir)}
    overlap = active_nums & other_nums
    for num in sorted(overlap):
        conflicts.append(
            f"Slot {num} exists in both modes/ and others/"
        )

    return conflicts


def swap_mode(source_num, target_num, modes_dir, others_dir):
    """Swap a mode from others/ into an active slot.

    Pre: source_num exists in others_dir, target_num exists in modes_dir,
         no conflicts detected.
    Post: the mode from others/ is now in modes/ with the target slot number,
          and the displaced mode is in others/ with the source slot number.
          Returns (moved_in, moved_out) description tuple.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "6-delay"))
    >>> open(os.path.join(m, "6-delay", "module.pd"), "w").close()
    >>> os.makedirs(os.path.join(o, "7-just-play"))
    >>> open(os.path.join(o, "7-just-play", "module.pd"), "w").close()
    >>> moved_in, moved_out = swap_mode(7, 6, m, o)
    >>> moved_in
    '6-just-play'
    >>> moved_out
    '7-delay'
    >>> os.path.isdir(os.path.join(m, "6-just-play"))
    True
    >>> os.path.isdir(os.path.join(o, "7-delay"))
    True
    >>> os.path.isdir(os.path.join(m, "6-delay"))
    False
    >>> os.path.isdir(os.path.join(o, "7-just-play"))
    False
    >>> shutil.rmtree(d)

    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "1-alpha"))
    >>> try:
    ...     swap_mode(99, 1, m, o)
    ... except ValueError as e:
    ...     'slot 99' in str(e)
    True
    >>> os.makedirs(os.path.join(o, "2-beta"))
    >>> try:
    ...     swap_mode(2, 99, m, o)
    ... except ValueError as e:
    ...     'slot 99' in str(e)
    True
    >>> shutil.rmtree(d)
    """
    conflicts = check_conflicts(modes_dir, others_dir)
    if conflicts:
        raise RuntimeError("Conflicts detected:\n" + "\n".join(conflicts))

    source = find_mode_by_number(others_dir, source_num)
    if source is None:
        raise ValueError(f"No mode with slot {source_num} found in others/")

    target = find_mode_by_number(modes_dir, target_num)
    if target is None:
        raise ValueError(f"No mode with slot {target_num} found in modes/")

    source_name, source_path = source
    target_name, target_path = target

    parent = os.path.dirname(modes_dir)
    staging = tempfile.mkdtemp(dir=parent, prefix=".swap-")
    completed = []
    try:
        staged_target = os.path.join(staging, f"{source_num}-{target_name}")
        staged_source = os.path.join(staging, f"{target_num}-{source_name}")

        shutil.move(target_path, staged_target)
        completed.append(("target_to_staging", target_path, staged_target))

        shutil.move(source_path, staged_source)
        completed.append(("source_to_staging", source_path, staged_source))

        final_source = os.path.join(modes_dir, f"{target_num}-{source_name}")
        shutil.move(staged_source, final_source)
        completed.append(("staging_to_modes", staged_source, final_source))

        final_target = os.path.join(others_dir, f"{source_num}-{target_name}")
        shutil.move(staged_target, final_target)
        completed.append(("staging_to_others", staged_target, final_target))
    except Exception:
        for step, orig, moved in reversed(completed):
            if os.path.isdir(moved):
                shutil.move(moved, orig)
        raise
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging)

    return (f"{target_num}-{source_name}", f"{source_num}-{target_name}")


def parse_manifest(manifest_path):
    """Parse a factory manifest file.

    Pre: manifest_path points to a file with lines like "modes/1-smooth-vibrato".
    Post: returns dict mapping "modes" or "others" to list of directory basenames.

    >>> import tempfile, os
    >>> f = tempfile.NamedTemporaryFile(mode='w', suffix='.manifest', delete=False)
    >>> _ = f.write("modes/1-alpha\\nmodes/2-beta\\nothers/7-gamma\\n")
    >>> f.close()
    >>> result = parse_manifest(f.name)
    >>> result['modes']
    ['1-alpha', '2-beta']
    >>> result['others']
    ['7-gamma']
    >>> os.unlink(f.name)
    """
    layout = {"modes": [], "others": []}
    with open(manifest_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("modes/"):
                layout["modes"].append(line[len("modes/"):])
            elif line.startswith("others/"):
                layout["others"].append(line[len("others/"):])
    return layout


def restore_factory(modes_dir, others_dir, manifest_path):
    """Restore the factory mode layout from manifest.

    Pre: manifest_path exists and describes the factory layout.
         modes_dir and others_dir exist.
    Post: manifest modes are moved to their factory locations.
          Non-manifest modes are left untouched.
          Returns (restored_count, already_ok_count, missing_names) tuple.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "6-just-play"))
    >>> open(os.path.join(m, "6-just-play", "module.pd"), "w").close()
    >>> os.makedirs(os.path.join(o, "7-delay"))
    >>> open(os.path.join(o, "7-delay", "module.pd"), "w").close()
    >>> mf = os.path.join(d, "factory.manifest")
    >>> with open(mf, "w") as f:
    ...     _ = f.write("modes/6-delay\\nothers/7-just-play\\n")
    >>> restored, ok, missing = restore_factory(m, o, mf)
    >>> restored > 0
    True
    >>> len(missing)
    0
    >>> os.path.isdir(os.path.join(m, "6-delay"))
    True
    >>> os.path.isdir(os.path.join(o, "7-just-play"))
    True
    >>> shutil.rmtree(d)

    Already-correct state returns all in already_ok:

    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> os.makedirs(os.path.join(m, "1-alpha"))
    >>> os.makedirs(os.path.join(o, "2-beta"))
    >>> mf = os.path.join(d, "factory.manifest")
    >>> with open(mf, "w") as f:
    ...     _ = f.write("modes/1-alpha\\nothers/2-beta\\n")
    >>> restored, ok, missing = restore_factory(m, o, mf)
    >>> restored
    0
    >>> ok
    2
    >>> shutil.rmtree(d)

    Missing mode on disk is reported:

    >>> d = tempfile.mkdtemp()
    >>> m = os.path.join(d, "modes"); os.makedirs(m)
    >>> o = os.path.join(d, "others"); os.makedirs(o)
    >>> mf = os.path.join(d, "factory.manifest")
    >>> with open(mf, "w") as f:
    ...     _ = f.write("modes/1-gone\\n")
    >>> restored, ok, missing = restore_factory(m, o, mf)
    >>> missing
    ['gone']
    >>> shutil.rmtree(d)
    """
    layout = parse_manifest(manifest_path)
    restored = 0
    already_ok = 0
    missing = []

    all_modes = {}
    dupes = []
    for num, name, path in list_modes(modes_dir):
        all_modes[name] = ("modes", num, path)
    for num, name, path in list_modes(others_dir):
        if name in all_modes:
            dupes.append(name)
        all_modes[name] = ("others", num, path)
    if dupes:
        raise RuntimeError(
            "Duplicate mode names across directories: " + ", ".join(dupes)
        )

    for section, target_dir in [("modes", modes_dir), ("others", others_dir)]:
        for dirname in layout[section]:
            parsed = parse_mode_dir(dirname)
            if not parsed:
                continue
            target_num, target_name = parsed
            dest = os.path.join(target_dir, dirname)
            if os.path.isdir(dest):
                already_ok += 1
                continue
            if target_name not in all_modes:
                missing.append(target_name)
                continue
            _, _, current_path = all_modes[target_name]
            if os.path.isdir(current_path):
                shutil.move(current_path, dest)
                restored += 1
            else:
                missing.append(target_name)

    return (restored, already_ok, missing)


def mirror_modes(modes_dir, target_dir):
    """Copy active modes to a target directory (e.g. mounted device).

    Pre: modes_dir contains the active mode directories.
         target_dir exists and is writable.
    Post: each numbered mode directory from modes_dir is copied to target_dir,
          replacing any existing directory with the same name.
          Returns count of modes copied.

    >>> import tempfile, os
    >>> src = tempfile.mkdtemp()
    >>> dst = tempfile.mkdtemp()
    >>> os.makedirs(os.path.join(src, "1-alpha"))
    >>> open(os.path.join(src, "1-alpha", "module.pd"), "w").close()
    >>> os.makedirs(os.path.join(src, "2-beta"))
    >>> open(os.path.join(src, "2-beta", "module.pd"), "w").close()
    >>> os.makedirs(os.path.join(src, "not-a-mode"))
    >>> mirror_modes(src, dst)
    2
    >>> os.path.isdir(os.path.join(dst, "1-alpha"))
    True
    >>> os.path.isfile(os.path.join(dst, "1-alpha", "module.pd"))
    True
    >>> os.path.isdir(os.path.join(dst, "not-a-mode"))
    False
    >>> shutil.rmtree(src)
    >>> shutil.rmtree(dst)

    Raises on non-existent target:

    >>> try:
    ...     mirror_modes("/tmp", "/no/such/path")
    ... except ValueError as e:
    ...     'does not exist' in str(e)
    True

    Fails on non-empty target (caller must clear first):

    >>> src = tempfile.mkdtemp()
    >>> dst = tempfile.mkdtemp()
    >>> os.makedirs(os.path.join(src, "1-alpha"))
    >>> os.makedirs(os.path.join(dst, "old-stuff"))
    >>> try:
    ...     mirror_modes(src, dst)
    ... except ValueError as e:
    ...     'not empty' in str(e)
    True
    >>> shutil.rmtree(src)
    >>> shutil.rmtree(dst)
    """
    if not os.path.isdir(target_dir):
        raise ValueError(f"Target directory does not exist: {target_dir}")
    if os.listdir(target_dir):
        raise ValueError(f"Target directory is not empty: {target_dir}")

    modes = list_modes(modes_dir)
    for num, name, src_path in modes:
        dirname = os.path.basename(src_path)
        dst_path = os.path.join(target_dir, dirname)
        shutil.copytree(src_path, dst_path)

    return len(modes)


def print_status(modes_dir, others_dir):
    """Print current mode layout."""
    print("Active modes:")
    for num, name, _ in list_modes(modes_dir):
        print(f"  {num} - {name}")
    print()
    others = list_modes(others_dir)
    if others:
        print("Available in others/:")
        for num, name, _ in others:
            print(f"  {num} - {name}")
    else:
        print("No modes in others/.")
    print()


def interactive_mode(modes_dir, others_dir):
    """Run interactive swap prompt."""
    print_status(modes_dir, others_dir)

    others = list_modes(others_dir)
    if not others:
        print("Nothing to swap — others/ is empty.")
        return

    conflicts = check_conflicts(modes_dir, others_dir)
    if conflicts:
        print("Cannot swap — conflicts detected:")
        for c in conflicts:
            print(f"  {c}")
        return

    try:
        source = input("Mode number to load (from others/): ").strip()
        target = input("Slot number to replace (in modes/): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if not source.isdigit() or not target.isdigit():
        print("Error: both arguments must be numbers.")
        return

    source_num, target_num = int(source), int(target)
    try:
        moved_in, moved_out = swap_mode(source_num, target_num, modes_dir, others_dir)
        print(f"Swapped: modes/{moved_in} <-> others/{moved_out}")
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}")


def main():
    if not os.path.isdir(MODES_DIR):
        print(f"Error: modes directory not found at {MODES_DIR}")
        sys.exit(1)

    if not os.path.isdir(OTHERS_DIR):
        os.makedirs(OTHERS_DIR)

    if len(sys.argv) == 1:
        interactive_mode(MODES_DIR, OTHERS_DIR)
        return

    if sys.argv[1] == "mirror":
        if len(sys.argv) != 3:
            print("Error: 'mirror' requires a target directory.")
            print("Usage: ./load mirror /path/to/mounted/pd/modes")
            sys.exit(1)
        target = sys.argv[2]
        if not os.path.isdir(target):
            print(f"Error: target directory not found: {target}")
            sys.exit(1)
        if os.listdir(target):
            print(f"Target directory is not empty: {target}")
            try:
                answer = input("Clear and mirror? (y/n): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(1)
            if answer != "y":
                print("Aborted.")
                sys.exit(0)
            for entry in os.listdir(target):
                path = os.path.join(target, entry)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        count = mirror_modes(MODES_DIR, target)
        print(f"Mirrored {count} mode(s) to {target}")
        return

    if sys.argv[1] == "restore":
        if len(sys.argv) > 2:
            print("Error: 'restore' takes no additional arguments.")
            sys.exit(1)
        if not os.path.isfile(FACTORY_MANIFEST):
            print(f"Error: factory manifest not found at {FACTORY_MANIFEST}")
            sys.exit(1)
        conflicts = check_conflicts(MODES_DIR, OTHERS_DIR)
        if conflicts:
            print("Cannot restore — conflicts detected:")
            for c in conflicts:
                print(f"  {c}")
            sys.exit(1)
        restored, ok, missing = restore_factory(MODES_DIR, OTHERS_DIR, FACTORY_MANIFEST)
        print(f"Factory restored. {restored} mode(s) moved, {ok} already in place.")
        if missing:
            print(f"Warning: {len(missing)} mode(s) not found on disk: {', '.join(missing)}")
        print_status(MODES_DIR, OTHERS_DIR)
        return

    if len(sys.argv) == 3:
        try:
            source_num = int(sys.argv[1])
            target_num = int(sys.argv[2])
        except ValueError:
            print("Error: arguments must be numbers or 'restore'.")
            print(__doc__)
            sys.exit(1)

        conflicts = check_conflicts(MODES_DIR, OTHERS_DIR)
        if conflicts:
            print("Cannot swap — conflicts detected:")
            for c in conflicts:
                print(f"  {c}")
            sys.exit(1)

        try:
            moved_in, moved_out = swap_mode(source_num, target_num, MODES_DIR, OTHERS_DIR)
            print(f"Swapped: modes/{moved_in} <-> others/{moved_out}")
        except (ValueError, RuntimeError) as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main()
