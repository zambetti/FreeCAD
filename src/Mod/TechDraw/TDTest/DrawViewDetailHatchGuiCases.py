# SPDX-License-Identifier: LGPL-2.1-or-later

"""Additional scenarios exclusively for DrawViewDetailHatchGuiTest.py.

Do not run this file directly. The launcher supplies a fresh fixture/process per
scenario. Listing cases imports only the standard library, not FreeCAD or Qt.
Discovery counts do not establish passing GUI tests; result.json, methods.json,
and subtests.json record the actual execution outcomes.

Assertions use tolerant image measurements rather than platform-specific golden
PNGs. Each negative assertion has a positive material/fill control. Style edits
are checked in the live scene before exporters can refresh it. Pattern scale is
measured by scanline crossings, a 90-degree rotation by diagonal correlation,
offset by changed ink pixels, and PAT weight by increased coverage. This is not
an exact phase-registration test between differently bounded faces.

Qt scene access uses ViewProviderPage's <PageName>View objectName and the TechDraw
Rez/Resolution preference (default 10 scene units/mm). The selection scenario is
opt-in through --case selection (also in --case all). It sends QTest QWindow
mouse events through native widget hit testing, observes actual scene hover focus
and selection, and rejects Face-1. It never inserts a desired selection name.
An unavailable visible page/Qt wrapper is an ERROR, never a silent skip/pass.

GUI fixture constraints: disable active dock overlays and hide dock panels that
otherwise intercept mouse events through transparent regions; retain custom
QGraphicsItem wrappers until document
closure (PySide can mark their C++-created parents Python-owned). Right-press
selection disables detail ShowMatting to expose the actual decorative face,
avoids the section's detail-reference marker, and filters only ContextMenu events
to avoid modal menu hangs. It does not test context-menu actions. TechDraw hover
sets QGraphicsScene focus rather than emitting Gui.Selection preselection.

The transforms scenario uses a noncentral round hole, solid inherited fill and
square cropping with ShowMatting=False. It checks an unrotated control, parent
rotation, then additional detail rotation in live/SVG/PDF images. Expected hole
centers and crop probes come from elementary 2D rotations, not inherited-face
implementation helpers. The solid fill avoids the shared SVG-pattern exporter
rotation/offset defect; style-svg deliberately keeps that defect failing.

Scope limits: one flat section, one explicit face (TechDraw's zero-based Face0),
one through ellipse, and one moving round hole. Deferred updates cover geometry;
style deferral, nested details, perspective and cross-version image tolerances
are not established here.
"""

import hashlib
import math
import unittest
from pathlib import Path


RED = (0.85, 0.10, 0.10)
BLUE = (0.10, 0.10, 0.85)
GREEN = (0.10, 0.65, 0.10)
CHANNEL = {"red": 0, "green": 1, "blue": 2}


def colored(rgb, name):
    channel = CHANNEL[name]
    others = [rgb[i] for i in range(3) if i != channel]
    return (rgb[channel] > 130 and rgb[channel] - max(others) > 45
            and abs(others[0] - others[1]) < 35)


def ink_mask(image, name):
    return [colored(image.pixelColor(x, y).getRgb()[:3], name)
            for y in range(image.height()) for x in range(image.width())]


def measure(image):
    """Small, interior image patches only; no FreeCAD objects or implementation helpers."""
    width, height = image.width(), image.height()
    if width <= 0 or height <= 0:
        raise ValueError("Measurement patch is empty")
    rgb = [image.pixelColor(x, y).getRgb()[:3]
           for y in range(height) for x in range(width)]
    result = {"pixels": len(rgb),
              "mean_rgb": [sum(p[i] for p in rgb) / len(rgb) for i in range(3)]}
    for name in CHANNEL:
        mask = [colored(p, name) for p in rgb]
        crossings = sum(mask[y * width + x] and not mask[y * width + x - 1]
                        for y in range(height) for x in range(1, width)) / height
        step = 3
        plus = sum(mask[y * width + x] and mask[(y + step) * width + x + step]
                   for y in range(height - step) for x in range(width - step))
        minus = sum(mask[y * width + x + step] and mask[(y + step) * width + x]
                    for y in range(height - step) for x in range(width - step))
        result[name] = {"pixels": sum(mask), "fraction": sum(mask) / len(mask),
                        "crossings": crossings, "slope": (plus - minus) / max(1, plus + minus),
                        "mask_sha256": hashlib.sha256(bytes(mask)).hexdigest()}
    return result


def changed_ink(before, after, color="red"):
    a, b = ink_mask(before, color), ink_mask(after, color)
    if before.size() != after.size():
        raise ValueError("Cannot compare differently sized patches")
    return sum(x != y for x, y in zip(a, b)) / len(a)


class ScenarioContext:
    def __init__(self, common, fixture, output, pdftoppm):
        self.common = common
        self.doc, self.page, self.section, self.detail = fixture
        self.output, self.pdftoppm = output, pdftoppm
        self.images = {}
        self.observations = {}
        self.pump = common["pump_events"]
        self.view = None
        # Keep wrappers for FreeCAD-owned custom QGraphicsItems until document teardown.
        self.qt_items = []

    def recompute(self):
        self.doc.recompute()
        self.common["wait_for_views"]([self.doc.Base, self.section, self.detail])

    def geometry(self):
        return {v.Name: [round(edge.Length, 7) for edge in v.getVisibleEdges()]
                for v in (self.section, self.detail)}

    def prepare_style(self, mode):
        self.section.CutSurfaceDisplay = mode
        self.section.ViewObject.CutSurfaceColor = RED if mode == "Color" else (1.0, 1.0, 1.0)
        self.detail.Scale = 1
        self.recompute()

    def page_view(self):
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide6 import QtCore, QtWidgets

        if self.view is None:
            Gui.activateWorkbench("TechDrawWorkbench")
            self.page.ViewObject.show()
            main = Gui.getMainWindow()
            # OverlayManager can reshow hidden docks on its timer. ToggleAll disables
            # overlays iff any overlay tab contains a dock (OverlayManager::setOverlayMode).
            overlays = [w for w in main.findChildren(QtWidgets.QTabWidget)
                        if w.inherits("Gui::OverlayTabWidget") and w.count()]
            disabled_overlays = [w.objectName() for w in overlays]
            if overlays:
                Gui.runCommand("Std_DockOverlayAll")
                self.pump(250)
            # Transparent task/dock panels can still intercept native mouse events over
            # the visible page. Hide them only in this disposable GUI fixture.
            docks = main.findChildren(QtWidgets.QDockWidget)
            hidden_docks = [w.objectName() for w in docks if w.isVisible()]
            for dock in docks:
                dock.hide()
            self.pump(150)
            typed_views = main.findChildren(QtWidgets.QGraphicsView)
            qt_views = [w for w in QtWidgets.QApplication.allWidgets() if w.inherits("QGraphicsView")]

            def describe(widget):
                return {"name": widget.objectName(), "class": widget.metaObject().className(),
                        "python_type": str(type(widget)), "visible": widget.isVisible()}
            self.common["write_json"](self.output / "page-widgets.json", {
                "main_window": describe(main), "page_visibility": self.page.ViewObject.Visibility,
                "disabled_overlays": disabled_overlays, "hidden_docks": hidden_docks,
                "typed_views": [describe(w) for w in typed_views],
                "qt_views": [describe(w) for w in qt_views],
                "top_level_widgets": [describe(w) for w in QtWidgets.QApplication.topLevelWidgets()],
            })
            view_name = self.page.Name + "View"  # ViewProviderPage::createMDIViewPage
            views = [v for v in typed_views if v.objectName() == view_name and v.isVisible()]
            if len(views) != 1:
                raise RuntimeError(f"Need one visible QGVPage named {view_name}, found {len(views)}")
            self.view = views[0]
            self.rez = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Rez").GetFloat(
                "Resolution", 10.0)
            w, h = self.common["PAGE_MM"]
            self.scene_rect = QtCore.QRectF(0, -h * self.rez, w * self.rez, h * self.rez)
            self.view.fitInView(self.scene_rect, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            # Keep hover highlighting away from measured faces in live-scene captures.
            blank = self.view.mapFromScene(QtCore.QPointF(5 * self.rez, -115 * self.rez))
            self.move_mouse(blank)
            self.pump(100)
        return self.view

    def mouse_target(self, viewport_pos):
        window = self.view.window().windowHandle()
        if window is None:
            raise RuntimeError("Page has no native window for QTest mouse events")
        return window, window.mapFromGlobal(self.view.viewport().mapToGlobal(viewport_pos))

    def move_mouse(self, viewport_pos):
        from PySide6 import QtTest

        # QWidget's QTest overload only warps QCursor for an unpressed move; on macOS
        # that need not deliver a hover event. The QWindow overload sends a QPA event.
        window, pos = self.mouse_target(viewport_pos)
        QtTest.QTest.mouseMove(window, pos, 50)

    def live_image(self):
        from PySide6 import QtCore, QtGui

        view = self.page_view()
        w, h = self.common["PAGE_MM"]
        ppm = self.common["PIXELS_PER_MM"]
        image = QtGui.QImage(w * ppm, h * ppm, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtCore.Qt.GlobalColor.white)
        painter = QtGui.QPainter(image)
        try:
            # No requestPaint/refreshViews/recompute: inspect the current scene as-is.
            view.scene().render(painter, QtCore.QRectF(0, 0, w * ppm, h * ppm), self.scene_rect)
        finally:
            painter.end()
        return image

    def patch(self, image, role, bounds=(-5, -5, 5, 5)):
        x, y = self.common["POSITIONS"][role]
        x0, y0, x1, y1 = bounds
        ppm = self.common["PIXELS_PER_MM"]
        left, top = round((x + x0) * ppm), round((self.common["PAGE_MM"][1] - y - y1) * ppm)
        width, height = round((x1 - x0) * ppm), round((y1 - y0) * ppm)
        if min(left, top) < 0 or left + width > image.width() or top + height > image.height():
            raise ValueError(f"Measurement outside exported page: {role}, {bounds}")
        return image.copy(left, top, width, height)

    def snapshot(self, name, *, live=False, exports=True):
        import FreeCAD as App
        import FreeCADGui as Gui
        import TechDrawGui

        Gui.Selection.clearSelection()
        self.pump(100)
        images = {}
        if live:
            images["gui"] = self.live_image()
            if not images["gui"].save(str(self.output / f"{name}.gui.png")):
                raise RuntimeError("Cannot save live scene capture")
        if exports:
            for fmt, exporter in (("svg", TechDrawGui.exportPageAsSvg),
                                  ("pdf", TechDrawGui.exportPageAsPdf)):
                path = self.output / f"{name}.{fmt}"
                exporter(self.page, str(path))
                images[fmt] = self.common["render_export"](path, self.pdftoppm)
        self.images[name] = images
        self.observations[name] = {
            "geometry": self.geometry(), "samples": {},
            "parameters": {
                "KeepUpdated": self.page.KeepUpdated,
                "CutSurfaceDisplay": self.section.CutSurfaceDisplay,
                "section_scale": self.section.Scale, "detail_scale": self.detail.Scale,
                "section_rotation": self.section.Rotation.Value,
                "detail_rotation": self.detail.Rotation.Value,
                "detail_anchor": list(self.detail.AnchorPoint),
                "detail_radius": self.detail.Radius,
                "ShowMatting": self.detail.ShowMatting,
                "MattingStyle": App.ParamGet(
                    "User parameter:BaseApp/Preferences/Mod/TechDraw/Decorations"
                ).GetInt("MattingStyle", 0),
                "HatchScale": self.section.HatchScale,
                "HatchRotation": str(self.section.HatchRotation),
                "HatchOffset": list(self.section.HatchOffset),
                "CutSurfaceTransparency": self.section.ViewObject.CutSurfaceTransparency,
                "CutSurfaceColor": str(self.section.ViewObject.CutSurfaceColor),
                "HatchColor": str(self.section.ViewObject.HatchColor),
                "GeomHatchColor": str(self.section.ViewObject.GeomHatchColor),
                "WeightPattern": str(self.section.ViewObject.WeightPattern),
            },
        }
        for fmt in images:
            for role in ("section", "detail"):
                self.probe(name, fmt, role)
        self.save_observations()
        return images

    def probe(self, name, fmt, role, bounds=(-5, -5, 5, 5), label="interior"):
        metrics = measure(self.patch(self.images[name][fmt], role, bounds))
        self.observations[name]["samples"][f"{fmt}/{role}/{label}"] = {
            "bounds_view_mm": list(bounds), **metrics}
        self.save_observations()
        return metrics

    def ring(self, name, fmt, role, center, inner, outer):
        image = self.images[name][fmt]
        x, y = self.common["POSITIONS"][role]
        ppm = self.common["PIXELS_PER_MM"]
        cx = (x + center[0]) * ppm
        cy = (self.common["PAGE_MM"][1] - y - center[1]) * ppm
        pixels = []
        for py in range(math.floor(cy - outer * ppm), math.ceil(cy + outer * ppm)):
            for px in range(math.floor(cx - outer * ppm), math.ceil(cx + outer * ppm)):
                radius = math.hypot(px + 0.5 - cx, py + 0.5 - cy) / ppm
                if inner < radius < outer:
                    if not (0 <= px < image.width() and 0 <= py < image.height()):
                        raise ValueError("Ring extends beyond exported page")
                    pixels.append(image.pixelColor(px, py).getRgb()[:3])
        if not pixels:
            raise ValueError("Empty ring probe")
        result = {"pixels": len(pixels),
                  "red_fraction": sum(colored(p, "red") for p in pixels) / len(pixels),
                  "dark_fraction": sum(max(p) < 120 for p in pixels) / len(pixels)}
        key = f"{fmt}/{role}/ring-{center}-{inner:g}-{outer:g}"
        self.observations[name]["samples"][key] = result
        self.save_observations()
        return result

    def save_observations(self):
        self.common["write_json"](self.output / "observations.json", self.observations)

    def set_shape(self, shape):
        if not shape.isValid() or len(shape.Solids) != 1:
            raise RuntimeError("Invalid regression fixture solid")
        source = self.doc.getObject("Specimen")
        new_source = source is None
        if new_source:
            source = self.doc.addObject("Part::Feature", "Specimen")
        source.Shape = shape
        if new_source:
            self.doc.Base.Source = [source]
            self.section.Source = [source]
        source.ViewObject.hide()
        return source


def stock_with_round_hole(x):
    import FreeCAD as App
    import Part

    stock = Part.makeBox(40, 20, 30, App.Vector(-20, -10, -15))
    tool = Part.makeCylinder(2.2, 40, App.Vector(x, -20, 0), App.Vector(0, 1, 0))
    return stock.cut(tool)


def stock_with_thin_ellipse():
    import FreeCAD as App
    import Part

    stock = Part.makeBox(40, 20, 30, App.Vector(-20, -10, -15))
    ellipse = Part.Ellipse(App.Vector(0, 0, -20), 6.0, 0.5).toShape()
    tool = Part.Face(Part.Wire([ellipse])).extrude(App.Vector(0, 0, 40))
    tool.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)
    return stock.cut(tool)


class ScenarioTest(unittest.TestCase):
    context: ScenarioContext

    def check_tint(self, name, color, minimum=0.005, roles=("section", "detail")):
        c = self.context
        for fmt in c.images[name]:
            for role in roles:
                with self.subTest(step=name, format=fmt, role=role, check="fill"):
                    metrics = c.probe(name, fmt, role)
                    self.assertGreater(metrics[color]["fraction"], minimum, metrics)
                    for other in CHANNEL:
                        if other != color:
                            self.assertEqual(metrics[other]["pixels"], 0, metrics)

    def check_absent(self, name, color):
        c = self.context
        for fmt in c.images[name]:
            for role in ("section", "detail"):
                with self.subTest(step=name, format=fmt, role=role, check="absent"):
                    self.assertEqual(c.probe(name, fmt, role)[color]["pixels"], 0)

    def check_hole(self, name, hole_x, *, anchor=0.0, scale=2.0):
        c = self.context
        center = ((hole_x - anchor) * scale, 0)
        half = 0.8 * scale
        bounds = (center[0] - half, -half, center[0] + half, half)
        radius = 2.2 * scale
        for fmt in c.images[name]:
            with self.subTest(step=name, format=fmt, check="hole-void"):
                self.assertEqual(c.probe(name, fmt, "detail", bounds, "hole")["red"]["pixels"], 0)
            with self.subTest(step=name, format=fmt, check="material-around-hole"):
                ring = c.ring(name, fmt, "detail", center, radius + 0.5, radius + 1.0)
                self.assertGreater(ring["red_fraction"], 0.75, ring)
            with self.subTest(step=name, format=fmt, check="circular-clip"):
                inside = c.ring(name, fmt, "detail", (0, 0), 10 * scale - 1.5, 10 * scale - 0.8)
                outside = c.ring(name, fmt, "detail", (0, 0), 10 * scale + 0.8, 10 * scale + 2)
                self.assertGreater(inside["red_fraction"], 0.75, inside)
                self.assertEqual(outside["red_fraction"], 0, outside)


class PatternStyleTests(ScenarioTest):
    mode = "PatHatch"

    def test_style_only_propagation(self):
        c = self.context
        c.prepare_style(self.mode)
        initial_geometry = c.geometry()
        c.snapshot("initial", live=True)
        self.check_tint("initial", "red")
        previous = "initial"
        changes = [("scale", c.section, "HatchScale", 2.0),
                   ("rotation", c.section, "HatchRotation", 90.0),
                   ("offset", c.section, "HatchOffset", (1.3, 0.2, 0.0)),
                   ("color", c.section.ViewObject,
                    "GeomHatchColor" if self.mode == "PatHatch" else "HatchColor", BLUE)]
        if self.mode == "PatHatch":
            changes.append(("weight", c.section.ViewObject, "WeightPattern", 0.8))
        for step, obj, prop, value in changes:
            setattr(obj, prop, value)
            # Intentionally no recompute/touch/requestPaint between property edit and GUI capture.
            c.pump(400)
            c.snapshot(step, live=True)
            color = "blue" if step in ("color", "weight") else "red"
            self.check_tint(step, color)
            with self.subTest(step=step, check="unchanged-projected-geometry"):
                self.assertEqual(c.geometry(), initial_geometry)
            if step == "color":
                previous = step
                continue
            for fmt in c.images[step]:
                for role in ("section", "detail"):
                    with self.subTest(step=step, format=fmt, role=role, check="style-effect"):
                        a = c.probe(previous, fmt, role)[color]
                        b = c.probe(step, fmt, role)[color]
                        if step == "scale":
                            self.assertGreater(a["crossings"], 1.5)
                            self.assertGreater(b["crossings"], a["crossings"] * 0.25)
                            self.assertLess(b["crossings"], a["crossings"] * 0.8)
                        elif step == "rotation":
                            self.assertLess(a["slope"] * b["slope"], -0.0001,
                                            "90-degree rotation did not reverse diagonal orientation")
                        elif step == "offset":
                            before = c.patch(c.images[previous][fmt], role)
                            after = c.patch(c.images[step][fmt], role)
                            self.assertGreater(changed_ink(before, after), 0.005,
                                               "Pattern offset left the exported/live ink unchanged")
                        elif step == "weight":
                            self.assertGreater(b["fraction"], a["fraction"] * 1.35)
                            self.assertLess(b["fraction"], a["fraction"] * 3.2)
            previous = step


class PatStyleTests(PatternStyleTests):
    mode = "PatHatch"


class SvgStyleTests(PatternStyleTests):
    mode = "SvgHatch"


class ColorStyleTests(ScenarioTest):
    def test_color_transparency_and_hide(self):
        c = self.context
        c.prepare_style("Color")
        c.snapshot("opaque-red", live=True)
        self.check_tint("opaque-red", "red", 0.8)
        c.section.ViewObject.CutSurfaceColor = BLUE
        c.pump(400)
        c.snapshot("opaque-blue", live=True)
        self.check_tint("opaque-blue", "blue", 0.8)
        c.section.ViewObject.CutSurfaceTransparency = 50
        c.pump(400)
        c.snapshot("half-alpha", live=True)
        self.check_tint("half-alpha", "blue", 0.8)
        for fmt in c.images["half-alpha"]:
            for role in ("section", "detail"):
                with self.subTest(format=fmt, role=role, check="half-alpha-contrast"):
                    a = c.probe("opaque-blue", fmt, role)["mean_rgb"]
                    b = c.probe("half-alpha", fmt, role)["mean_rgb"]
                    self.assertGreater(b[2] - b[0], 0.3 * (a[2] - a[0]))
                    self.assertLess(b[2] - b[0], 0.7 * (a[2] - a[0]))
        c.section.ViewObject.CutSurfaceTransparency = 100
        c.pump(400)
        c.snapshot("transparent", live=True)
        self.check_absent("transparent", "blue")
        c.section.ViewObject.CutSurfaceTransparency = 0
        c.pump(400)
        c.snapshot("opaque-restored", live=True)
        self.check_tint("opaque-restored", "blue", 0.8)
        c.section.CutSurfaceDisplay = "Hide"
        c.pump(400)
        c.snapshot("hidden", live=True)
        self.check_absent("hidden", "blue")
        c.section.CutSurfaceDisplay = "Color"
        c.pump(400)
        c.snapshot("shown-again", live=True)
        self.check_tint("shown-again", "blue", 0.8)


def explicit_svg(c, color):
    hatch = c.doc.addObject("TechDraw::DrawHatch", "ExplicitSvg")
    # TechDraw face indices are zero-based (unlike Part's Face1 convention).
    hatch.Source = (c.detail, ["Face0"])
    hatch.HatchPattern = str(Path(__file__).with_name("TestHatch.svg"))
    hatch.ViewObject.HatchColor = color
    hatch.ViewObject.HatchScale = 1
    return hatch


def explicit_pat(c, color):
    hatch = c.doc.addObject("TechDraw::DrawGeomHatch", "ExplicitPat")
    hatch.Source = (c.detail, ["Face0"])
    hatch.FilePattern = str(c.output / "diagonal.pat")
    hatch.NamePattern = "TD_DIAGONAL"
    hatch.ScalePattern = 1
    hatch.ViewObject.ColorPattern = color
    hatch.ViewObject.WeightPattern = 0.35
    return hatch


class ExplicitSvgTests(ScenarioTest):
    def test_svg_overrides_inheritance_then_removal_restores_it(self):
        c = self.context
        c.snapshot("inherited")
        self.check_tint("inherited", "red")
        hatch = explicit_svg(c, BLUE)
        c.recompute()
        c.snapshot("svg-override")
        self.check_tint("svg-override", "blue", roles=("detail",))
        self.check_tint("svg-override", "red", roles=("section",))
        c.doc.removeObject(hatch.Name)
        c.recompute()
        c.snapshot("svg-removed")
        self.check_tint("svg-removed", "red")


class ExplicitPatTests(ScenarioTest):
    def test_pat_wins_then_falls_back_to_svg_then_inheritance(self):
        c = self.context
        c.section.CutSurfaceDisplay = "SvgHatch"
        c.recompute()
        c.snapshot("inherited")
        self.check_tint("inherited", "red")
        svg = explicit_svg(c, GREEN)
        c.recompute()
        c.snapshot("svg-override")
        self.check_tint("svg-override", "green", roles=("detail",))
        self.check_tint("svg-override", "red", roles=("section",))
        pat = explicit_pat(c, BLUE)
        c.recompute()
        c.snapshot("pat-wins")
        self.check_tint("pat-wins", "blue", roles=("detail",))
        self.check_tint("pat-wins", "red", roles=("section",))
        c.doc.removeObject(pat.Name)
        c.recompute()
        c.snapshot("pat-removed")
        self.check_tint("pat-removed", "green", roles=("detail",))
        self.check_tint("pat-removed", "red", roles=("section",))
        c.doc.removeObject(svg.Name)
        c.recompute()
        c.snapshot("both-removed")
        self.check_tint("both-removed", "red")


class HolesClippingTests(ScenarioTest):
    def test_thin_ellipse_and_circular_clip_in_exported_images(self):
        c = self.context
        c.set_shape(stock_with_thin_ellipse())
        # Thick contour strokes could conceal an incorrect fill inside this thin void.
        c.section.ViewObject.LineWidth = 0.15
        c.detail.ViewObject.LineWidth = 0.15
        for mode in ("PatHatch", "SvgHatch", "Color"):
            c.section.CutSurfaceDisplay = mode
            c.section.ViewObject.CutSurfaceColor = RED if mode == "Color" else (1.0, 1.0, 1.0)
            c.recompute()
            c.snapshot(mode)
            for fmt in c.images[mode]:
                for role, scale in (("section", 1), ("detail", 2)):
                    material = tuple(v * scale for v in (-3, 2.5, 3, 5.5))
                    hole = tuple(v * scale for v in (-3, -0.2, 3, 0.2))
                    with self.subTest(mode=mode, format=fmt, role=role, check="material"):
                        metrics = c.probe(mode, fmt, role, material, "material")
                        self.assertGreater(metrics["red"]["fraction"], 0.8 if mode == "Color" else 0.005)
                    with self.subTest(mode=mode, format=fmt, role=role, check="thin-hole"):
                        self.assertEqual(c.probe(mode, fmt, role, hole, "thin-hole")["red"]["pixels"], 0)
                with self.subTest(mode=mode, format=fmt, check="detail-clip"):
                    inside = c.ring(mode, fmt, "detail", (0, 0), 14, 18)
                    outside = c.ring(mode, fmt, "detail", (0, 0), 21, 23)
                    self.assertGreater(inside["red_fraction"], 0.8 if mode == "Color" else 0.005)
                    self.assertEqual(outside["red_fraction"], 0)
        c.doc.saveAs(str(c.output / "holes-reproducer.FCStd"))


class GeometryUpdateTests(ScenarioTest):
    def test_parent_geometry_anchor_and_scales(self):
        c = self.context
        c.section.CutSurfaceDisplay = "Color"
        c.section.ViewObject.CutSurfaceColor = RED
        c.set_shape(stock_with_round_hole(-4))
        c.recompute()
        c.snapshot("hole-left")
        self.check_hole("hole-left", -4)
        c.set_shape(stock_with_round_hole(4))
        c.recompute()
        c.snapshot("hole-right")
        self.check_hole("hole-right", 4)
        for fmt in c.images["hole-right"]:
            with self.subTest(format=fmt, check="old-hole-refilled"):
                metrics = c.probe("hole-right", fmt, "detail", (-9, -1, -7, 1), "old-hole")
                self.assertGreater(metrics["red"]["fraction"], 0.8)
        c.detail.AnchorPoint = (4, 0, 0)
        c.recompute()
        c.snapshot("anchor-moved")
        self.check_hole("anchor-moved", 4, anchor=4)
        c.detail.Scale = 1.5
        c.recompute()
        c.snapshot("detail-scale")
        self.check_hole("detail-scale", 4, anchor=4, scale=1.5)
        c.section.Scale = 1.25
        c.recompute()
        c.snapshot("parent-scale")
        self.check_hole("parent-scale", 4, anchor=4, scale=1.5)
        c.doc.saveAs(str(c.output / "updates-reproducer.FCStd"))


class TransformTests(ScenarioTest):
    def test_parent_and_detail_rotation_with_unmatted_square_crop(self):
        import FreeCAD as App

        c = self.context
        decorations = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Decorations")
        decorations.SetInt("MattingStyle", 1)
        c.detail.ShowMatting = False
        c.detail.ShowHighlight = False
        c.section.CutSurfaceDisplay = "Color"
        c.section.ViewObject.CutSurfaceColor = RED
        c.section.ViewObject.LineWidth = 0.15
        c.detail.ViewObject.LineWidth = 0.15
        c.set_shape(stock_with_round_hole(4))

        def rotate(point, degrees):
            angle = math.radians(degrees)
            x, y = point
            return (x * math.cos(angle) - y * math.sin(angle),
                    x * math.sin(angle) + y * math.cos(angle))

        def bounds(center, half):
            x, y = center
            return (x - half, y - half, x + half, y + half)

        previous_centers = {}
        stages = (("unrotated", 0, 0), ("parent-90", 90, 0), ("parent-90-detail-45", 90, 45))
        for step, parent_angle, detail_angle in stages:
            c.section.Rotation = parent_angle
            if c.detail.Rotation.Value != detail_angle:
                # ReadOnly is editor-only (PropertyContainerPy::setCustomAttributes).
                # This editor-read-only Prop_Output gets an explicit geometry request;
                # this step tests its transform, not automatic property invalidation.
                c.detail.Rotation = detail_angle
                c.detail.touch()
            c.recompute()
            c.snapshot(step, live=True)
            parent_center = rotate((4, 0), parent_angle)
            centers = {"section": parent_center,
                       "detail": tuple(2 * v for v in rotate(parent_center, detail_angle))}
            c.observations[step]["expected"] = {
                "hole_centers_view_mm": centers,
                "square_crop_rotation_degrees": detail_angle,
            }
            c.save_observations()
            with self.subTest(step=step, check="transform-fixture"):
                self.assertAlmostEqual(c.section.Rotation.Value, parent_angle)
                self.assertAlmostEqual(c.detail.Rotation.Value, detail_angle)
                self.assertEqual((c.section.Scale, c.detail.Scale), (1, 2))
                self.assertEqual(list(c.detail.AnchorPoint), [0, 0, 0])
                self.assertEqual(c.detail.Radius, 10)
                self.assertFalse(c.detail.ShowMatting)
                self.assertEqual(decorations.GetInt("MattingStyle"), 1)
            for fmt in c.images[step]:
                for role, scale in (("section", 1), ("detail", 2)):
                    center = centers[role]
                    radius = 2.2 * scale
                    with self.subTest(step=step, format=fmt, role=role, check="rotated-hole-void"):
                        metrics = c.probe(step, fmt, role, bounds(center, 0.7 * scale), "hole")
                        self.assertEqual(metrics["red"]["pixels"], 0, metrics)
                    with self.subTest(step=step, format=fmt, role=role, check="hole-material"):
                        ring = c.ring(step, fmt, role, center, radius + 0.5, radius + 1)
                        self.assertGreater(ring["red_fraction"], 0.8, ring)
                    with self.subTest(step=step, format=fmt, role=role, check="hole-contour"):
                        ring = c.ring(step, fmt, role, center, radius - 0.35, radius + 0.35)
                        self.assertGreater(ring["dark_fraction"], 0.05, ring)
                    if role in previous_centers and previous_centers[role] != center:
                        with self.subTest(step=step, format=fmt, role=role, check="old-hole-refilled"):
                            metrics = c.probe(step, fmt, role,
                                             bounds(previous_centers[role], 0.35 * scale),
                                             "old-hole")
                            self.assertGreater(metrics["red"]["fraction"], 0.8, metrics)
                # The crop is square in the parent's output coordinates; only the
                # additional detail rotation rotates it. These corners lie outside
                # a circular crop and, at 45 degrees, outside an unrotated square.
                corners = [rotate((x, y), detail_angle)
                           for x, y in ((17, 17), (-17, 17), (-17, -17), (17, -17))]
                outside = [rotate(point, detail_angle)
                           for point in ((23, 0), (0, 23), (-23, 0), (0, -23))]
                for label, points, filled in (("square-corners", corners, True),
                                              ("outside-square", outside, False)):
                    metrics = [c.probe(step, fmt, "detail", bounds(point, 1.2), f"{label}-{i}")
                               for i, point in enumerate(points)]
                    with self.subTest(step=step, format=fmt, check=label):
                        for sample in metrics:
                            if filled:
                                self.assertGreater(sample["red"]["fraction"], 0.8, sample)
                            else:
                                self.assertEqual(sample["red"]["pixels"], 0, sample)
            previous_centers = centers
        c.doc.saveAs(str(c.output / "transforms-reproducer.FCStd"))


class DeferredUpdateTests(ScenarioTest):
    def test_keep_updated_defers_geometry_until_resumed(self):
        c = self.context
        c.section.CutSurfaceDisplay = "Color"
        c.section.ViewObject.CutSurfaceColor = RED
        c.set_shape(stock_with_round_hole(-4))
        c.recompute()
        c.snapshot("before", live=True, exports=False)
        self.check_hole("before", -4)
        c.page.KeepUpdated = False
        try:
            c.set_shape(stock_with_round_hole(4))
            c.doc.recompute()
            c.pump(500)
            # Export would refresh the scene and invalidate this deferred-update observation.
            c.snapshot("deferred", live=True, exports=False)
            with self.subTest(check="frozen-detail-ink"):
                before = c.patch(c.images["before"]["gui"], "detail", (-22, -22, 22, 22))
                after = c.patch(c.images["deferred"]["gui"], "detail", (-22, -22, 22, 22))
                self.assertEqual(changed_ink(before, after), 0)
        finally:
            c.page.KeepUpdated = True
        c.recompute()
        c.snapshot("resumed", live=True)
        self.check_hole("resumed", 4)
        for fmt in c.images["resumed"]:
            with self.subTest(format=fmt, check="resumed-old-hole-refilled"):
                metrics = c.probe("resumed", fmt, "detail", (-9, -1, -7, 1), "old-hole")
                self.assertGreater(metrics["red"]["fraction"], 0.8)


class SelectionTests(ScenarioTest):
    def test_real_face_click_never_selects_synthetic_face_minus_one(self):
        import FreeCADGui as Gui
        from PySide6 import QtCore, QtTest, QtWidgets

        c = self.context
        c.section.CutSurfaceDisplay = "Color"
        c.section.ViewObject.CutSurfaceColor = RED
        c.recompute()
        c.snapshot("before-selection")
        self.check_tint("before-selection", "red", 0.8)
        view = c.page_view()
        events = []

        class Observer:
            def setPreselection(self, doc, obj, sub):
                events.append({"document": doc, "object": obj, "subelement": sub})

        moves = []

        class MouseObserver(QtCore.QObject):
            def eventFilter(self, watched, event):
                if event.type() == QtCore.QEvent.Type.MouseMove:
                    moves.append([event.position().x(), event.position().y()])
                return False

        observer = Observer()
        mouse_observer = MouseObserver()
        view.viewport().installEventFilter(mouse_observer)
        Gui.Selection.addObserver(observer)
        try:
            main = Gui.getMainWindow()
            main.raise_()
            main.activateWindow()
            c.pump(250)
            Gui.Selection.clearSelection()
            x, y = c.common["POSITIONS"]["detail"]
            pos = view.mapFromScene(QtCore.QPointF((x + 2) * c.rez, -(y + 3) * c.rez))
            if not view.viewport().rect().contains(pos):
                raise RuntimeError("Selection point is outside the actual viewport")
            view.setFocus()
            blank = view.mapFromScene(QtCore.QPointF(5 * c.rez, -115 * c.rez))
            c.move_mouse(blank)
            c.move_mouse(pos)
            c.pump(150)
            widget_at = QtWidgets.QApplication.widgetAt(view.viewport().mapToGlobal(pos))
            hover_state = {
                "viewport_moves": moves, "view_has_focus": view.hasFocus(),
                "scene_has_focus": view.scene().hasFocus(), "window_active": main.isActiveWindow(),
                "viewport_mouse_tracking": view.viewport().hasMouseTracking(),
                "widget_at_point": None if widget_at is None else widget_at.metaObject().className(),
                "widget_at_is_viewport": widget_at == view.viewport(),
                "viewport_point": [pos.x(), pos.y()],
            }
            main.grab().save(str(c.output / "selection-window.png"))
            c.common["write_json"](c.output / "hover-state.json", hover_state)
            if not hover_state["widget_at_is_viewport"]:
                raise RuntimeError(f"Page mouse target is obstructed: {hover_state}")
            # QGIPrimPath::hoverEnterEvent sets scene focus, not Gui.Selection preselection.
            hovered = view.scene().focusItem()
            c.qt_items.append(hovered)
            hover_record = None if hovered is None else {
                "type": hovered.type(), "z": hovered.zValue(),
                "selectable": bool(hovered.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable),
                "accepts_hover": hovered.acceptHoverEvents(),
            }
            window, window_pos = c.mouse_target(pos)
            QtTest.QTest.mouseClick(window, QtCore.Qt.MouseButton.LeftButton,
                                   QtCore.Qt.KeyboardModifier.NoModifier, window_pos, 50)
            c.pump(200)
            selected = [{"object": item.ObjectName, "subelements": list(item.SubElementNames)}
                        for item in Gui.Selection.getSelectionEx()]
            selected_items = view.scene().selectedItems()
            c.qt_items.extend(selected_items)
            hovered_was_selected = hovered is not None and hovered in selected_items
        finally:
            Gui.Selection.removeObserver(observer)
            view.viewport().removeEventFilter(mouse_observer)
        c.common["write_json"](c.output / "selection.json", {
            "preselection": events, "selected": selected, "hover_focus": hover_record,
            "hover_state": hover_state,
            "hovered_was_selected": hovered_was_selected,
        })
        with self.subTest(check="real-face-selected"):
            self.assertTrue(any(item["object"] == c.detail.Name and "Face0" in item["subelements"]
                                for item in selected), selected)
        with self.subTest(check="no-synthetic-selection"):
            self.assertFalse(any(sub.startswith("Face-") for item in selected for sub in item["subelements"]), selected)
        with self.subTest(check="real-face-hover"):
            self.assertIsNotNone(hover_record)
            self.assertTrue(hover_record["selectable"] and hover_record["accepts_hover"], hover_record)
            self.assertTrue(hovered_was_selected, "Hover must focus the real face selected by the subsequent click")
            self.assertFalse(any(e["subelement"].startswith("Face-") for e in events), events)

    def test_right_press_skips_decorative_section_faces(self):
        import FreeCADGui as Gui
        import shiboken6
        from PySide6 import QtCore, QtTest, QtWidgets

        c = self.context
        c.section.CutSurfaceDisplay = "Color"
        c.section.ViewObject.CutSurfaceColor = RED
        # QGIMatting's unfilled circular border still hits its interior at a higher Z.
        # Disable it via the public property so this test actually hits the inherited face.
        c.detail.ShowMatting = False
        c.recompute()
        c.snapshot("before-right-selection")
        self.check_tint("before-right-selection", "red", 0.8)
        view = c.page_view()
        records = {}

        class SuppressMenu(QtCore.QObject):
            def eventFilter(self, watched, event):
                # Exercise QGVPage::mousePressEvent, not the subsequent modal QMenu::exec.
                if event.type() == QtCore.QEvent.Type.ContextMenu:
                    return True
                return False

        app = QtWidgets.QApplication.instance()
        menu_filter = SuppressMenu()
        app.installEventFilter(menu_filter)
        try:
            for role, target in (("section", c.section), ("detail", c.detail)):
                Gui.Selection.clearSelection()
                c.move_mouse(view.mapFromScene(QtCore.QPointF(5 * c.rez, -115 * c.rez)))
                c.pump(100)
                x, y = c.common["POSITIONS"][role]
                # Avoid the source view's detail-reference highlight above its section face.
                dx = 14 if role == "section" else 2
                pos = view.mapFromScene(QtCore.QPointF((x + dx) * c.rez, -(y + 3) * c.rez))
                if not view.viewport().rect().contains(pos):
                    raise RuntimeError("Right-click point is outside the actual viewport")
                if QtWidgets.QApplication.widgetAt(view.viewport().mapToGlobal(pos)) != view.viewport():
                    raise RuntimeError(f"Right-click target for {role} is obstructed by another widget")
                hit = view.itemAt(pos)
                chain = []
                item = hit
                while item is not None:
                    c.qt_items.append(item)
                    chain.append({"type": item.type(), "z": item.zValue(),
                                  "flags": str(item.flags()), "buttons": str(item.acceptedMouseButtons()),
                                  "python_owns_wrapper": shiboken6.ownedByPython(item)})
                    item = item.parentItem()
                records[role] = {"hit_chain": chain, "phase": "before-press"}
                c.common["write_json"](c.output / "right-selection.json", records)
                with self.subTest(role=role, check="decorative-hit"):
                    self.assertIsNotNone(hit)
                    self.assertFalse(hit.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
                    self.assertEqual(hit.acceptedMouseButtons(), QtCore.Qt.MouseButton.NoButton)
                window, window_pos = c.mouse_target(pos)
                try:
                    # MDIViewPage::selectOnRightPress runs on press, before any context menu.
                    QtTest.QTest.mousePress(window, QtCore.Qt.MouseButton.RightButton,
                                           QtCore.Qt.KeyboardModifier.NoModifier, window_pos, 50)
                    c.pump(100)
                    selected = [{"object": item.ObjectName, "subelements": list(item.SubElementNames)}
                                for item in Gui.Selection.getSelectionEx()]
                    records[role].update(phase="after-press", selected_on_press=selected)
                    c.common["write_json"](c.output / "right-selection.json", records)
                finally:
                    QtTest.QTest.mouseRelease(window, QtCore.Qt.MouseButton.RightButton,
                                             QtCore.Qt.KeyboardModifier.NoModifier, window_pos, 50)
                    c.pump(150)
                records[role]["phase"] = "after-release"
                c.common["write_json"](c.output / "right-selection.json", records)
                with self.subTest(role=role, check="right-selects-owner"):
                    self.assertTrue(any(item["object"] == target.Name
                                        and not any(item["subelements"]) for item in selected), selected)
                with self.subTest(role=role, check="right-no-synthetic-face"):
                    self.assertFalse(any(sub.startswith("Face-") for item in selected
                                         for sub in item["subelements"]), selected)
        finally:
            app.removeEventFilter(menu_filter)


SCENARIOS = {
    "style-pat": (PatStyleTests, "Live/export scale, rotation, offset, color, weight; no recompute"),
    "style-svg": (SvgStyleTests, "Live/export scale, rotation, offset, color; no recompute"),
    "style-color": (ColorStyleTests, "Live/export color, transparency, hide/show; no recompute"),
    "explicit-svg": (ExplicitSvgTests, "SVG overrides inherited PAT; removal restores inheritance"),
    "explicit-pat": (ExplicitPatTests, "PAT > explicit SVG > inherited SVG; removal fallbacks"),
    "holes-clipping": (HolesClippingTests, "Thin ellipse void and circular clipping in SVG/PDF pixels"),
    "updates": (GeometryUpdateTests, "Parent solid, detail anchor/scale and parent scale updates"),
    "transforms": (TransformTests, "Parent/detail rotation, noncentral hole, square crop without matting"),
    "deferred": (DeferredUpdateTests, "KeepUpdated=False freezes live geometry; resume updates"),
    "selection": (SelectionTests, "Real Qt left/right events never select decorative Face-1"),
}


def make_suite(name, common, fixture, output, pdftoppm):
    test_class, _ = SCENARIOS[name]
    context = ScenarioContext(common, fixture, output, pdftoppm)
    suite = unittest.TestSuite()
    # TestSuite discards completed tests; keep Qt wrappers alive until the caller
    # closes the document, not just until the last selection method returns.
    suite.fixture_context = context
    for method in unittest.defaultTestLoader.getTestCaseNames(test_class):
        test = test_class(method)
        test.context = context
        suite.addTest(test)
    return suite
