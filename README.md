# logo-to-stl

Turn the geometric **Makeability Lab** logo (SVG) into a 3D-printable **STL**, with
independent extrusion heights per region.

The "ML" mark is a tessellation of right triangles on a 72-unit grid — 16 form
the **L**, 12 form the **M** wings, and 4 are black accent **cutouts**. This tool
reads those triangles from the SVG and extrudes each into a prism at a height you
control per region, including optional "semi-random" jitter. Because the
triangles share edges and a common base plane, a slicer fuses them into a single
printable object.

## Why a script (and not Fusion/TinkerCad)

The geometry is exact and grid-aligned, so a small parametric script gives
precise, reproducible height control that's tedious to reproduce by hand in a GUI
— and it doubles as a teaching example of generative/parametric design. The core
has **no third-party dependencies**: it parses the SVG with the standard library
and writes a binary STL by hand.

## Quick start

```bash
# (optional) isolate, though the core needs no third-party packages
python3 -m venv .venv && source .venv/bin/activate

# generate stl/makeability-logo.stl with the defaults
python3 logo_to_stl.py svg/makeability-logo.svg -v
```

Open the resulting STL in any slicer (PrusaSlicer, Cura, Bambu Studio, OrcaSlicer).

## Options

```
python3 logo_to_stl.py SVG [-o OUT] [--width-mm W] [--seed N] [--base-mm T] [-v]
                            [--l-height H] [--l-jitter J]
                            [--m-height H] [--m-jitter J]
                            [--black-height H] [--black-jitter J]
```

| Flag             | Default | Meaning                                            |
|------------------|---------|----------------------------------------------------|
| `svg`            | —       | input SVG (positional)                             |
| `-o, --output`   | `stl/<name>.stl` | output STL path                           |
| `--width-mm`     | `130`   | overall width in mm (scales x and y uniformly)     |
| `--seed`         | `42`    | RNG seed for jittered heights (reproducible)       |
| `--base-mm`      | `0`     | optional solid floor under the whole model         |
| `--l-height`     | `6`     | L: flat height (mm)                                |
| `--l-jitter`     | `0`     | L: ± random range (mm)                             |
| `--m-height`     | `6`     | M: flat height (mm)                                |
| `--m-jitter`     | `2.5`   | M: ± random range (mm) — the "semi-random" look    |
| `--black-height` | `2.5`   | cutouts: flat height (mm), recessed by default     |
| `--black-jitter` | `0`     | cutouts: ± random range (mm)                       |
| `-v, --verbose`  | off     | print region counts and final dimensions           |

Examples:

```bash
# taller, more varied M wings, on a 1.5 mm base plate
python3 logo_to_stl.py svg/makeability-logo.svg --m-jitter 4 --base-mm 1.5 -v

# flat two-tone (L/M one height, cutouts lower), 100 mm wide
python3 logo_to_stl.py svg/makeability-logo.svg --m-jitter 0 --width-mm 100
```

## How regions are detected

Each triangle's region is read from the id of its nearest ancestor `<g>`:

- id contains `Black` → **cutouts**
- id starts with `L_` → **L**
- id contains `M_Inner_Fills` → **M**

Other groups (outlines, construction helpers, duplicate color layers) and all
`<line>`/`<polyline>`/`<path>` elements are ignored. See the module docstring in
`logo_to_stl.py` for the full list of input assumptions.

## Printing notes

- Output prints as one piece: triangles share edges and a z=0 base, so the slicer
  unions them. The mesh is **not strictly watertight** (prisms meet at coincident
  internal walls), which slicers handle fine. If you need a pristine single
  manifold (e.g. for STEP export or strict repair tools), run a boolean union in
  `trimesh`/Blender — see `requirements-dev.txt`.
- Defaults give a 130 × 87 × 8 mm model. Adjust `--width-mm` for your bed/use.

## Roadmap

- **Wordmark text.** The `svg/makeability-logo-with-text.svg` file contains the
  "Makeability Lab" text as `<path>` outlines. Those are font curves with interior
  holes, which need bezier flattening + hole-aware triangulation — a different
  problem from the grid triangles, and finicky to print at logo scale. The mark is
  extruded today; text is intentionally out of scope for v1. A clean future
  approach: parse the paths into polygons-with-holes and extrude them as one flat
  layer via `trimesh.creation.extrude_polygon`.
- Optional STEP/3MF export.

## Repo layout

```
logo-to-stl/
├── logo_to_stl.py            # the tool (stdlib only)
├── requirements.txt          # runtime deps (none) — notes only
├── requirements-dev.txt      # optional: trimesh for validation/preview
├── svg/
│   ├── makeability-logo.svg            # the ML mark (no text)
│   └── makeability-logo-with-text.svg  # mark + wordmark paths
├── stl/                      # generated output (git-ignored)
├── LICENSE
└── README.md
```

## License

MIT — see [LICENSE](LICENSE).
