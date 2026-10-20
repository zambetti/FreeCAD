# SPDX-License-Identifier: LGPL-2.1-or-later

r"""Isolated GUI/export regression for https://github.com/FreeCAD/FreeCAD/issues/17090.

Run from the repository root, without rebuilding or installing this test::

    pixi run --frozen --environment default python \
        src/Mod/TechDraw/TDTest/DrawViewDetailHatchGuiTest.py \
        --freecad build/relWithDebInfo/bin/FreeCAD \
        --output-dir build/relWithDebInfo/detail-hatch-baseline

The output directory must be new (no stale exports/results). This launcher starts
that exact GUI executable with private FreeCAD configuration/data directories,
executes this source file through a generated macro, and enforces a timeout. It
never attaches to an already-running FreeCAD. Qt 6's SVG renderer and Poppler's
pdftoppm inspect the actual exported files, not a screenshot or just their
existence. Requires pdftoppm on PATH (or --pdftoppm), but no extra Python packages,
installed test modules, RPC server, or CMake changes.

Exit codes: 0 = all assertions passed; 1 = regression assertion failure;
2 = setup/export/render/process error; 124 = child process timeout. Missing hatch
inheritance is intentionally a failure, not an expectedFailure or a skip.

The default --case baseline retains the quick presence/absence reproducer.
Use --list-cases for the additional scenarios in DrawViewDetailHatchGuiCases.py.
Repeat --case to select scenarios, or use --case all. Each scenario gets its own
GUI process, configuration, artifacts and timeout; failures do not stop later
scenarios. --dry-run prints the plan without launching FreeCAD or writing files.

Style-only cases inspect the live QGraphicsScene BEFORE export (exporters refresh
views), without recompute(), touch(), or requestPaint() after style edits. The
selection case uses QTest mouse events, never synthetic Selection.addSelection().
Qt 6/PySide6, the built GUI and pdftoppm are required. The current pixi environment
has QtPdf bindings but lacks the native QtPdf library, so PDF inspection uses the
already-installed Poppler instead. Real GUI cases need a usable screen/WindowServer;
this macOS terminal sandbox required GUI access approval.
New scenarios must be runtime-validated once the implementation build is ready;
listing/counting cases is not evidence that their assertions have passed.
"""

import argparse
import hashlib
import json
import os
import runpy
import shlex
import signal
import shutil
import subprocess
import sys
import time
import traceback
import unittest
from pathlib import Path
from typing import ClassVar


PAGE_MM = (200, 120)
PIXELS_PER_MM = 8
POSITIONS = {"section": (85, 60), "detail": (150, 60)}
HATCH_COLOR = (0.85, 0.10, 0.10)
MIN_HATCH_FRACTION = 0.005
MODES = ("PatHatch", "SvgHatch", "Color", "Hide")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def fingerprint(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}


def scenario_registry():
    helper = Path(__file__).with_name("DrawViewDetailHatchGuiCases.py")
    extra = runpy.run_path(str(helper))
    return {"baseline": (DetailHatchExportTests, "Quick section/detail SVG/PDF controls"),
            **extra["SCENARIOS"]}, extra


def launch():
    registry, _ = scenario_registry()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freecad", type=Path,
                        help="Existing GUI executable, not FreeCADCmd")
    parser.add_argument("--output-dir", type=Path,
                        help="New artifact directory (prefer an ignored build directory)")
    parser.add_argument("--pdftoppm", type=Path, default=shutil.which("pdftoppm"),
                        help="Poppler PDF renderer (default: pdftoppm on PATH)")
    parser.add_argument("--timeout", type=float, default=120,
                        help="Maximum child runtime in seconds (default: 120)")
    parser.add_argument("--case", action="append", choices=[*registry, "all"],
                        help="Repeatable scenario selector (default: baseline)")
    parser.add_argument("--list-cases", action="store_true", help="List test counts without a GUI")
    parser.add_argument("--dry-run", action="store_true", help="Print plan; do not launch or write")
    args = parser.parse_args()
    if args.list_cases:
        total = 0
        for name, (test_class, description) in registry.items():
            count = len(unittest.defaultTestLoader.getTestCaseNames(test_class))
            total += count
            print(f"{name:16} {count:2} test methods  {description}")
        print(f"TOTAL: {len(registry)} scenarios, {total} test methods (not executed)")
        return 0
    if args.freecad is None or args.output_dir is None:
        parser.error("--freecad and --output-dir are required unless --list-cases is used")
    chosen = args.case or ["baseline"]
    cases = list(registry) if "all" in chosen else list(dict.fromkeys(chosen))
    binary = args.freecad.resolve()
    output = args.output_dir.resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error(f"Not an executable: {binary}")
    if (args.pdftoppm is None or not args.pdftoppm.is_file()
            or not os.access(args.pdftoppm, os.X_OK)):
        parser.error("PDF assertions require pdftoppm on PATH or --pdftoppm /path/to/pdftoppm")
    pdftoppm = args.pdftoppm.resolve()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be finite and positive")
    if output.exists():
        parser.error(f"Use a new --output-dir; refusing to reuse {output}")
    plan = [(case, output if len(cases) == 1 else output / case) for case in cases]
    if args.dry_run:
        for case, directory in plan:
            print(f"PLAN: {case}; GUI={binary}; artifacts={directory}; timeout={args.timeout:g}s")
        print("No GUI launched; no artifacts written.")
        return 0
    if len(cases) > 1:
        output.mkdir(parents=True)
    results = []
    for case, directory in plan:
        code = launch_scenario(binary, pdftoppm, directory, args.timeout, case)
        results.append({"case": case, "exit_code": code, "artifacts": str(directory)})
        if len(cases) > 1:
            write_json(output / "scenarios.json", results)
    codes = [result["exit_code"] for result in results]
    return next((code for code in (124, 2, 1) if code in codes), 0)


def launch_scenario(binary, pdftoppm, output, timeout, case):
    output.mkdir(parents=True)
    for name in ("home", "data", "tmp", "config", "cache"):
        (output / name).mkdir()
    source = Path(__file__).resolve()
    macro = output / "run.FCMacro"
    macro.write_text(
        "import runpy\n"
        "from PySide6 import QtCore\n"
        f"_detail_hatch_test = runpy.run_path({str(source)!r})\n"
        "QtCore.QTimer.singleShot(0, "
        f"lambda run=_detail_hatch_test['run_gui_tests']: "
        f"run({str(output)!r}, {str(pdftoppm)!r}, {case!r}))\n",
        encoding="utf-8",
    )
    command = [str(binary), "--user-cfg", str(output / "user.cfg"),
               "--system-cfg", str(output / "system.cfg"),
               "--log-file", str(output / "FreeCAD.log"), str(macro)]
    private_env = {
        "FREECAD_USER_HOME": str(output / "home"),
        "FREECAD_USER_DATA": str(output / "data"),
        "FREECAD_USER_TEMP": str(output / "tmp"),
        "XDG_CONFIG_HOME": str(output / "config"),
        "XDG_CACHE_HOME": str(output / "cache"),
        "PYTHONUNBUFFERED": "1",
    }
    metadata = {"command": command, "environment": private_env,
                "executable": fingerprint(binary), "test_script": fingerprint(source),
                "launcher_python": sys.executable, "timeout_seconds": timeout,
                "case": case,
                "scenario_script": fingerprint(source.with_name("DrawViewDetailHatchGuiCases.py")),
                "pdftoppm": fingerprint(pdftoppm),
                "svg_pattern": fingerprint(source.with_name("TestHatch.svg"))}
    repo = source.parents[4]
    for key, git_args in (("source_head", ["rev-parse", "HEAD"]),
                          ("source_status", ["status", "--short", "--branch"])):
        result = subprocess.run(["git", "--no-pager", "--no-optional-locks", *git_args],
                                cwd=repo, capture_output=True, text=True, timeout=10, check=False)
        metadata[key] = result.stdout.strip() if result.returncode == 0 else result.stderr
    write_json(output / "launch.json", metadata)
    print(f"SCENARIO: {case}", flush=True)
    print("GUI command:", shlex.join(command), flush=True)
    print("Artifacts:", output, flush=True)
    env = os.environ.copy()
    env.update(private_env)
    with (output / "process.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=(os.name == "posix"))
        except OSError as error:
            print(f"Cannot launch GUI: {error}", file=sys.stderr)
            return 2
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            stop_process(process)
            write_json(output / "timeout.json", {"timeout_seconds": timeout, "case": case})
            print(f"TIMEOUT after {timeout:g}s; see process.log and FreeCAD.log")
            return 124
    result_path = output / "result.json"
    if not result_path.exists():
        print(f"GUI exited {process.returncode} without result.json; see process.log/FreeCAD.log")
        return 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    test_log = output / "tests.log"
    if test_log.exists():
        print(test_log.read_text(encoding="utf-8"), end="")
    for key in ("exception", "cleanup_exception"):
        if key in result:
            print(result[key])
    print(f"SUMMARY: {result['status']}; GUI exit={process.returncode}; "
          f"tests={result.get('tests', 0)}, failures={result.get('failures', 0)}, "
          f"errors={result.get('errors', 0)}")
    # FreeCAD's main() returns zero after the Qt event loop, even for exit(1).
    # The launcher propagates the assertions through result.json instead.
    if process.returncode != 0:
        print("Abnormal GUI exit; treating this as a harness error")
        return 2
    return result["exit_code"]


def stop_process(process):
    # On POSIX, include a possibly active pdftoppm child in timeout cleanup.
    def terminate(force=False):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            elif force:
                process.kill()
            else:
                process.terminate()
        except ProcessLookupError:
            pass

    terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        terminate(force=True)
        process.wait()


def pump_events(milliseconds=50):
    from PySide6 import QtCore

    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def wait_for_views(views, timeout=20):
    """Wait for QtConcurrent work and stable public geometry, not just recompute()."""
    from PySide6 import QtCore

    deadline = time.monotonic() + timeout
    previous = None
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        pump_events()
        signature = tuple(
            (tuple(view.State), tuple(round(edge.Length, 7) for edge in view.getVisibleEdges()))
            for view in views
        )
        ready = (all("Up-to-date" in state and edges for state, edges in signature)
                 and QtCore.QThreadPool.globalInstance().activeThreadCount() == 0)
        if not ready or signature != previous:
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= 0.5:
            return
        previous = signature
    raise TimeoutError(f"Views did not settle: {[(v.Name, v.State) for v in views]}; {previous}")


def make_fixture(output):
    import FreeCAD as App
    import FreeCADGui as Gui
    import Part  # noqa: F401 - Registers Part feature types in the isolated process.

    template_path = output / "blank.svg"
    template_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:freecad="http://www.freecadweb.org/wiki/index.php?title=Svg_Namespace" '
        'width="200mm" height="120mm" viewBox="0 0 200 120"></svg>\n', encoding="utf-8")
    pat_path = output / "diagonal.pat"
    pat_path.write_text("*TD_DIAGONAL, Deterministic section hatch\n45,0,0,0,2.5\n",
                        encoding="utf-8")

    doc = App.newDocument("DetailHatch17090")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 40, 20, 30
    box.Placement.Base = App.Vector(-20, -10, -15)
    page = doc.addObject("TechDraw::DrawPage", "Page")
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = str(template_path)
    page.Template = template
    page.KeepUpdated = True
    page.Scale = 1

    base = doc.addObject("TechDraw::DrawViewPart", "Base")
    base.Source = [box]
    base.Direction = (0, 0, 1)
    base.XDirection = (1, 0, 0)
    page.addView(base)
    # addView() initially centers a view on the page; position it afterwards.
    base.X, base.Y = 30, 60
    doc.recompute()
    wait_for_views([base])

    section = doc.addObject("TechDraw::DrawViewSection", "Section")
    section.Source = [box]
    section.BaseView = base
    section.SectionNormal = (0, -1, 0)
    section.SectionOrigin = (0, 0, 0)
    section.XDirection = (1, 0, 0)
    section.ScaleType = "Custom"
    section.Scale = 1
    section.FileGeomPattern = str(pat_path)
    section.NameGeomPattern = "TD_DIAGONAL"
    section.FileHatchPattern = str(Path(__file__).with_name("TestHatch.svg"))
    section.HatchScale = 1
    section.HatchRotation = 0
    section.HatchOffset = (0, 0, 0)
    section.CutSurfaceDisplay = "PatHatch"
    section.ViewObject.GeomHatchColor = HATCH_COLOR
    section.ViewObject.HatchColor = HATCH_COLOR
    section.ViewObject.CutSurfaceColor = (1.0, 1.0, 1.0)
    section.ViewObject.CutSurfaceTransparency = 0
    section.ViewObject.WeightPattern = 0.35
    page.addView(section)
    section.X, section.Y = POSITIONS["section"]
    doc.recompute()
    wait_for_views([base, section])

    detail = doc.addObject("TechDraw::DrawViewDetail", "Detail")
    detail.BaseView = section
    detail.Direction = section.Direction
    detail.XDirection = section.XDirection
    detail.AnchorPoint = (0, 0, 0)
    detail.Radius = 10
    detail.ScaleType = "Custom"
    detail.Scale = 2
    page.addView(detail)
    detail.X, detail.Y = POSITIONS["detail"]
    doc.recompute()
    wait_for_views([base, section, detail])
    Gui.Selection.clearSelection()
    page.ViewObject.show()
    pump_events(250)
    return doc, page, section, detail


def render_export(path, pdftoppm):
    from PySide6 import QtCore, QtGui, QtSvg

    size = QtCore.QSize(*(int(mm * PIXELS_PER_MM) for mm in PAGE_MM))
    if path.suffix == ".svg":
        renderer = QtSvg.QSvgRenderer(str(path))
        if not renderer.isValid():
            raise RuntimeError(f"Invalid exported SVG: {path}")
        image = QtGui.QImage(size, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtCore.Qt.GlobalColor.white)
        painter = QtGui.QPainter(image)
        try:
            renderer.render(painter)
        finally:
            painter.end()
    else:
        prefix = str(path) + "-poppler"
        conversion = subprocess.run(
            [pdftoppm, "-singlefile", "-f", "1", "-l", "1", "-png",
             "-scale-to-x", str(size.width()), "-scale-to-y", str(size.height()),
             str(path), prefix], capture_output=True, text=True, timeout=20, check=False)
        path.with_suffix(".pdftoppm.log").write_text(conversion.stderr, encoding="utf-8")
        if conversion.returncode != 0:
            raise RuntimeError(f"PDF rendering failed for {path}: {conversion.stderr}")
        image = QtGui.QImage(prefix + ".png")
    if image.isNull():
        raise RuntimeError(f"Renderer returned an empty image: {path}")
    if not image.save(str(path.with_suffix(path.suffix + ".png"))):
        raise RuntimeError(f"Cannot save rendered artifact: {path}")
    return image


def pixel_metrics(image, role):
    """Sample cut-face interiors, excluding outlines, captions and the detail marker."""
    x, y = POSITIONS[role]
    half_side = 6 if role == "section" else 12
    center_x = round(x * PIXELS_PER_MM)
    center_y = round((PAGE_MM[1] - y) * PIXELS_PER_MM)
    half_pixels = round(half_side * PIXELS_PER_MM)
    red = 0
    for py in range(center_y - half_pixels, center_y + half_pixels):
        for px in range(center_x - half_pixels, center_x + half_pixels):
            color = image.pixelColor(px, py)
            red += color.red() > 140 and color.green() < 115 and color.blue() < 115
    total = (2 * half_pixels) ** 2
    black = 0
    # A larger box includes the outlines, proving that each view was exported.
    extent = 23 * PIXELS_PER_MM
    for py in range(center_y - extent, center_y + extent):
        for px in range(center_x - extent, center_x + extent):
            color = image.pixelColor(px, py)
            black += max(color.red(), color.green(), color.blue()) < 100
    return {"red_pixels": red, "sample_pixels": total, "red_fraction": red / total,
            "outline_black_pixels": black,
            "sample_rect_mm": [x - half_side, PAGE_MM[1] - y - half_side,
                               2 * half_side, 2 * half_side]}


class DetailHatchExportTests(unittest.TestCase):
    """Assertions consume measured exports; controls cannot hide missing inheritance."""

    observations: ClassVar[dict]

    def assert_fill(self, role, mode, minimum):
        for fmt, metrics in self.observations[mode].items():
            with self.subTest(format=fmt, role=role, mode=mode):
                sample = metrics[role]
                self.assertGreater(sample["red_fraction"], minimum,
                                   f"{role} has no expected {mode} fill in exported {fmt}: {sample}")
                if mode in ("PatHatch", "SvgHatch"):
                    self.assertLess(sample["red_fraction"], 0.5,
                                    "Expected sparse hatch strokes, not a solid colored fill")

    def test_exported_geometry(self):
        for mode, exports in self.observations.items():
            for fmt, metrics in exports.items():
                for role, sample in metrics.items():
                    with self.subTest(mode=mode, format=fmt, role=role):
                        self.assertGreater(sample["outline_black_pixels"], 20)

    def test_section_positive_controls(self):
        for mode in ("PatHatch", "SvgHatch", "Color"):
            self.assert_fill("section", mode, 0.8 if mode == "Color" else MIN_HATCH_FRACTION)

    def test_detail_inherits_pat_hatch(self):
        self.assert_fill("detail", "PatHatch", MIN_HATCH_FRACTION)

    def test_detail_inherits_svg_hatch(self):
        self.assert_fill("detail", "SvgHatch", MIN_HATCH_FRACTION)

    def test_detail_inherits_cut_color(self):
        self.assert_fill("detail", "Color", 0.8)

    def test_hidden_cut_surface_control(self):
        for fmt, metrics in self.observations["Hide"].items():
            for role, sample in metrics.items():
                with self.subTest(format=fmt, role=role):
                    self.assertEqual(sample["red_pixels"], 0)


def run_baseline(fixture, output, pdftoppm, log):
    import FreeCADGui as Gui
    import TechDrawGui

    doc, page, section, detail = fixture
    observations = {}
    for mode in MODES:
        section.CutSurfaceDisplay = mode
        # Hatch pixels must come from the pattern, never a red background fill.
        section.ViewObject.CutSurfaceColor = HATCH_COLOR if mode == "Color" else (1.0, 1.0, 1.0)
        doc.recompute()
        wait_for_views([section, detail])
        Gui.Selection.clearSelection()
        exports = {}
        for suffix, exporter in (("svg", TechDrawGui.exportPageAsSvg),
                                 ("pdf", TechDrawGui.exportPageAsPdf)):
            path = output / f"{mode}.{suffix}"
            exporter(page, str(path))
            image = render_export(path, pdftoppm)
            exports[suffix] = {role: pixel_metrics(image, role) for role in POSITIONS}
            log.write(f"{mode:8} {suffix}: section red={exports[suffix]['section']['red_pixels']}, "
                      f"detail red={exports[suffix]['detail']['red_pixels']}\n")
            log.flush()
        observations[mode] = exports
        write_json(output / "observations.json", observations)
        if mode == "PatHatch":
            doc.saveAs(str(output / "reproducer.FCStd"))
    DetailHatchExportTests.observations = observations
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DetailHatchExportTests)
    return unittest.TextTestRunner(stream=log, verbosity=2).run(suite)


def run_gui_tests(output_dir, pdftoppm, case="baseline"):
    """Entry point called by the launcher's macro, after the GUI event loop starts."""
    import FreeCAD as App
    from PySide6 import QtCore, QtWidgets

    output = Path(output_dir)
    doc = None
    started = time.monotonic()
    result = {"status": "ERROR", "exit_code": 2}
    try:
        import TechDraw
        import TechDrawGui

        runtime = {"freecad_version": App.Version(), "python": sys.version,
                   "qt": QtCore.qVersion(), "pid": os.getpid(),
                   "gui_up": bool(App.GuiUp), "resource_dir": App.getResourceDir(),
                   "TechDraw": fingerprint(TechDraw.__file__),
                   "TechDrawGui": fingerprint(TechDrawGui.__file__)}
        write_json(output / "runtime.json", runtime)
        fixture = make_fixture(output)
        doc, _page, section, detail = fixture
        write_json(output / "geometry.json", {
            view.Name: {"state": list(view.State), "visible_edges": len(view.getVisibleEdges()),
                        "scale": view.Scale, "direction": list(view.Direction),
                        "position_mm": [view.X.Value, view.Y.Value],
                        "properties": view.PropertiesList}
            for view in (section, detail)
        })
        with (output / "tests.log").open("w", encoding="utf-8") as log:
            if case == "baseline":
                tests = run_baseline(fixture, output, pdftoppm, log)
            else:
                _, extra = scenario_registry()
                suite = extra["make_suite"](case, globals(), fixture, output, pdftoppm)
                tests = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        code = 2 if tests.errors else (0 if tests.wasSuccessful() else 1)
        result = {"status": "PASS" if code == 0 else "FAIL" if code == 1 else "ERROR",
                  "exit_code": code, "tests": tests.testsRun,
                  "failures": len(tests.failures), "errors": len(tests.errors),
                  "failed_checks": [test.id() for test, _ in tests.failures],
                  "skipped": len(tests.skipped)}
    except Exception:  # noqa: BLE001 - Publish any GUI callback failure before shutting down.
        result["exception"] = traceback.format_exc()
    finally:
        if doc is not None:
            try:
                App.closeDocument(doc.Name)
            except Exception:  # noqa: BLE001 - Do not mask the original failure during cleanup.
                result.update(status="ERROR", exit_code=2, cleanup_exception=traceback.format_exc())
        result["case"] = case
        result["elapsed_seconds"] = time.monotonic() - started
        write_json(output / "result.json", result)
        QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)


if __name__ == "__main__":
    raise SystemExit(launch())
