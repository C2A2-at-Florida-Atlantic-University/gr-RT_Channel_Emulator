# GR RT Channel Emulation

This GNU Radio block uses Sionna RT to compute a single-transmitter (TX),
single-receiver (RX) channel from a scene and node positions. The maintained
[GRC demo](examples/channel_estimation_demo.grc) sends a known GLFSR sequence
through that channel and compares least-squares (LS) tap estimates with the
RT block's own tap output. No radio hardware is required.

## 1. Install the system requirements

Install the system components before running the project installer:

| Component | Requirement | Installed by |
|---|---|---|
| Python | ≥3.10; use the interpreter compatible with your GNU Radio installation | Your GNU Radio environment |
| GNU Radio | 3.10, including Companion, `grcc`, QT GUI, and `gr-network` (Socket PDU) | Your OS/package manager |
| PyQt5 | Required by the demo and bundled map viewer | Your GNU Radio environment |
| LLVM | Required for CPU ray tracing; shared library must match Python's CPU architecture | Your OS/package manager |
| NumPy / Sionna RT | ≥1.26 / ≥2.0,<3, plus their dependencies | `scripts/install.sh` via [pyproject.toml](pyproject.toml) |

Follow the [GNU Radio installation guide](https://wiki.gnuradio.org/index.php/InstallingGR),
[Sionna RT requirements](https://github.com/NVlabs/sionna#installation), and
[Dr.Jit backend instructions](https://drjit.readthedocs.io/en/latest/what.html#backends).
The installer does **not** install GNU Radio, PyQt5, or the system LLVM library.
No radio hardware, GPU, ROS installation, or separate Sionna-RT-GUI is needed
for the standard demo. ROS and notebook tools are optional, described below.

The local macOS environment uses Python 3.13.5, GNU Radio 3.10.12.0,
NumPy 2.3.0, Sionna RT 2.0.1, Dr.Jit 1.3.1, Mitsuba 3.8.0, PyQt5 5.15.11,
and LLVM 18. These are tested versions, not a lockfile; other combinations
need verification, particularly the demo's GNU Radio sample alignment.

## 2. Install the block from the repository root

Run all commands from the repository root. With the GNU Radio environment
active, first check its Python and command-line tools, then install:

```bash
python3 -c 'import sys; from gnuradio import gr, qtgui, network; import PyQt5; print(sys.executable); print(gr.version())'
command -v gnuradio-companion
command -v grcc
./scripts/install.sh
```

[install.sh](scripts/install.sh) creates `.venv`
with `--system-site-packages` so it can use GNU Radio, and runs
`pip install --editable .` against this repository's `pyproject.toml`.
Changes under `python/rt_channel_emulation/` then take effect without reinstalling.
To select a different interpreter, set `PYTHON`; for this macOS installation:

```bash
PYTHON=/opt/homebrew/bin/python3.13 ./scripts/install.sh
```

GNU Radio and the environment's binary extensions
must use compatible Python versions. Do not put another Python version's
`site-packages` on `PYTHONPATH`. If an existing `.venv` is incompatible, move it
aside before rerunning the installer with the correct interpreter. Selecting
`PYTHON` does not replace an existing `.venv`. Use `.venv/bin/python` for project
commands; activating the environment is optional.

## 3. Verify the installed requirements

If LLVM is not automatically detected, set
`DRJIT_LIBLLVM_PATH` to your existing LLVM shared library **before importing
Dr.Jit, Mitsuba, or Sionna**. The verified MacPorts path on this workstation is
shown below; replace it with the library on your machine:

```bash
export DRJIT_LIBLLVM_PATH=/opt/local/libexec/llvm-18/lib/libLLVM.dylib
```

Check the GNU Radio network block, ray tracer, and all five packaged scenes:

```bash
.venv/bin/python - <<'PY'
import sys
from gnuradio import gr, qtgui, network
from sionna.rt import PathSolver
from rt_channel_emulation import RTChannelEmulation, resolve_scene_path

print("Python:", sys.executable)
print("GNU Radio:", gr.version())
for scene in ("FAU_scene", "POWDER", "AERPAW_scene", "LOS_empty", "NLOS_box"):
    print(scene, resolve_scene_path(scene))
PY
```

For notebooks, select this repository's `.venv` kernel. If LLVM initialization
already failed, **restart the kernel**, set the path in its first cell, and run
from the top. Rerunning `os.environ[...] = ...` in the failed kernel is not enough
to reset Dr.Jit. A notebook launched before the shell export will not inherit
that change. Check `sys.executable` and the library path if a fresh kernel still
fails. The same environment variable must be available to GRC and its generated
flowgraph; launch GRC from the configured terminal.

## 4. Open and run the single demo

Launch GRC through the wrapper so it can find the block
definition and the installed dependencies:

```bash
./scripts/run_example.sh
```

Press **Run** and wait for the initial ray trace. GRC generates
`examples/channel_estimation_demo.py` from the `.grc` file; do not maintain or
edit that generated Python file. Edit the `.grc` file and regenerate instead.
Relaunch GRC after changing the block definition under `grc/`.
The wrapper supplies `GRC_BLOCKS_PATH` and the project Python paths. If the block
does not appear, use this wrapper rather than an already-open GRC instance.

Current settings in the maintained `.grc` file:

| Setting | Value |
|---|---|
| Scene | `FAU_scene` |
| Initial TX / RX positions | `[28,26,1]` / `[28,25,1]`, scene-local metres |
| Sample rate / carrier | 1 MS/s / 2.4 GHz |
| Maximum path depth | 5 |
| Tap lags / vector length | 0 through 16 inclusive / 17 complex taps |
| GLFSR period / averaging | 255 samples / 8 periods per LS observation |
| External TX / RX amplitude | 1 / 1 (linear) |
| Noise voltage | 0.001 linear complex RMS |
| Tap plot | QT GUI Time Sink in stem mode; autoscaling enabled |
| RT tap output / map viewer | Enabled / enabled |
| External position sockets | UDP TX `127.0.0.1:52001`, RX `127.0.0.1:52002` |

The scenes are packaged under
[scenes](python/rt_channel_emulation/scenes). To use POWDER in the same demo,
stop the flowgraph and change the RT block's scene to `POWDER`, TX to `[0,0,2]`,
and RX to `[10,0,2]`. These are local test coordinates, not geographic locations.
Use `AERPAW_scene` to load [AERPAW.xml](python/rt_channel_emulation/scenes/AERPAW_scene/AERPAW.xml)
and its five meshes. All scene positions are local XYZ metres, not GPS coordinates.
To create your own environment, follow
[section 10](#10-create-a-scene-with-blender-and-openstreetmap).

For line-of-sight (LoS) and
non-line-of-sight (NLoS) checks, use the same demo for both controlled scenes:

1. Stop the flowgraph and any position sender. Enable **Visualization**.
   Set TX to `[-5,0,1]` and RX to `[5,0,1]` in scene-local metres.
   Keep sample rate at **1 MS/s**, carrier at **2.4 GHz**, maximum depth at **5**,
   and set the shared GRC variables `l_min = -6`, `l_max = 32` (39 taps).
   Do not override the RT block alone: both LS and RT must use `num_taps`.
2. Set the RT block's scene to `LOS_empty` and run. Set the external amplitude
   controls to **TX = 1**, **RX = 1**, and **Noise Voltage = 0.00001**.
   The viewer shows the nodes and direct ray without a ground plane or objects.
3. Stop, change only the scene to `NLOS_box`, and run with the same amplitude/noise
   values. The closed box now obstructs the direct link.
4. To restore LoS without removing the box, stop and change RX to `[5,5,1]`.
   Run again: the direct link passes beside the box.

With the positions in steps 1–3:

| Scene alias | Geometry | Sionna RT 2.0.1 result |
|---|---|---|
| [LOS_empty](python/rt_channel_emulation/scenes/LOS_empty/LOS_empty.xml) | No surfaces | One direct path; peak tap magnitude ≈0.000994 |
| [NLOS_box](python/rt_channel_emulation/scenes/NLOS_box/NLOS_box.xml) | Closed metal shell, x/y = −1…1 m, z = 0…2 m | No valid paths; all 39 RT taps zero |

The link distance is 10 m. For the configured
isotropic, co-polarized antennas, free-space amplitude is
`|h| = (c/f)/(4*pi*d) ≈ 0.000994`, where `c = 299792458 m/s`, `f = 2.4e9 Hz`,
and `d = 10 m`. Physical delay is `d/c ≈ 33.36 ns`. The backend shifts the first
arrival to lag 0, which is vector index 6 for lags −6…32; the other tap
magnitudes are numerically near zero. At 1 MS/s, tap spacing is 1 µs, not one
tap per ray. [Propagation fundamentals](https://nvlabs.github.io/sionna/rt/em_primer.html),
[sampled taps](https://nvlabs.github.io/sionna/rt/api/paths.html#sionna.rt.Paths.taps)

NLoS means the direct segment is obstructed, not that every
NLoS environment has zero received power. This isolated box has no surrounding
reflectors; the [backend](python/rt_channel_emulation/backend.py) enables specular
reflection and transmission but leaves diffraction and diffuse reflection off.
The shell uses ITU metal with **0.1 m thickness per surface**, not a solid 2 m
metal volume. Sionna's material thickness models a slab at each mesh face.
[Radio-material model](https://nvlabs.github.io/sionna/rt/api/radio_materials.html)

In `NLOS_box` at the blocked
positions, LS taps should fluctuate near zero because the remaining received
samples are noise. This follows from the tested zero RT taps and the demo's
additive-noise model; it does not validate real-world building penetration.

If UDP port 52001 or 52002 is already occupied, stop
the other demo or change **Port** in the corresponding **Socket PDU** block
and the sender's `--tx-port` or `--rx-port` together. For a static-only run,
leave the sender stopped; the initial positions remain in use.

## 5. Read the plots and check the controls

```text
GLFSR → Throttle → Multiply TX amplitude → RT Channel → Multiply RX amplitude
                                            │                    ├→ signal display
                                            │                    ├→ repeated-probe SNR (secondary)
                                            │                    └→ LS → magnitude → vector to stream ────────────────┐
                                            └→ rt_taps ─┬→ magnitude → vector to stream → Multiply TX×RX ─────────────┴→ stem plot
                                                       └→ tap-energy SNR (primary)
```

The complex Vector Source repeats the demo's literal
`glfsr` sequence of ±1 values. Standard GNU Radio blocks align the received
samples, average periods, calculate correlation, and apply the LS correction.
For a fixed channel, LS solves `min ||Xh-y||²`, with `X` built from delayed
copies of that known sequence. The numbered GRC comments and
[probe-weight helper](python/rt_channel_emulation/estimation.py) define each
stage; [tests](tests/test_standard_ls.py) compare it with NumPy's least-squares solve.

The RT block outputs `L = l_max-l_min+1` complex propagation
coefficients directly (17 for the current demo, 39 for the controlled scene test).
They exclude noise and external amplitude scaling. In the current demo, LS
uses the scaled RX samples directly, so its estimates include TX×RX amplitude.
The RT magnitudes are multiplied by the same `tx_amplitude * rx_amplitude`
outside the block for comparison.
Blue plots LS magnitudes; red plots scaled RT magnitudes. Both traces use the same
vector length and steady-state rate, approximately 5 estimates/s.

Each tap is a vertical stem from zero to its magnitude,
not a line connecting adjacent taps. Two standard Vector to Stream blocks feed
a [QT GUI Time Sink in stem mode](https://www.gnuradio.org/doc/doxygen/classgr_1_1qtgui_1_1time__sink__f.html),
with `num_taps` points per frame. Its horizontal **Time** axis represents FIR
delay `j / samp_rate`, not elapsed run time; Sionna lag is `l_min + j`.
At 1 MS/s, taps 0, 1, 2 appear at 0, 1, 2 µs. Autoscaling is currently enabled.
For a fixed zero baseline, disable **Autoscale**, set **Y min = 0**, and choose
**Y max** above the largest tap magnitude. The trace legend is hidden in the
current demo; enable it in `tap_plot` if needed.

**TX Amplitude** and **RX Amplitude** control external
Multiply Const blocks, not the RT block. Both use linear sample multipliers:
1 means unchanged, 2 doubles amplitude. The current sliders span **0–100**.
Use positive amplitudes for meaningful channel/SNR comparisons: TX = 0 removes
the probe, and RX = 0 removes both the received signal and noise. RX scales both
signal and noise; it does not improve SNR. Neither amplitude control changes
the RT propagation taps.
Changing controls can temporarily mix old/new samples; compare after settling.

**Noise Voltage** now follows GNU Radio's original
`channels.channel_model`: linear complex RMS amplitude `sigma`, with
`E[|w|²] = sigma²` and I/Q component variances `sigma²/2`. Zero disables noise.
For example, 0.01 means noise power 0.0001 in sample units; doubling it
quadruples noise power. No physical volts, impedance, or dBm calibration is
assumed. Sources: [GNU Radio channel model](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/gr-channels/lib/channel_model_impl.cc),
[complex Gaussian noise implementation](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/gr-analog/lib/fastnoise_source_impl.cc).

Regenerate existing flowgraphs. Remove `tx_gain_db` and
`rx_gain_db` from the RT block; use external multipliers `10**(old_gain_db/20)`
if needed. Replace `noise_voltage_db` with `noise_voltage = 10**(old_value/20)`;
do not copy a negative dB value into the new linear field. The block's default
is now zero noise. Message schema version 2 reports `noise_voltage` and removes
gain fields; `effective_taps` remains an identical alias of `taps` for existing
reference readers. See [runtime.py](python/rt_channel_emulation/runtime.py).

The **SNR comparison** plot shows two received-sample SNR values in dB:

- **Blue — RT tap-energy SNR (model):** the primary, simple prediction from the
  RT taps, external amplitudes, and configured noise voltage. It does not use LS.
- **Red — Repeated-probe SNR (measured):** the existing RX-sample estimator,
  retained unchanged as a secondary check; it does not use RT taps or configured noise.

For the primary calculation, let `h[k]` be the complex propagation taps,
`A_TX` and `A_RX` the linear amplitude controls, and `sigma` the linear complex
RMS noise voltage. The model is
`y[n] = A_RX * (A_TX * sum(h[k]*x[n-k]) + w[n])`, with noise added **after**
propagation and **before** RX scaling, as in the
[GNU Radio channel model](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/gr-channels/lib/channel_model_impl.cc).

1. Sum **all** squared RT tap magnitudes and apply the amplitude controls:
   `Ps = (A_TX*A_RX)**2 * sum(abs(h[k])**2)`.
2. Calculate received noise power: `Pn = (A_RX*sigma)**2`.
3. Display `SNR_dB = 10*log10(Ps/Pn)`.

These are sample powers, not calibrated watts. Above the numerical floors and
for nonzero RX amplitude, this simplifies to
`10*log10(A_TX**2 * sum(abs(h[k])**2) / sigma**2)`:
RX amplitude cancels because it scales signal and noise equally. Sum **squared
magnitudes**, not magnitudes or complex taps. Seven standard blocks implement
the primary branch: Complex to Mag², Vector to Stream, Integrate, Multiply Const
(signal scaling), Max (zero-power guard), Multiply Const (inverse noise power),
and Log10. The existing constant supplies the numerical floor. No new custom
block, dependency, or ray trace is needed.

Example: one tap `h = 0.1`, TX = 2, RX = 3, and noise voltage = 0.01 gives
`Ps = 6**2 * 0.1**2 = 0.36`, `Pn = 0.03**2 = 0.0009`, and
`SNR = 10*log10(400) = 26.02 dB`. Doubling RX to 6 leaves that SNR unchanged.

**Assumption:** the simple tap-energy formula is exact for zero-mean, unit-power
input with uncorrelated samples. The demo's ±1 GLFSR has unit power
but small nonzero periodic correlations, so this is an **approximation** for
its multipath waveform, not an exact predicted probe power. For the validated
N-sample GLFSR and at most N taps, the exact period-averaged power before
amplitude scaling is `(1 + 1/N)*sum(abs(h)**2) - abs(sum(h))**2/N`.
This follows from the probe autocorrelation checked by the
[existing helper](python/rt_channel_emulation/estimation.py) and
[SNR tests](tests/test_snr.py). The primary branch deliberately uses the simpler
tap-energy formula; the two traces need not coincide exactly even after settling.

For the secondary calculation, probe period `N = len(glfsr) = 255` and
window `W = 8*N = 2040` samples:

1. Delay RX by N samples and subtract: `d[n] = y[n] - y[n-N]`.
2. Estimate noise power: `Pn = mean(|d[n]|²) / 2` over W samples.
3. Measure total received power over the matching window: `Pt = mean(|y[n]|²)`.
4. Estimate signal power: `Ps = Pt - Pn`; display `10*log10(Ps/Pn)` in dB.

Both SNR streams have rate `samp_rate/(window_size*window_decimation)`, about
5 values/s. The SNR plot uses this rate, not the IQ sample rate, for its time
axis. It shows relative stream time within each display frame, not a wall-clock
timestamp. RT snapshots and probe windows are not sample-synchronized.

A fixed linear multipath channel preserves the probe's
period: `s[n] = s[n-N]`. With `y = s+w`, subtraction leaves `w[n]-w[n-N]`.
For independent zero-mean noise, the two noise powers add, giving `2*Pn`.
This cancels the full repeated multipath waveform, not just its LoS component.
Standard Delay, Subtract, Complex to Mag², Integrate, Multiply Const, Keep One
in N, Max, Divide, Log10, and QT GUI Time Sink blocks implement these steps in the
[demo](examples/channel_estimation_demo.grc); [tests](tests/test_snr.py) compare
the generated branch against independent NumPy calculations.

With a settled channel and powers above the numerical floors, doubling TX amplitude
raises SNR by about 6.02 dB; doubling RX amplitude leaves it unchanged; doubling
noise voltage lowers it by about 6.02 dB. The secondary estimator uses raw RX samples,
before LS averaging, so it does not include LS processing gain. It assumes a
stable channel and uncorrelated noise across adjacent probe periods; motion,
CFO, timing mismatch, or amplitude transitions contaminate the noise estimate.
Both calculations floor signal and noise powers at `1e-20` before division/log.
These floors prevent numerical errors; they do not add noise to the signal.
At zero noise, true SNR is infinite for nonzero signal; with zero signal and
positive noise, true SNR is negative infinity in dB; RX = 0 gives undefined
0/0. The finite displayed values in these cases are **numerical placeholders**,
not physical SNR. The secondary subtraction also fluctuates near no signal.
Allow several windows to settle after changing taps or controls.

To check both calculations, run the demo, keep both amplitudes and noise
positive, wait for settling, and double one control at a time. Use the predicted
±6.02 dB / unchanged responses above; the red trace fluctuates with finite noise
windows while the blue trace is constant for fixed taps and settings.

LS magnitudes should follow the RT reference after
settling; stronger noise should increase LS fluctuations. This follows from the
noisy FIR model tested by the regression suite, not a guarantee for every window.

Keep frequency offset at 0 and timing ratio at 1.
Stop and regenerate after changing sample rate, tap range, probe, or averaging.
The demo compensates for GNU Radio 3.10.12 startup alignment using
Delay=`max(2,L)+1` and whole-period Skip Head. Each LS observation covers
2.040 ms; SNR also discards its one-period delay startup. RT updates are not
sample-synchronized with those observations; compare after changes settle.
Magnitude agreement does not verify phase or real-world propagation accuracy.

Use the map's zoom buttons or mouse wheel,
arrow controls or mouse drag, and elevation/rotation controls. **Fit TX/RX**
recenters the view. Press **Stop** in GRC or close the flowgraph window to close
the owned map viewer too. Socket PDU blocks belong to the flowgraph, not the RT
block. [Viewer code](python/rt_channel_emulation/gui_launcher.py)

## 6. Optionally move the nodes

The RT block has two optional **message inputs**:
`tx_position` and `rx_position`. Each expects `[X, Y, Z]` in the scene's local
coordinate system, in metres. For example, `[28, 26, 1]` means x = 28 m,
y = 26 m, z = 1 m relative to the scene origin; z is not necessarily height
above the local ground. These are not GPS coordinates. The **Initial TX/RX
Position** parameters are used until a message updates that node.
[Position handling](python/rt_channel_emulation/block.py)

Two standard
[Socket PDU blocks](https://www.gnuradio.org/doc/doxygen/classgr_1_1network_1_1socket__pdu.html)
receive UDP packets. A PDU is a metadata/payload pair; the RT block decodes
its byte payload as a UTF-8 JSON array. No additional custom block is used:

```text
position_source.py ── [TX X,Y,Z] ── UDP :52001 ── Socket PDU ── tx_position ┐
                   └─ [RX X,Y,Z] ── UDP :52002 ── Socket PDU ── rx_position ┴─ RT Channel
```

Reopen GRC with `./scripts/run_example.sh` to load the updated
block definition, then press **Run**. In another terminal, send ten updates:

```bash
.venv/bin/python scripts/position_source.py \
  --host 127.0.0.1 --tx-port 52001 --rx-port 52002 \
  --tx-start 28 26 1 --rx-start 28 25 1 \
  --tx-velocity 0 0.5 0 --rx-velocity 0 -0.5 0 \
  --rate-hz 0.2 --updates 10
```

The source sends `position = start + velocity*t`,
with metres, metres/second, and `t = update_index/rate_hz`. At 0.2 Hz, the second
pair moves each node 2.5 m. The first pair contains exactly the starting
positions. Each socket receives only its own array, e.g. `[28, 26, 1]`,
not a dictionary and not a combined TX/RX message. One datagram contains one
complete array, below the demo's 1024-byte receive limit. Each accepted message
retraces the channel and refreshes its FIR taps, RT reference outputs, and map.
Malformed arrays, non-numeric values, NaN, and infinity are logged and ignored.
[Sender](scripts/position_source.py), [input tests](tests/test_position_messages.py)

TX and RX messages are independent: one message updates
one node while keeping the other node's current position. A pair therefore
causes two traces, with an intermediate channel state; it is not atomic or
synchronized to IQ samples. At 0.2 updates/s per node, the sender requests
0.4 traces/s in total. Use one sender per node and lower the rate if traces
cannot keep up, because message queues can grow. UDP does not guarantee
delivery or ordering, and a successful send does not confirm application.
The demo binds localhost only; positions are not authenticated.
[UDP specification](https://www.rfc-editor.org/rfc/rfc768)

A non-socket message source can send
`pmt.init_f64vector(3, [28, 26, 1])` directly to `tx_position` (similarly for RX).
The existing `config` port still accepts
`pmt.to_pmt({"tx_position": [28, 26, 1], "rx_position": [28, 25, 1]})`
to apply both positions in one trace. Existing Python position setters remain.

The RT block no longer has `mobility_enabled`,
`mobility_host`, or `mobility_port`, or their setters/internal TCP server.
Regenerate old flowgraphs and replace that TCP connection with the demo's
two UDP Socket PDU connections. The sender's old `--port` option is replaced
by `--tx-port` and `--rx-port`. No new Python dependencies are needed.

The retained [ROS bridge](scripts/ros_position_source.py)
uses two `geometry_msgs/msg/PoseStamped` topics. Both must already express
positions in the loaded scene's XYZ metre frame; the bridge does not transform
GPS, frames, or orientations. ROS 2 is a separate, optional installation.
Stop the linear position source, then in a sourced ROS terminal run:

```bash
python3 scripts/ros_position_source.py --frame-id scene --rate-hz 0.2 --max-age-s 10 \
  --host 127.0.0.1 --tx-port 52001 --rx-port 52002
```

In two other sourced ROS terminals, publish the initial positions:

```bash
ros2 topic pub -r 1 /tx/pose geometry_msgs/msg/PoseStamped \
  '{header: {frame_id: scene}, pose: {position: {x: 28, y: 26, z: 1}, orientation: {w: 1}}}'
ros2 topic pub -r 1 /rx/pose geometry_msgs/msg/PoseStamped \
  '{header: {frame_id: scene}, pose: {position: {x: 28, y: 25, z: 1}, orientation: {w: 1}}}'
```

Change a publisher's position to move its node. Both
positions must be available and fresh; missing, stale, non-finite, or wrong-frame
positions are not sent. Each timer tick sends the fresh TX and RX arrays to
their respective sockets, including unchanged values to tolerate packet loss.
Helper tests cover validation; live ROS operation must be checked in your ROS
environment.

## 7. Verify changes before continuing development

From the repository root, configure LLVM as in step 3, then run the regression
suite and compile the demo without launching windows:

```bash
.venv/bin/python -m unittest discover -s tests -v
GRC_BLOCKS_PATH="$PWD/grc" PYTHONPATH="$PWD/python" \
  grcc -o examples examples/channel_estimation_demo.grc
```

For focused position-input checks and controlled scene tests:

```bash
.venv/bin/python -m unittest tests.test_position_messages tests.test_position_source tests.test_integration -v
.venv/bin/python -m unittest tests.test_scenes -v
```

The current `test_demo_generates_valid_python` check expects tap autoscaling
to be disabled, but the maintained demo enables it. This is a known test/demo
mismatch: GRC generation succeeds, but that plot-setting assertion fails.
The older `test_amplitude_controls_are_external_and_ls_is_calibrated` also
expects the removed `calibrate_ls` block; the current demo instead scales the RT
magnitudes to match uncalibrated LS taps. These existing mismatches are separate
from the SNR calculation checks.

Tests check that the empty scene has no geometry, that
the box is closed and outward-facing, and that the real Sionna backend produces
the free-space delay/amplitude, blocked-link suppression, and restored LoS after
moving RX. Each case retains 39 finite taps. These propagation tests skip only
when Sionna is not installed; an installed but broken Sionna/LLVM setup fails.

Tests exercise tap extraction, external amplitude/linear noise,
model-based and probe-based SNR, configuration
updates, direct RT vectors, standard-block LS, position messages, scene geometry,
and viewer shutdown. Real localhost UDP tests exercise both socket routes;
a Sionna integration test checks retracing on a GNU Radio scheduler thread.
GNU Radio/Qt-dependent tests skip if those components are unavailable. Repeat
steps 4–6 for a real-scene GUI check; passing unit tests is not RF validation.

## 8. Profile scene-to-tap calculation time

Open [profile_snapshots.ipynb](tests/profile_snapshots.ipynb).
Install its optional tools from the repository root, then select this repository's
`.venv` Python as the notebook kernel and run all cells:

```bash
.venv/bin/python -m pip install -e ".[profiling]"
```

This extra provides Matplotlib, `ipykernel`, and NBClient. Use a notebook-capable
editor or install a Jupyter frontend separately; JupyterLab is not included.
Configure LLVM in the first cell before imports, and restart the kernel if an
earlier import failed.

The notebook measures the production backend for **FAU,
POWDER, AERPAW, and LOS_empty**, at every maximum depth from **3 through 15**. Each point
uses two discarded warm-ups and **100 measured snapshots at random TX/RX pairs**;
the plot shows **arithmetic mean** wall-clock milliseconds, with one color per
scene. The same 100 pairs are reused at every depth: **5,200 measured snapshots**.
Positions are independently uniform over each scene's XY geometry bounds at
fixed scene height `z = 2 m`; the empty scene uses FAU's bounds and pairs.
AERPAW's bounds include its full **5.4 × 5.4 km ground plane**, not only its
buildings. A completed four-scene run is documented in the
[paper's profiling provenance](paper/README.md#profiling-scope). The paper reports
only FAU, POWDER, and AERPAW; Empty remains in the notebook and saved raw data.
Earlier result directories remain unchanged. Rerun the notebook for your own measurements.
Positions may fall inside buildings; there is no collision rejection or
terrain-relative height adjustment. Counts, position seed, height, radio settings,
and tap support are editable in the notebook. Raw times, mean summaries, actual
coordinates, settings, and the PNG plot are saved under `tests/profiling_results/`.
Completed batches are saved after each scene/depth point in a new run directory;
earlier fixed-position median results are not overwritten.

After the LLVM setup in step 3, run every cell
without a notebook editor using the included
[NBClient command](https://nbclient.readthedocs.io/en/latest/client.html#using-a-command-line-interface):

```bash
.venv/bin/jupyter-execute tests/profile_snapshots.ipynb --timeout=3600
```

Timing includes the current backend's per-call scene reload,
ray tracing, tap conversion, and completion synchronization. It excludes initial
imports, visualization, GNU Radio tap application, and sample-stream scheduling.
This is **taps-ready latency**, not a pure solver benchmark or a measured maximum
flowgraph sample rate. Bounds discovery, position generation, and file writes
are also outside the timer. The mean is `sum(100 durations) / 100`, not a median.
One spatial sample set and warmed caches limit generalization to other placements
and cold startup. Definitions and primary sources are included in the notebook.
The optional tools do not change block runtime requirements.

Fast timing/mean/sampling checks do not run the full benchmark:

```bash
.venv/bin/python -m unittest tests.test_snapshot_profiling -v
```

### Export scene pictures for the paper

With the same environment and LLVM setup, load and render all five bundled scenes:

```bash
.venv/bin/python paper/prepare_scene_views.py
```

The script uses Sionna's renderer and the existing viewer's material colors;
it does not run channel profiling or alter scene files. It writes individual
300-dpi PNGs and [a combined overview](paper/scene_views/scenes_overview.png) under
`paper/scene_views/`. The empty scene is labeled as having no surfaces. Pictures
show geometry only, without TX/RX nodes or propagation paths; each scene is
framed separately without vertical exaggeration. See the
[paper instructions](paper/README.md#scene-pictures) for inclusion in Overleaf.

## 9. Compare POWDER OTA and simulated channel estimates

Open [compare_powder_ota.ipynb](tests/compare_powder_ota.ipynb).
Install its optional tools and authenticate with an account that can read the
[dataset](https://huggingface.co/datasets/CAAI-FAU/Key-Generation):

```bash
.venv/bin/python -m pip install -e ".[profiling,ota]"
.venv/bin/hf auth login
```

Select the project's `.venv` kernel, review the RF frequency and
antenna assumptions in the notebook's section 2, and run all cells. The notebook pins the
`CaST-Powder-OTA-Dense-Nodes-123` dataset revision and reads stored estimates for
EBC (node 1) and Guest House (node 2), separately in each direction. It uses the
supplied XYZ positions and the block's production POWDER backend, matching the
OTA tap count and delay spacing. The current notebook uses maximum path depth
15 and a provisional carrier frequency of 3.5 GHz. No GNU Radio GUI is required.

Tap-magnitude stem plots for both directions, per-record shape
errors, raw tap arrays, normalized power-delay arrays, counts, settings and
provenance are saved locally under `tests/ota_comparison_results/`. The notebook
explains normalization and rejects
inconsistent delay grids or unreviewed strongest-tap alignment. It does not fit
gain, phase or delay to improve the comparison.

The current stem traces and error metrics use unit-energy normalization.
However, the notebook's shaded range currently takes square roots of quantiles
of raw OTA magnitudes, not normalized powers; do not report it as a comparable
normalized percentile interval without correcting that calculation.

These are **normalized shape**
comparisons, not calibrated path-loss validation. The export omits RF frequency
and hardware calibration; confirm the provisional 3.5 GHz setting. Antenna,
filter, timing and matched-filter-estimator effects remain comparison limitations.

Tests use synthetic records without downloading
data or tracing rays:

```bash
.venv/bin/python -m unittest discover -s tests -p test_ota_comparison.py -v
```

## 10. Create a scene with Blender and OpenStreetMap

Watch NVIDIA Developer's
[Sionna RT: Scene Creation with Blender using OpenStreetMap](https://www.youtube.com/watch?v=7xHLDxUaQ7c).
The video demonstrates the workflow; it predates this project's Sionna RT 2.x
environment, so use the linked add-on documentation for current installation
and menu details. Sionna's [introduction](https://nvlabs.github.io/sionna/rt/tutorials/Introduction.html)
also describes this scene-creation route.

```text
OpenStreetMap area → Blender geometry + radio materials → Mitsuba XML + PLY meshes → RT block
```

1. **Install the scene-authoring tools separately.** Install
   [Blender](https://www.blender.org/download/),
   [Blosm, formerly Blender-OSM](https://github.com/vvoovv/blosm), and
   [Mitsuba-Blender](https://github.com/mitsuba-renderer/mitsuba-blender).
   Blosm imports map geometry; Mitsuba-Blender exports the scene format Sionna
   reads. Choose a Blender version supported by both add-ons, install their ZIP
   releases through Blender's add-on preferences, and enable them. Configure
   Blosm's download directory and Mitsuba-Blender's dependencies as their guides
   specify. These are authoring tools, not additional block runtime dependencies;
   do not point Blender at this project's Python `site-packages`.

2. **Import a small area containing both nodes and nearby reflecting surfaces.**
   Open Blender's **Blosm** sidebar (`N`), choose **OpenStreetMap**, and use
   **select → paste → import** to choose the geographic bounding box. If terrain
   is needed, import it first for the same area, then import buildings onto it.
   For a flat baseline, add an explicit ground mesh if ground reflection is
   intended; a Blender grid is not a physical surface. Start with a small area
   to keep geometry manageable. See the
   [Blosm import instructions](https://github.com/vvoovv/blosm/wiki/Documentation#openstreetmap-import-from-the-server).

3. **Check dimensions and establish the coordinate frame.** Work with one
   scene unit equal to one metre, with Z up; use Metric units and Unit Scale 1
   and verify a known distance. Check building heights, roofs, and ground
   elevation instead of assuming imported geometry is a surveyed model.
   Record the map bounds, geographic origin/projection, and any translations
   or rotations you apply. Choose TX/RX points in this final local frame, above
   terrain and outside solid objects. For example, `[0,0,2]` and `[10,0,2]`
   are 10 m apart and 2 m above a flat ground at z = 0; they are not GPS values.
   Do not export node-marker meshes as obstacles: the block creates the nodes.

4. **Assign radio materials to every exported surface.** In Blender's material
   properties, assign names such as `itu_concrete`, `itu_brick`, `itu_glass`,
   `itu_metal`, or `itu_medium_dry_ground`, according to the intended surface.
   Use the exact names from the
   [Sionna radio-material list](https://nvlabs.github.io/sionna/rt/api/radio_materials.html),
   without accidental suffixes such as `.001`. The exporter typically produces
   IDs such as `mat-itu_concrete`; Sionna RT 2.0.1 recognizes these as ITU radio
   materials. Check the exported assignments against the
   [FAU XML example](python/rt_channel_emulation/scenes/FAU_scene/FAU_scene.xml).
   Colors/textures are not measured electromagnetic properties. Review material
   assumptions and slab thickness: a visually solid building is not automatically
   a volumetric wall model. Custom material parameters must be saved in the XML;
   the block reloads it for each snapshot.

5. **Export a self-contained Mitsuba scene.** Save the editable `.blend` file.
   On an export copy, convert required geometry to meshes, check face normals,
   and use **Object → Apply → All Transforms** to bake object transforms into
   mesh data ([Blender transform guide](https://docs.blender.org/manual/en/3.6/scene_layout/object/editing/apply.html)).
   Use **File → Export → Mitsuba (.xml)**, enable **Export IDs**, and disable
   **Split File**. Preserve the chosen Z-up coordinate frame. Keep the XML and
   its referenced assets together, with relative mesh paths; for example:

   ```text
   my_scene/
     scene.xml
     meshes/
       buildings.ply
       ground.ply
   ```

   For this project's lightweight viewer, export PLY geometry with world-space
   vertices and no per-shape XML `<transform>` elements. Do not simply delete a
   transform: bake it into the vertices and re-export. The viewer does not resolve
   split-file includes or scene instances. These are
   [viewer limitations](python/rt_channel_emulation/gui_launcher.py), not general
   Sionna limitations. See the
   [Mitsuba export guide](https://github.com/mitsuba-renderer/mitsuba-blender/wiki/Exporting-a-Blender-scene).

6. **Check the exported scene before running GRC.** Configure LLVM as in step 3
   of the installation instructions, then run this from the repository root,
   replacing the XML path. It loads materials and viewer geometry without
   opening a window or computing paths:

   ```bash
   .venv/bin/python - "/absolute/path/my_scene/scene.xml" <<'PY'
   from pathlib import Path
   import sys
   from sionna.rt import load_scene
   from rt_channel_emulation.gui_launcher import load_scene_geometry

   path = Path(sys.argv[1]).expanduser().resolve(strict=True)
   scene = load_scene(str(path), merge_shapes=False)
   scene.frequency = 2.4e9  # Replace with the experiment's carrier frequency.
   geometry = load_scene_geometry(path)
   print("Scene:", path)
   print("Objects:", len(scene.objects), "Viewer faces:", len(geometry.polygons))
   for name, obj in scene.objects.items():
       print(name, "->", obj.radio_material.name)
   PY
   ```

   Resolve missing mesh files, unsupported transforms, and unrecognized materials
   before proceeding. Successful loading checks compatibility, not RF accuracy.

7. **Use the scene in the existing demo.** Stop the flowgraph and any position
   sender. Set the RT block's **Scene Path** to the absolute `scene.xml` path,
   set **Initial TX/RX Position (m)** to your local coordinates, and configure
   the carrier frequency. Enable **Visualize Scene and Paths**, then run and
   inspect node placement and the LS/RT tap display. Keep the demo's shared tap
   length and sample rate consistent. Restart after editing scene geometry so
   the viewer reloads its cached meshes. No new scene alias or Python code is
   needed for an external XML path. Adding a distributable bundled scene also
   requires updating the asset patterns in [pyproject.toml](pyproject.toml).

Keep the `.blend`, exported XML/meshes, import date, geographic bounds,
coordinate mapping, and material assumptions with the experiment. Include
OpenStreetMap contributor attribution and follow the
[OpenStreetMap license requirements](https://www.openstreetmap.org/copyright)
when sharing derived scenes or figures; check any separate terrain-data license.

## Conclusion

Maintain the block under `python/rt_channel_emulation/`,
its GRC definition under `grc/`, and only `channel_estimation_demo.grc` as demo
source. The scripts install, launch, or supply positions; generated Python and
local experiment artifacts are not maintained source.
Development conventions and agent verification commands are in [AGENTS.md](AGENTS.md).
