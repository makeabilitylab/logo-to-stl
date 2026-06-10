#!/usr/bin/env python3
"""
logo_to_stl.py — Turn the geometric Makeability Lab logo (SVG) into a 3D-printable STL.

WHAT THIS DOES
--------------
The Makeability Lab "ML" mark is a tessellation of right triangles on a regular
72-unit grid: 16 triangles form the "L", 12 form the "M" wings, and 4 are black
accent cutouts. This script reads those triangles straight from the SVG and
extrudes each into a prism using a per-region (optionally jittered) height.
Adjacent prisms share vertical faces and a common z=0 base, so a slicer fuses
them into a single printable object — no boolean unions required.

WHY IT'S WRITTEN THIS WAY
-------------------------
The geometry is exact and grid-aligned, so a small parametric script gives
precise, reproducible control over heights (including "semi-random" ones via a
seeded RNG) that would be tedious to reproduce by hand in a GUI CAD tool. The
core path uses ONLY the Python standard library — no numpy, no CAD kernel: we
parse the SVG with xml.etree and write a binary STL with struct. That keeps the
repo trivial to clone-and-run and easy to read for teaching.

INPUT ASSUMPTIONS (about the SVG)
---------------------------------
This script is intentionally specific to the Makeability Lab logo SVGs in svg/.
It assumes:
  1. Printable geometry is expressed as <polygon> elements whose `points` are
     absolute coordinates (no transforms on ancestors). The lab SVGs satisfy
     this; an arbitrary SVG may not.
  2. Each printable face is a TRIANGLE. Polygons that don't reduce to exactly
     three distinct vertices are skipped. (The sources contain some degenerate
     polygons with a repeated vertex — these are collapsed first.)
  3. A triangle's REGION comes from the id of its nearest ancestor <g>:
        - id contains "Black"          -> "black" (accent cutouts)
        - id starts with "L_"          -> "L"
        - id contains "M_Inner_Fills"  -> "M"
     Other group ids (outlines, construction helpers, the duplicate
     "Original_Colors"/"Outlines" layers) are ignored. Triangles duplicated
     across layers are de-duplicated, so each unique triangle extrudes once.
  4. <line>, <polyline>, and <path> elements are ignored. The "with-text" SVG
     stores the "Makeability Lab" wordmark as <path> outlines; those are NOT
     extruded (see README "Roadmap"). The script warns when it skips them so the
     omission is never silent.
  5. SVG y grows downward; we flip y so the printed model is not mirrored.
  6. Coordinates are arbitrary SVG units; output size is set by --width-mm, which
     scales x and y uniformly from the geometry's bounding box.

USAGE
-----
    python3 logo_to_stl.py svg/makeability-logo.svg
    python3 logo_to_stl.py svg/makeability-logo.svg -o stl/logo.stl --width-mm 120
    python3 logo_to_stl.py svg/makeability-logo.svg --m-jitter 4 --black-height 2 -v

Run `python3 logo_to_stl.py --help` to see every knob.
"""

import sys
import math
import struct
import random
import argparse
import xml.etree.ElementTree as ET
from collections import defaultdict


# --------------------------------------------------------------------------- #
# SVG parsing
# --------------------------------------------------------------------------- #

def _local(tag):
    """Strip the XML namespace: '{http://...}polygon' -> 'polygon'."""
    return tag.split('}')[-1]


def _parse_points(points_attr):
    """Parse an SVG `points` string into [(x, y), ...] floats.

    Accepts either "x0,y0 x1,y1 ..." or "x0 y0 x1 y1 ..."; commas and
    whitespace are treated identically.
    """
    nums = list(map(float, points_attr.replace(',', ' ').split()))
    return list(zip(nums[0::2], nums[1::2]))


def _dedupe_vertices(points):
    """Collapse consecutive duplicate vertices and a closing duplicate.

    The source SVGs contain degenerate polygons such as
    `144 144, 144 144, 144 216, 216 216` (first vertex listed twice). After this
    step that becomes a clean 3-vertex triangle.
    """
    out = []
    for p in points:
        if not out or out[-1] != p:
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out


def _region_for_group(group_id):
    """Map a <g> id to a logical region, or None if the group isn't printable.

    See assumption (3) in the module docstring for the matching rules.
    """
    if 'Black' in group_id:
        return 'black'
    if group_id.startswith('L_'):
        return 'L'
    if 'M_Inner_Fills' in group_id:   # excludes *_Outlines and *_Original_Colors
        return 'M'
    return None


def load_triangles(svg_path):
    """Read `svg_path` and return (triangles, warnings).

    triangles : dict {region: [ ((x, y), (x, y), (x, y)), ... ]}, de-duplicated.
    warnings  : list[str] of non-fatal notes (e.g. ignored <path> text outlines).
    """
    root = ET.parse(svg_path).getroot()
    found = defaultdict(dict)   # region -> {sorted-vertex key: triangle}
    n_paths = 0

    def walk(element, current_group_id):
        nonlocal n_paths
        for child in element:
            tag = _local(child.tag)
            if tag == 'g':
                # Descend, carrying this group's id as the active region context.
                walk(child, child.get('id', current_group_id))
            elif tag == 'polygon':
                tri = _dedupe_vertices(_parse_points(child.get('points', '')))
                region = _region_for_group(current_group_id)
                if region is not None and len(tri) == 3:
                    # Key on the sorted vertices so identical triangles in
                    # different layers map to one entry.
                    found[region][tuple(sorted(tri))] = tri
            elif tag == 'path':
                n_paths += 1
            # <line> / <polyline> are construction/outline strokes -> ignored.

    walk(root, '(root)')

    warnings = []
    if n_paths:
        warnings.append(
            f"ignored {n_paths} <path> element(s) — likely the 'Makeability Lab' "
            f"wordmark. Text is not extruded (see README 'Roadmap')."
        )
    return {r: list(d.values()) for r, d in found.items()}, warnings


# --------------------------------------------------------------------------- #
# Geometry -> mesh
# --------------------------------------------------------------------------- #

def _unit_normal(a, b, c):
    """Outward unit normal of triangle (a, b, c) for the STL facet record."""
    ux, uy, uz = (b[i] - a[i] for i in range(3))
    vx, vy, vz = (c[i] - a[i] for i in range(3))
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    mag = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return (nx / mag, ny / mag, nz / mag)


def _prism_facets(tri_xy, z_bottom, z_top):
    """Extrude a 2D triangle into a closed triangular prism.

    Returns 8 facets (each a 3-tuple of (x, y, z) vertices): 1 bottom cap,
    1 top cap, and 3 rectangular sides (2 facets each). The 2D triangle is first
    reordered to be counter-clockwise so the top cap's normal points up (+Z) and
    every side faces outward.
    """
    (x0, y0), (x1, y1), (x2, y2) = tri_xy
    signed_area2 = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if signed_area2 < 0:                       # clockwise -> swap to CCW
        tri_xy = [tri_xy[0], tri_xy[2], tri_xy[1]]

    bottom = [(x, y, z_bottom) for (x, y) in tri_xy]
    top = [(x, y, z_top) for (x, y) in tri_xy]

    facets = [
        (bottom[0], bottom[2], bottom[1]),     # bottom cap (reversed -> faces -Z)
        (top[0], top[1], top[2]),              # top cap (faces +Z)
    ]
    for i in range(3):                         # three side quads, CCW outward
        j = (i + 1) % 3
        facets.append((bottom[i], bottom[j], top[j]))
        facets.append((bottom[i], top[j], top[i]))
    return facets


def build_facets(triangles, region_heights, width_mm, seed, base_mm):
    """Turn region triangles into a flat list of 3D facets.

    triangles      : {region: [triangle, ...]} in SVG units.
    region_heights : {region: (base_mm, jitter_mm)}.
    width_mm       : target overall width; sets the SVG-unit -> mm scale.
    seed           : RNG seed so jittered heights are reproducible.
    base_mm        : if > 0, add a solid floor of this thickness under everything.

    Returns (facets, info) where info summarizes the result for --verbose.
    """
    rng = random.Random(seed)

    # Bounding box over all triangles (SVG units) -> uniform scale and y-flip.
    all_pts = [p for tris in triangles.values() for t in tris for p in t]
    xmin = min(x for x, _ in all_pts)
    xmax = max(x for x, _ in all_pts)
    ymax = max(y for _, y in all_pts)
    scale = width_mm / (xmax - xmin)

    def to_mm(pt):
        x, y = pt
        return ((x - xmin) * scale, (ymax - y) * scale)   # shift to origin, flip y

    facets = []
    for region, tris in triangles.items():
        base, jitter = region_heights[region]
        for tri in tris:
            h = base + (rng.uniform(-jitter, jitter) if jitter else 0.0)
            tri_mm = [to_mm(p) for p in tri]
            facets += _prism_facets(tri_mm, 0.0, base_mm + h)

    if base_mm > 0:   # optional solid floor spanning the whole silhouette
        for tris in triangles.values():
            for tri in tris:
                facets += _prism_facets([to_mm(p) for p in tri], 0.0, base_mm)

    zs = [v[2] for f in facets for v in f]
    info = {
        "facets": len(facets),
        "width_mm": width_mm,
        "depth_mm": (ymax - min(y for _, y in all_pts)) * scale,
        "max_height_mm": max(zs),
        "scale": scale,
    }
    return facets, info


# --------------------------------------------------------------------------- #
# STL output
# --------------------------------------------------------------------------- #

def write_binary_stl(facets, path):
    """Write `facets` to a binary STL file.

    Binary STL layout: an 80-byte header, a uint32 triangle count, then per
    facet: 3 floats (normal) + 9 floats (three vertices) + a uint16 attribute
    word. All little-endian.
    """
    with open(path, 'wb') as f:
        f.write(b'\0' * 80)
        f.write(struct.pack('<I', len(facets)))
        for a, b, c in facets:
            f.write(struct.pack('<3f', *_unit_normal(a, b, c)))
            f.write(struct.pack('<3f', *a))
            f.write(struct.pack('<3f', *b))
            f.write(struct.pack('<3f', *c))
            f.write(struct.pack('<H', 0))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Extrude the Makeability Lab triangle logo (SVG) into a 3D-printable STL.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("svg", help="input SVG (e.g. svg/makeability-logo.svg)")
    p.add_argument("-o", "--output",
                   help="output STL path (default: stl/<svg-stem>.stl)")
    p.add_argument("--width-mm", type=float, default=130.0,
                   help="target overall width in mm (scales x and y uniformly)")
    p.add_argument("--seed", type=int, default=42,
                   help="RNG seed for the semi-random (jittered) heights")
    p.add_argument("--base-mm", type=float, default=0.0,
                   help="optional solid floor thickness under the whole model")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="print region counts and final dimensions")

    # Per-region heights in mm. base = flat height; jitter = +/- random range.
    g = p.add_argument_group("region heights (mm)")
    g.add_argument("--l-height", type=float, default=6.0, help="L: base height")
    g.add_argument("--l-jitter", type=float, default=0.0, help="L: +/- random range")
    g.add_argument("--m-height", type=float, default=6.0, help="M: base height")
    g.add_argument("--m-jitter", type=float, default=2.5, help="M: +/- random range")
    g.add_argument("--black-height", type=float, default=2.5, help="cutouts: base height")
    g.add_argument("--black-jitter", type=float, default=0.0, help="cutouts: +/- random range")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    # Derive a default output path from the input stem if none was given.
    if args.output:
        out_path = args.output
    else:
        import os
        stem = os.path.splitext(os.path.basename(args.svg))[0]
        out_path = os.path.join("stl", stem + ".stl")

    triangles, warnings = load_triangles(args.svg)
    for w in warnings:
        print(f"note: {w}", file=sys.stderr)
    if not triangles:
        sys.exit(f"error: no printable triangles found in {args.svg!r}; "
                 f"is this a Makeability Lab logo SVG?")

    region_heights = {
        "L": (args.l_height, args.l_jitter),
        "M": (args.m_height, args.m_jitter),
        "black": (args.black_height, args.black_jitter),
    }
    facets, info = build_facets(
        triangles, region_heights, args.width_mm, args.seed, args.base_mm)

    write_binary_stl(facets, out_path)

    if args.verbose:
        counts = ", ".join(f"{r}={len(t)}" for r, t in sorted(triangles.items()))
        print(f"regions     : {counts}")
        print(f"facets      : {info['facets']}")
        print(f"size (mm)   : {info['width_mm']:.1f} x {info['depth_mm']:.1f} "
              f"x {info['max_height_mm']:.1f}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
