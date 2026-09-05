#!/usr/bin/env python3
"""Generate the three vector PDF figures for the v0.1 preprint.

Pure standard library (hand-written PDF content streams), Helvetica only
(no Chinese fonts), deterministic output (no timestamps, no absolute paths).

Outputs (in this directory):
  fig1_signing_lower_bound.pdf
  fig2_stage_level_ci_crossing.pdf
  fig3_binary_hash_contrast.pdf

Usage: python figures.py
"""

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))

W, H = 600.0, 420.0

# data-derived constants (see reproduce.py / audit.py)
POOLED_SD = 24.312297335941736   # pooled within-candidate paired sd of null deltas
Z_MARGINAL = 1.96
Z_DECISIVE = 3.0 * 1.96


# ------------------------------------------------------------------ pdf core
def esc(s):
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf(draw_cmds, width=W, height=H):
    """draw_cmds: list of content-stream strings."""
    stream = "\n".join(draw_cmds)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>" % (
            width, height),
        "<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        "/Encoding /WinAnsiEncoding >>",
    ]
    out = ["%PDF-1.4"]
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len("\n".join(out)) + 1)
        out.append(f"{i} 0 obj")
        out.append(obj)
        out.append("endobj")
    xref_pos = len("\n".join(out)) + 1
    n = len(objects) + 1
    out.append("xref")
    out.append(f"0 {n}")
    out.append("0000000000 65535 f ")
    for off in offsets:
        out.append(f"{off:010d} 00000 n ")
    out.append("trailer")
    out.append(f"<< /Size {n} /Root 1 0 R >>")
    out.append("startxref")
    out.append(str(xref_pos))
    out.append("%%EOF")
    return "\n".join(out)


def T(x, y, size, s):
    return f"BT /F1 {size:.1f} Tf {x:.2f} {y:.2f} Td ({esc(s)}) Tj ET"


def TR(x, y, size, s):
    """Render text rotated 90 degrees counter-clockwise."""
    return (f"BT /F1 {size:.1f} Tf 0 1 -1 0 {x:.2f} {y:.2f} Tm "
            f"({esc(s)}) Tj ET")


def L(x1, y1, x2, y2, w=1.0):
    return f"{w:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"


def P(pts, w=1.0):
    parts = [f"{w:.2f} w {pts[0][0]:.2f} {pts[0][1]:.2f} m"]
    for x, y in pts[1:]:
        parts.append(f"{x:.2f} {y:.2f} l")
    parts.append("S")
    return "\n".join(parts)


def dot(x, y, r=2.2):
    # PDF has no PostScript-style ``arc`` operator.  Approximate a circle with
    # four cubic Bezier segments so every conforming renderer shows markers.
    k = 0.5522847498 * r
    return (
        f"0 0 0 rg {x + r:.2f} {y:.2f} m "
        f"{x + r:.2f} {y + k:.2f} {x + k:.2f} {y + r:.2f} "
        f"{x:.2f} {y + r:.2f} c "
        f"{x - k:.2f} {y + r:.2f} {x - r:.2f} {y + k:.2f} "
        f"{x - r:.2f} {y:.2f} c "
        f"{x - r:.2f} {y - k:.2f} {x - k:.2f} {y - r:.2f} "
        f"{x:.2f} {y - r:.2f} c "
        f"{x + k:.2f} {y - r:.2f} {x + r:.2f} {y - k:.2f} "
        f"{x + r:.2f} {y:.2f} c f 0 0 0 RG"
    )


def gray(g):
    return f"{g:.2f} {g:.2f} {g:.2f} RG"


# ------------------------------------------------------------------ figure 1
def fig1():
    # axes mapping: n in [0, 60] -> x; q in [0, 1] -> y
    x0, x1 = 80.0, 540.0
    y0, y1 = 55.0, 265.0
    def X(n):
        return x0 + (x1 - x0) * (n / 60.0)
    def Y(q):
        return y0 + (y1 - y0) * q

    cmds = []
    cmds.append(gray(0.0))
    cmds.append(T(80, 317, 13, "95% one-sided lower bound on q from an "
                               "all-positive sequence: 0.05^(1/n)"))
    cmds.append(T(80, 298, 9, "sign_min = 0.9 requires n >= "
                               "ln(0.05)/ln(0.9) = 28.4332 -> n_min = 29"))
    # axes
    cmds.append(L(x0, y0, x1, y0, 1.0))
    cmds.append(L(x0, y1, x0, y0, 1.0))
    for n in range(0, 61, 10):
        cmds.append(L(X(n), y0, X(n), y0 + 4, 0.8))
        cmds.append(T(X(n) - 4, y0 - 14, 8, str(n)))
    for q in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        cmds.append(L(x0, Y(q), x0 - 4, Y(q), 0.8))
        cmds.append(T(x0 - 30, Y(q) - 3, 8, f"{q:.1f}"))
    cmds.append(T(292, 16, 10, "sequence length n"))
    cmds.append(TR(24, 112, 10, "lower confidence bound q"))
    # curve
    pts = [(X(n), Y(0.05 ** (1.0 / n))) for n in range(1, 61)]
    cmds.append(P(pts, 1.4))
    # 0.9 line
    cmds.append(gray(0.4))
    cmds.append(L(x0, Y(0.9), x1, Y(0.9), 0.8))
    cmds.append(T(x1 - 130, Y(0.9) + 4, 8, "sign_min = 0.9"))
    # n=29 vertical
    cmds.append(L(X(29), y0, X(29), Y(0.9), 0.8))
    cmds.append(T(X(29) + 4, Y(0.9) - 12, 8, "n = 29"))
    # budget markers
    for n in (4, 8, 12):
        cmds.append(dot(X(n), Y(0.05 ** (1.0 / n)), 3.0))
        cmds.append(T(X(n) + 4, Y(0.05 ** (1.0 / n)) - 2, 8, str(n)))
    cmds.append(T(x0 + 10, y0 + 18, 8,
                  "frozen escalation budget markers: 4, 8, 12"))
    return build_pdf(cmds, height=340.0)


# ------------------------------------------------------------------ figure 2
def fig2():
    # log-log: x = delta - delta_min (ms) in [0.3, 30]; y = pairs in [1, 1e4]
    x0, x1 = 90.0, 540.0
    y0, y1 = 55.0, 265.0
    g_lo, g_hi = 0.3, 30.0
    p_lo, p_hi = 1.0, 10000.0
    def X(g):
        return x0 + (x1 - x0) * (math.log(g) - math.log(g_lo)) / (
            math.log(g_hi) - math.log(g_lo))
    def Y(p):
        return y0 + (y1 - y0) * (math.log(p) - math.log(p_lo)) / (
            math.log(p_hi) - math.log(p_lo))

    cmds = []
    cmds.append(gray(0.0))
    cmds.append(T(80, 317, 13, "Stage-level CI crossing approximation "
                               "(single-stage conditions only)"))
    cmds.append(T(80, 298, 9, "excludes permutation, sign_consistency, the "
                               "sequential ladder and replication; "
                               "not a promotion-path cost ratio"))
    # axes
    cmds.append(L(x0, y0, x1, y0, 1.0))
    cmds.append(L(x0, y1, x0, y0, 1.0))
    for g in (0.3, 0.5, 1, 2, 5, 10, 20, 30):
        cmds.append(L(X(g), y0, X(g), y0 + 4, 0.8))
        cmds.append(T(X(g) - 10, y0 - 14, 8, f"{g:g}"))
    for p in (1, 10, 100, 1000, 10000):
        cmds.append(L(x0, Y(p), x0 - 4, Y(p), 0.8))
        cmds.append(T(x0 - 34, Y(p) - 3, 8, f"{p:g}"))
    cmds.append(T(276, 16, 10, "true effect gap delta - delta_min (ms, log)"))
    cmds.append(TR(26, 112, 10, "pairs needed (log)"))
    # curves
    samples = 80
    for z, width in ((Z_MARGINAL, 1.0), (Z_DECISIVE, 1.8)):
        pts = []
        for i in range(samples):
            g = g_lo * (g_hi / g_lo) ** (i / (samples - 1))
            n = (z * POOLED_SD / g) ** 2
            n = max(p_lo, min(p_hi, n))
            pts.append((X(g), Y(n)))
        cmds.append(P(pts, width))
    # legend
    cmds.append(L(105, 125, 130, 125, 1.8))
    cmds.append(T(138, 121, 9, "decisive: gap >= 3h (5.88 SE)"))
    cmds.append(L(105, 108, 130, 108, 1.0))
    cmds.append(T(138, 104, 9, "marginal: ci_low > delta_min (1.96 SE)"))
    cmds.append(T(105, 87, 8, "pooled paired sd = 24.312 ms (244 null "
                               "pairs); ratio 9 at every gap"))
    return build_pdf(cmds, height=340.0)


# ------------------------------------------------------------------ figure 3
def fig3():
    n = 31
    x0, x1 = 80.0, 540.0
    y0, y1 = 55.0, 165.0
    def X(i):
        return x0 + (x1 - x0) * (i / (n - 1))
    cmds = []
    cmds.append(gray(0.0))
    cmds.append(T(80, 237, 13, "Binary identity of candidate and control "
                               "executables per candidate"))
    cmds.append(T(80, 218, 9, "30 null candidates: one single shared hash "
                               "6e50bccc4189c8aa...; real candidate differs: "
                               "52d798dec09f688f..."))
    cmds.append(L(x0, y0, x1, y0, 1.0))
    cmds.append(L(x0, y0, x0, y1, 1.0))
    for i in range(0, n, 5):
        cmds.append(L(X(i), y0, X(i), y0 + 4, 0.8))
        cmds.append(T(X(i) - 4, y0 - 14, 8, str(i + 1)))
    cmds.append(L(x0, y1, x1, y1, 0.5))
    cmds.append(T(x0 - 18, y0 - 3, 8, "0"))
    cmds.append(T(x0 - 18, y1 - 3, 8, "1"))
    cmds.append(T(x0 + 10, y1 - 20, 9, "1 = byte-identical binary"))
    cmds.append(T(x0 + 10, y0 + 12, 9, "0 = different binary"))
    for i in range(n):
        ident = 1 if i < 30 else 0
        cmds.append(dot(X(i), y0 + (y1 - y0) * ident, 2.4))
    cmds.append(T(X(30) - 92, y0 + 24, 8, "fmt_buffer_reuse (real)"))
    return build_pdf(cmds, height=260.0)


def main():
    for name, build in (
            ("fig1_signing_lower_bound.pdf", fig1),
            ("fig2_stage_level_ci_crossing.pdf", fig2),
            ("fig3_binary_hash_contrast.pdf", fig3)):
        data = build()
        with open(os.path.join(HERE, name), "w", encoding="ascii",
                  newline="\n") as fh:
            fh.write(data)
        assert data.startswith("%PDF-1.4") and data.rstrip().endswith("%%EOF")
        assert " arc " not in data
        print(f"wrote {name} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
