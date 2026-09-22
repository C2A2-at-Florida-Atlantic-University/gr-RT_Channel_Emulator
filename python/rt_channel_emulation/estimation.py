"""Precompute the known-probe weights used by the GRC demo's LS stages."""

from __future__ import annotations

import numpy as np


def glfsr_correlation_spectrum(probe, num_taps: int, periods: int = 1) -> tuple:
    """Return fixed FFT weights for the standard-block periodic LS example.

    Inputs: one repeating +/-1 GLFSR period x of length N, L unknown taps,
    and M consecutive periods summed by GNU Radio's vector Integrate block.
    Output: conj(FFT(x))/(N*M), used by vector Multiply Const before IFFT.
    GNU Radio's inverse FFT is unnormalized: 1/N gives correlation, and 1/M
    averages the M periods. These weights depend ONLY on the known probe.

    Require periodic autocorrelation [N, -1, ..., -1]. Then the LS matrix
    X[n,j] = x[(n-j) mod N] has X^H X = (N+1)I - 11^T for its L columns.
    For r = X^H y, the remaining standard blocks compute exactly
    h[j] = (r[j] + sum(r[:L])/(N+1-L))/(N+1), j=0..L-1.
    This is L-tap least squares, not merely correlation or a truncated
    N-tap inverse. The tests compare it with numpy.linalg.lstsq under noise.
    """
    probe = np.asarray(probe, dtype=np.complex128)
    if probe.ndim != 1 or len(probe) < 2 or not np.isin(probe, [-1, 1]).all():
        raise ValueError("probe must be a one-dimensional +/-1 sequence")
    if not isinstance(num_taps, int) or not 1 <= num_taps <= len(probe):
        raise ValueError("num_taps must be between 1 and the probe period")
    if not isinstance(periods, int) or periods < 1:
        raise ValueError("periods must be a positive integer")
    spectrum = np.fft.fft(probe)
    correlation = np.fft.ifft(np.abs(spectrum)**2)
    expected = np.full(len(probe), -1.0)
    expected[0] = len(probe)
    if not np.allclose(correlation, expected, rtol=0, atol=1e-8):
        raise ValueError("probe must have periodic autocorrelation [N, -1, ..., -1]")
    return tuple(np.conj(spectrum) / (len(probe) * periods))
