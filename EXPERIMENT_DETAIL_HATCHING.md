# Experimental inherited TechDraw detail hatching

This is an openly AI-assisted experiment for [FreeCAD issue #17090](https://github.com/FreeCAD/FreeCAD/issues/17090), not an upstream pull request or an officially supported FreeCAD feature. It is developed primarily using an AI coding agent (GPT-6-Astra), with human direction. Human visual verification of this new implementation is still pending; verification of an earlier, deleted experiment is not evidence for this branch.

## Starting point and baseline

- Branch: `experiment/ai-techdraw-detail-hatching` (reused, not recreated).
- Starting commit: `33adf618dcebfb167795a219175e7dbb18f7a353`.
- Initial working tree was clean; local `main` and `origin/main` pointed at the same commit. No reset, push, or pull request was performed.
- Existing build: `build/relWithDebInfo`, Pixi default environment, configured Pixi Clang 23.1.1, macOS Apple Silicon. No compiler substitution.
- User-provided baseline CTest result: 2,174 passing, zero failures, two skipped, eight disabled, 2,184 registered. This was not rerun during the initial investigation.

Issue #17090 still reproduces in the existing unmodified build. The new GUI harness created a box, section, and detail, exported actual SVG and PDF files, and inspected their rendered pixels. In three fresh-process runs, inherited PAT, SVG, and solid-color fill were absent from the detail while present in the section. The retained baseline run is `build/relWithDebInfo/detail-hatch-baseline-05` (ignored local artifacts).

| Display | Section red pixels SVG / PDF | Detail red pixels SVG / PDF |
|---|---:|---:|
| PatHatch | 1234 / 1328 | 0 / 0 |
| SvgHatch | 580 / 327 | 0 / 0 |
| Color | 9216 / 9216 | 0 / 0 |
| Hide | 0 / 0 | 0 / 0 |

The baseline suite ran six test methods and reported six failing inheritance subtests (three modes × two formats), with zero errors. Geometry, section-fill, and hidden-fill controls passed. Export success alone was not considered validation.

## Root cause and implementation direction

Section cut faces are stored separately from ordinary projected faces. `QGIViewSection` renders those regions with the section's hatch properties. A `DrawViewDetail` reconstructs its shape from the section but uses `QGIViewPart`, which has no inherited section-face rendering pass.

Current main also contains the v26.3 face finder and void identification. The experiment must handle its deferred section-face construction, rather than assuming the historical implementation still applies unchanged.

The implementation keeps authoritative OCC cut topology through geometric association and PAT trimming. Detail coverage is determined geometrically, not by matching parent/detail face numbers. Explicit detail hatches subtract their coverage from inherited regions. A per-detail completed-geometry cache is independent of hatch style. The section and detail share the existing cut-surface renderer; ordinary face selection remains separate from decorative hatch faces.

## Build and regression commands

Reuse the configured Pixi environment and build tree:

```sh
pixi run --frozen cmake --build build/relWithDebInfo \
  --target TechDraw TechDrawGui TechDraw_tests_run -j 6
pixi run --frozen ctest --test-dir build/relWithDebInfo \
  -R 'DetailHatch|DrawViewDetailHatching|TestLineFormat' --output-on-failure
```

The targeted build succeeds. The latest focused CTest run (`detail-hatch-ctest-03.log`) passed all 39 tests in 9.32 seconds. Adding source/test files caused normal CMake regeneration within the existing build; no clean build or compiler reconfiguration was performed.

The GUI regression launcher runs scenarios in separate bounded FreeCAD processes with isolated preferences. It uses Qt to render SVG and installed Poppler `pdftoppm` to inspect PDF output. It requires a usable GUI session on macOS. It rejects reused output directories to avoid stale-artifact false positives.

```sh
pixi run --frozen --environment default python \
  src/Mod/TechDraw/TDTest/DrawViewDetailHatchGuiTest.py --list-cases

pixi run --frozen --environment default python \
  src/Mod/TechDraw/TDTest/DrawViewDetailHatchGuiTest.py \
  --freecad build/relWithDebInfo/bin/FreeCAD \
  --output-dir build/relWithDebInfo/detail-hatch-check-01 \
  --case baseline --timeout 120
```

Use `--case all` for all scenarios, or repeat `--case` to select scenarios. Each scenario produces runtime metadata, test results, exports, and rendered images. The first fixed-build GUI run (`detail-hatch-fixed-03`) passed 15 of 16 methods and 316 of 324 subtests, with no errors, skips, or timeouts. The eight failures are SVG-pattern rotation/offset exports in both section and detail; see below. Later robustness changes and a new transform scenario await final GUI validation.

## Work checkpoint

Implementation and focused C++ regression coverage are complete; final GUI and broader existing-suite validation are pending. New files are included in local Git checkpoints. No new human visual acceptance is claimed.

A source-confirmed pre-existing limitation is retained rather than fixed outside scope: SVG hatch rotation and offset work in the live scene but are lost in SVG/PDF exports for both ordinary sections and details. `QGIFace::buildPixHatch()` has a commented-out transform and loads the untransformed pixmap. The relevant exporter functions are byte-identical to the starting commit. The historical baseline only used zero rotation/offset, so this is not claimed as a historical runtime reproduction of that separate defect. The stricter regression assertions remain failing, not skipped or marked expected failures.
