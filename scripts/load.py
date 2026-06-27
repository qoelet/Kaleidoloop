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
import subprocess
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

    Excludes macOS externals from the mirror:

    >>> src = tempfile.mkdtemp()
    >>> dst = tempfile.mkdtemp()
    >>> os.makedirs(os.path.join(src, "6-delay"))
    >>> open(os.path.join(src, "6-delay", "waveplayer~.pd_linux"), "w").close()
    >>> open(os.path.join(src, "6-delay", "waveplayer~.pd_darwin"), "w").close()
    >>> mirror_modes(src, dst)
    1
    >>> os.path.exists(os.path.join(dst, "6-delay", "waveplayer~.pd_linux"))
    True
    >>> os.path.exists(os.path.join(dst, "6-delay", "waveplayer~.pd_darwin"))
    False
    >>> shutil.rmtree(src); shutil.rmtree(dst)
    """
    if not os.path.isdir(target_dir):
        raise ValueError(f"Target directory does not exist: {target_dir}")
    if os.listdir(target_dir):
        raise ValueError(f"Target directory is not empty: {target_dir}")

    modes = list_modes(modes_dir)
    for num, name, src_path in modes:
        dirname = os.path.basename(src_path)
        dst_path = os.path.join(target_dir, dirname)
        shutil.copytree(src_path, dst_path,
                        ignore=shutil.ignore_patterns("*.pd_darwin"))

    return len(modes)


def darwin_dest(project_dir):
    """Return the path where the Mac waveplayer~ external is placed.

    Pre: project_dir is the repo root.
    Post: returns <project_dir>/pd/lib/waveplayer~.pd_darwin.

    >>> darwin_dest("/repo").replace("\\\\", "/")
    '/repo/pd/lib/waveplayer~.pd_darwin'
    """
    return os.path.join(project_dir, "pd", "lib", "waveplayer~.pd_darwin")


def _module_dirs(project_dir):
    """All mode folders under pd/modes/ and pd/modes/others/."""
    modes_dir = os.path.join(project_dir, "pd", "modes")
    others_dir = os.path.join(modes_dir, "others")
    dirs = []
    for d in (modes_dir, others_dir):
        for _num, _name, path in list_modes(d):
            dirs.append(path)
    return dirs


def mac_ext_sources(project_dir):
    """Mac-only replacement abstractions for Linux-only externals.

    Live in pd/dev/mac-ext/ (e.g. tanh~.pd, freeverb~.pd). On macOS Pd loads
    these; on the device the real compiled .pd_linux takes priority, so they do
    not shadow it.
    """
    d = os.path.join(project_dir, "pd", "dev", "mac-ext")
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".pd")]


def _symlink_force(src, dest):
    """Create dest as a relative symlink to src, replacing anything already there."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.islink(dest) or os.path.exists(dest):
        os.remove(dest)
    os.symlink(os.path.relpath(src, os.path.dirname(dest)), dest)


def darwin_link_targets(project_dir):
    """Every path that should symlink to the built waveplayer~.pd_darwin.

    pd/lib (on the search path) plus each mode folder, so a dynamically loaded
    module finds waveplayer~ locally with no reliance on declared search paths
    (which Pd can drop when a patch is saved).
    """
    return [darwin_dest(project_dir)] + [
        os.path.join(path, "waveplayer~.pd_darwin") for path in _module_dirs(project_dir)
    ]


def strip_darwin(project_dir):
    """Remove all macOS-only externals so uploads stay clean.

    Pre: project_dir is the repo root.
    Post: deletes pd/lib/waveplayer~.pd_darwin and any *.pd_darwin under
          pd/modes/. Returns the sorted list of removed paths.

    >>> import tempfile, os
    >>> d = tempfile.mkdtemp()
    >>> os.makedirs(os.path.join(d, "pd", "lib"))
    >>> os.makedirs(os.path.join(d, "pd", "modes", "6-delay"))
    >>> open(darwin_dest(d), "w").close()
    >>> open(os.path.join(d, "pd", "modes", "6-delay", "waveplayer~.pd_darwin"), "w").close()
    >>> removed = strip_darwin(d)
    >>> len(removed)
    2
    >>> os.path.exists(darwin_dest(d))
    False
    >>> shutil.rmtree(d)
    """
    removed = []
    dest = darwin_dest(project_dir)
    if os.path.isfile(dest) or os.path.islink(dest):
        os.remove(dest)
        removed.append(dest)
    ext_names = {os.path.basename(s) for s in mac_ext_sources(project_dir)}
    modes_root = os.path.join(project_dir, "pd", "modes")
    for root, _dirs, files in os.walk(modes_root):
        for fn in files:
            p = os.path.join(root, fn)
            if fn.endswith(".pd_darwin") or (fn in ext_names and os.path.islink(p)):
                os.remove(p)
                removed.append(p)
    return sorted(removed)


def _needs_build(ext_dir, built):
    """True if the external must be (re)built: artifact missing or a source newer.

    >>> import tempfile, os, time
    >>> d = tempfile.mkdtemp()
    >>> built = os.path.join(d, "waveplayer~.pd_darwin")
    >>> _needs_build(d, built)  # artifact missing
    True
    >>> open(built, "w").close()
    >>> src = os.path.join(d, "waveplayer~.c"); open(src, "w").close()
    >>> os.utime(built, (time.time() + 10, time.time() + 10))  # artifact newer
    >>> _needs_build(d, built)
    False
    >>> os.utime(src, (time.time() + 20, time.time() + 20))  # source newer
    >>> _needs_build(d, built)
    True
    >>> shutil.rmtree(d)
    """
    if not os.path.isfile(built):
        return True
    built_mtime = os.path.getmtime(built)
    for name in ("waveplayer~.c", "waveplayer_dsp.h", "Makefile"):
        src = os.path.join(ext_dir, name)
        if os.path.isfile(src) and os.path.getmtime(src) > built_mtime:
            return True
    return False


def place_darwin(project_dir, force=False):
    """Build the Mac waveplayer~ external once and symlink it into pd/lib.

    Pre: external/waveplayer/ contains the source and Makefile.
    Post: builds external/waveplayer/waveplayer~.pd_darwin only if it is
          missing or out of date (or force=True), then points
          pd/lib/waveplayer~.pd_darwin at that single canonical build via a
          relative symlink. Returns (built, dest) where `built` is True if a
          compile actually ran.
    Raises RuntimeError if the build fails or produces no artifact.
    """
    ext_dir = os.path.join(project_dir, "external", "waveplayer")
    built_path = os.path.join(ext_dir, "waveplayer~.pd_darwin")
    did_build = force or _needs_build(ext_dir, built_path)
    if did_build:
        result = subprocess.run(["make", "-C", ext_dir], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError("Build failed:\n" + result.stdout + result.stderr)
    if not os.path.isfile(built_path):
        raise RuntimeError(f"Build produced no artifact at {built_path}")
    targets = darwin_link_targets(project_dir)
    for dest in targets:
        _symlink_force(built_path, dest)
    # Mac-only replacement abstractions (tanh~, freeverb~) into each mode folder
    exts = mac_ext_sources(project_dir)
    n_ext = 0
    for module_dir in _module_dirs(project_dir):
        for src in exts:
            _symlink_force(src, os.path.join(module_dir, os.path.basename(src)))
            n_ext += 1
    return (did_build, len(targets), n_ext)


def generate_bench(project_dir):
    """Regenerate pd/dev/test-bench.pd with a click-to-switch button per mode.

    Pre: pd/modes/ (and pd/modes/others/) hold the mode folders.
    Post: writes pd/dev/test-bench.pd. Each mode is a labelled button that, on
          click, dynamically (re)loads that module into a [pd dut] subpatch at
          slot 1 and routes its audio via throw~/catch~ to dac~. Active modes
          are one row, others the next. loadbang auto-loads the first mode.
          Returns (dest_path, n_buttons).
    """
    modes_dir = os.path.join(project_dir, "pd", "modes")
    others_dir = os.path.join(modes_dir, "others")
    # paths are relative to the bench's own folder (pd/dev), so the bench needs
    # no [declare] lines (which Pd drops on save). waveplayer~ is found because
    # load.py mac symlinks it into each mode folder.
    active = [(f"{n}-{nm}", f"../modes/{n}-{nm}") for n, nm, _ in list_modes(modes_dir)]
    others = [(f"{n}-{nm}", f"../modes/others/{n}-{nm}") for n, nm, _ in list_modes(others_dir)]

    body, conns, counter = [], [], [0]

    def obj(line):
        body.append(line)
        counter[0] += 1
        return counter[0] - 1

    obj("#X obj 20 14 cnv 15 900 28 empty empty Kaleidoloop\\ playback\\ harness 16 11 0 14 #e0e0e0 #000000 0")
    lb = obj("#X obj 40 70 loadbang")
    dspmsg = obj("#X msg 40 110 \\; pd dsp 1")
    obj("#X obj 250 80 hsl 200 18 0 1 0 0 knob1-1 empty knob1 -2 -9 0 10 #fcfcfc #000000 #000000 0 1")
    obj("#X obj 250 130 hsl 200 18 0 1 0 0 knob2-1 empty knob2 -2 -9 0 10 #fcfcfc #000000 #000000 0 1")
    rec = obj("#X obj 540 80 bng 24 250 50 0 empty empty empty 20 7 0 10 #fcfcfc #000000 #000000")
    recmsg = obj("#X msg 580 80 1 64")
    skeys = obj("#X obj 540 130 s keys")
    loadb = obj("#X obj 680 80 bng 24 250 50 0 empty empty empty 20 7 0 10 #fcfcfc #000000 #000000")
    openp = obj("#X obj 680 120 openpanel")
    lp = obj("#X obj 680 150 list prepend open")
    lt = obj("#X obj 680 180 list trim")
    swo = obj("#X obj 680 210 s waveplayer-open")
    catch = obj("#X obj 40 560 catch~ tb-out")
    dac = obj("#X obj 40 600 dac~")
    obj("#X text 248 62 knobs (0-1)")
    obj("#X text 536 62 record")
    obj("#X text 676 62 load wav")
    obj("#X text 20 252 click a mode to load it (always slot 1):")
    obj("#X text 20 314 active:")
    obj("#X text 20 404 others:")

    park_x, park_y, first = 1010, [60], [None]

    def button(label, relpath, x, y):
        b = obj(f"#X obj {x} {y} bng 26 250 50 0 empty empty {label} 30 8 0 9 #fcfcfc #000000 #000000")
        m = obj(
            f"#X msg {park_x} {park_y[0]} \\; pd-dut clear \\; pd-dut obj 40 40 "
            f"{relpath}/module 1 \\; pd-dut obj 40 90 throw~ tb-out \\; pd-dut "
            f"connect 0 0 1 0 \\; loadbang-1 bang"
        )
        park_y[0] += 22
        conns.append(f"#X connect {b} 0 {m} 0")
        if first[0] is None:
            first[0] = b

    x = 90
    for label, relpath in active:
        button(label, relpath, x, 310)
        x += 132
    x = 90
    for label, relpath in others:
        button(label, relpath, x, 400)
        x += 132

    # empty [pd dut] subpatch — audio leaves it via throw~, so it needs no wires
    body.append("#N canvas 0 0 240 200 dut 0")
    body.append("#X restore 800 560 pd dut")
    counter[0] += 1

    conns.append(f"#X connect {catch} 0 {dac} 0")
    conns.append(f"#X connect {catch} 0 {dac} 1")
    conns.append(f"#X connect {rec} 0 {recmsg} 0")
    conns.append(f"#X connect {recmsg} 0 {skeys} 0")
    conns.append(f"#X connect {loadb} 0 {openp} 0")
    conns.append(f"#X connect {openp} 0 {lp} 0")
    conns.append(f"#X connect {lp} 0 {lt} 0")
    conns.append(f"#X connect {lt} 0 {swo} 0")
    conns.append(f"#X connect {lb} 0 {dspmsg} 0")
    if first[0] is not None:
        conns.append(f"#X connect {lb} 0 {first[0]} 0")

    lines = ["#N canvas 60 60 960 680 10;"]
    lines += [b + ";" for b in body]
    lines += [c + ";" for c in conns]
    dest = os.path.join(project_dir, "pd", "dev", "test-bench.pd")
    with open(dest, "w") as f:
        f.write("\n".join(lines) + "\n")
    return (dest, len(active) + len(others))


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

    if sys.argv[1] == "mac":
        force = len(sys.argv) > 2 and sys.argv[2] in ("rebuild", "--force", "-f")
        try:
            built, n_links, n_ext = place_darwin(PROJECT_DIR, force=force)
        except RuntimeError as e:
            print(f"Error: {e}")
            sys.exit(1)
        verb = "Built" if built else "Reused"
        msg = f"{verb} waveplayer~.pd_darwin; symlinked into pd/lib + {n_links - 1} mode folder(s)"
        if n_ext:
            msg += f"; + {n_ext} replacement-abstraction link(s) (tanh~/freeverb~)"
        print(msg)
        if not built:
            print("(source unchanged — use './load mac rebuild' to force a rebuild)")
        print("Open pd/dev/test-bench.pd in Pd to develop a mode.")
        return

    if sys.argv[1] == "bench":
        dest, n = generate_bench(PROJECT_DIR)
        print(f"Generated {os.path.relpath(dest, PROJECT_DIR)} with {n} mode button(s).")
        print("Run './scripts/load.py mac' (once) if you haven't, then open it in Pd.")
        return

    if sys.argv[1] == "hw":
        removed = strip_darwin(PROJECT_DIR)
        if removed:
            print(f"Stripped {len(removed)} macOS external(s):")
            for p in removed:
                print(f"  {os.path.relpath(p, PROJECT_DIR)}")
        else:
            print("Already clean — no macOS externals present.")
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
