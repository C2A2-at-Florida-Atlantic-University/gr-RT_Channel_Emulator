# Repository guidance

## Scope and source of truth

Read `README.md`, `pyproject.toml`, and the affected implementation before editing.
Check `git status --short`; preserve existing edits, untracked files, scene assets,
notebook results, and research documents. Do not commit or discard work unless asked.

The project emulates one transmitter and one receiver in a Sionna RT scene,
applies the resulting FIR taps to GNU Radio IQ samples, and compares RT and LS
taps in one maintained GRC demo.

- `python/rt_channel_emulation/backend.py`: scene loading, radio settings, and ray-traced taps.
- `python/rt_channel_emulation/runtime.py`: atomic configuration updates and reference metadata.
- `python/rt_channel_emulation/block.py`: GNU Radio channel, message inputs, and tap outputs.
- `python/rt_channel_emulation/visualization.py` and `gui_launcher.py`: owned Qt viewer process.
- `grc/rt_channel_emulation_rt_channel_emulation.block.yml`: GRC interface and constructor templates.
- `examples/channel_estimation_demo.grc`: the single maintained demo source.
- `scripts/`: installation, launch, linear-motion sender, and optional ROS sender.
- `tests/`: regression tests and optional profiling/OTA notebooks.

Do not hand-edit `examples/channel_estimation_demo.py`; regenerate it with `grcc`.
Do not edit scenes or paper artifacts as a side effect of unrelated work.

## Keep changes simple

- Make the smallest readable change that meets the request; preserve unrelated behavior.
- Prefer existing GNU Radio blocks and standard-library code over new custom blocks or dependencies.
- Keep networking outside the RT block. The demo owns two UDP Socket PDU blocks.
- Explain inputs, outputs, units, assumptions, and non-obvious calculations in concise comments/docstrings.
- Keep README prose direct, without fact/inference/opinion prefixes. Retain useful primary-source links.
- Check demo defaults in the `.grc` file rather than copying older README values.
- Update the Python interface, GRC definition, demo, affected senders, tests, and docs together when needed.
- Do not silently fix unrelated failures or weaken tests to obtain a passing run; report discrepancies.

## Interface and model conventions

- Positions are three finite real coordinates `[X, Y, Z]` in scene-local metres, not GPS.
- `tx_position` and `rx_position` message inputs accept PMT numeric arrays or Socket PDU
  byte payloads containing one UTF-8 JSON array. Initial positions remain block parameters.
- Demo destinations are localhost UDP ports 52001 (TX) and 52002 (RX). Each packet updates
  one node and triggers a trace. Delivery is not guaranteed; updates are not sample-synchronized.
- A `config` PMT dictionary can update both positions atomically in one trace.
- Register named message-handler methods on the receiving GNU Radio Python block;
  its gateway resolves handlers by method name. Avoid lambdas for `set_msg_handler`.
- TX/RX amplitude multipliers belong outside the RT block. `noise_voltage` is linear
  complex RMS noise amplitude, with noise power equal to its square, not a dB quantity.
- RT references contain propagation-only taps, excluding added noise and external gains.
  Keep LS and RT sample rates, lag origins, and vector lengths matched.
- Preserve the current propagation flags and delay normalization unless asked to change them.
  Path depth is not tap count; snapshot speed and LS agreement do not prove OTA accuracy.
- Flowgraph shutdown must close the owned map viewer. Tests must clean up sockets and processes.

## Environment and verification

Use the Python interpreter compatible with GNU Radio 3.10 and PyQt5. The installation
script creates `.venv` with system packages visible and installs the project editably:

```bash
./scripts/install.sh
```

Set `PYTHON=/path/to/compatible/python` when needed. Do not replace an existing virtual
environment or upgrade dependencies without a task-specific reason. Never mix Python
versions through `PYTHONPATH`. Dependencies and optional extras are defined in `pyproject.toml`.

For CPU tracing, set `DRJIT_LIBLLVM_PATH` to the installed matching LLVM shared library
before importing Dr.Jit/Mitsuba/Sionna. The local macOS example is in README; it is not
a portable default. Restart notebook kernels after a failed LLVM initialization.

Run from the repository root, starting with tests relevant to the change:

```bash
.venv/bin/python -m unittest tests.test_backend tests.test_runtime tests.test_position_messages tests.test_position_source -v
.venv/bin/python -m unittest tests.test_integration tests.test_standard_ls tests.test_snr -v
.venv/bin/python -m unittest discover -s tests -v
GRC_BLOCKS_PATH="$PWD/grc" PYTHONPATH="$PWD/python" \
  grcc -o examples examples/channel_estimation_demo.grc
```

Real Sionna tests need a working backend; integration tests use localhost sockets and
GNU Radio shared memory. Report unavailable dependencies or execution restrictions,
not skipped checks as passes. GUI behavior may also require a manual run through
`./scripts/run_example.sh`.

There is an existing mismatch: `test_demo_generates_valid_python` expects tap autoscaling
off, while the current demo enables it. Verify the current files before reporting it;
do not change plot behavior merely to satisfy an unrelated task.

Do not run the full profiling sweep, download private OTA data, or contact
live ROS publishers unless the task requires it. Use synthetic/offline tests first.
Never store credentials in notebooks, source files, or logs.

For each completed change, summarize what changed and why, list commands actually run
and their results, and state remaining limitations. Keep generated files and local
experiment outputs out of maintained source.
