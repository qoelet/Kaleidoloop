# Kaleidoloop

## Notes

The original patches shipped with a `waveplayer~` that is compiled for Linux.
I want to be able to develop patches (_playback modes_) on my Macbook, so this fork
has a "fake" `waveplayer~` and play a mode via a single-mode test patch with some
workarounds,

- `./scripts/load.py mac`: builds + places `pd/lib/waveplayer~.pd_darwin`.
- Populate `pd/sounds/` with samples
- Develop with `pd/dev/test-bench.pd` in Pd Vanilla
- Before uploading to Kaleidoloop: `./scripts/load.py hw`
