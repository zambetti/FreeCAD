# SPDX-License-Identifier: LGPL-2.1-or-later

"""Additional scenarios exclusively for DrawViewDetailHatchGuiTest.py.

Do not run this file directly. The launcher supplies a fresh fixture/process per
scenario. Listing cases imports only the standard library, not FreeCAD or Qt.
These scenarios are staged for validation after the implementation build is ready;
their existence/count is not a claim of passing GUI tests.

Assertions use tolerant image measurements rather than platform-specific golden
PNGs. Each negative assertion has a positive material/fill control. Style edits
are checked in the live scene before exporters can refresh it. Pattern scale is
measured by scanline crossings, a 90-degree rotation by diagonal correlation,
offset by changed ink pixels, and PAT weight by increased coverage. This is not
an exact phase-registration test between differently bounded faces.

Qt scene access uses the page's QGraphicsView objectName (set by QGVPage) and the
TechDraw Rez/Resolution preference (default 10 scene units/mm). The selection
scenario is deliberately opt-in through --case selection (also in --case all).
It sends QTest mouse events through the viewport, observes actual selection and
preselection, and rejects Face-1. It does not insert a desired selection name.
An unavailable visible page/Qt wrapper is an ERROR, never a silent skip/pass.

Scope limits: one flat section, one explicit face (TechDraw's zero-based Face0),
one through ellipse, and one moving round hole. Deferred updates cover geometry;
style deferral, nested details, perspective and cross-version image tolerances
are not established here. Export/click execution awaits explicit run permission.
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
        from PySide6 import QtCore, QtTest, QtWidgets

        if self.view is None:
            self.page.ViewObject.show()
            self.pump(150)
            views = [v for v in Gui.getMainWindow().findChildren(QtWidgets.QGraphicsView)
                     if v.objectName() == self.page.Name and v.isVisible()]
            if len(views) != 1:
                raise RuntimeError(f"Need one visible QGVPage named {self.page.Name}, found {len(views)}")
            self.view = views[0]
            self.rez = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Rez").GetFloat(
                "Resolution", 10.0)
            w, h = self.common["PAGE_MM"]
            self.scene_rect = QtCore.QRectF(0, -h * self.rez, w * self.rez, h * self.rez)
            self.view.fitInView(self.scene_rect, QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            # Keep hover highlighting away from measured faces in live-scene captures.
            blank = self.view.mapFromScene(QtCore.QPointF(5 * self.rez, -115 * self.rez))
            QtTest.QTest.mouseMove(self.view.viewport(), blank)
            self.pump(100)
        return self.view

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
                "detail_anchor": list(self.detail.AnchorPoint),
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
        result = {"pixels": len(pixels), "red_fraction": sum(colored(p, "red") for p in pixels) / len(pixels)}
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
        from PySide6 import QtCore, QtTest

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

        observer = Observer()
        Gui.Selection.addObserver(observer)
        try:
            Gui.Selection.clearSelection()
            x, y = c.common["POSITIONS"]["detail"]
            pos = view.mapFromScene(QtCore.QPointF((x + 2) * c.rez, -(y + 3) * c.rez))
            if not view.viewport().rect().contains(pos):
                raise RuntimeError("Selection point is outside the actual viewport")
            view.setFocus()
            QtTest.QTest.mouseMove(view.viewport(), pos, 50)
            c.pump(150)
            QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                                   QtCore.Qt.KeyboardModifier.NoModifier, pos, 50)
            c.pump(200)
            selected = [{"object": item.ObjectName, "subelements": list(item.SubElementNames)}
                        for item in Gui.Selection.getSelectionEx()]
        finally:
            Gui.Selection.removeObserver(observer)
        c.common["write_json"](c.output / "selection.json", {"preselection": events, "selected": selected})
        with self.subTest(check="real-face-selected"):
            self.assertTrue(any(item["object"] == c.detail.Name and "Face0" in item["subelements"]
                                for item in selected), selected)
        with self.subTest(check="no-synthetic-selection"):
            self.assertFalse(any(sub.startswith("Face-") for item in selected for sub in item["subelements"]), selected)
        with self.subTest(check="real-face-hover"):
            self.assertTrue(any(e["object"] == c.detail.Name and e["subelement"] == "Face0" for e in events), events)
            self.assertFalse(any(e["subelement"].startswith("Face-") for e in events), events)


SCENARIOS = {
    "style-pat": (PatStyleTests, "Live/export scale, rotation, offset, color, weight; no recompute"),
    "style-svg": (SvgStyleTests, "Live/export scale, rotation, offset, color; no recompute"),
    "style-color": (ColorStyleTests, "Live/export color, transparency, hide/show; no recompute"),
    "explicit-svg": (ExplicitSvgTests, "SVG overrides inherited PAT; removal restores inheritance"),
    "explicit-pat": (ExplicitPatTests, "PAT > explicit SVG > inherited SVG; removal fallbacks"),
    "holes-clipping": (HolesClippingTests, "Thin ellipse void and circular clipping in SVG/PDF pixels"),
    "updates": (GeometryUpdateTests, "Parent solid, detail anchor/scale and parent scale updates"),
    "deferred": (DeferredUpdateTests, "KeepUpdated=False freezes live geometry; resume updates"),
    "selection": (SelectionTests, "Real Qt hover/click selects Face0, never inherited Face-1"),
}


def make_suite(name, common, fixture, output, pdftoppm):
    test_class, _ = SCENARIOS[name]
    context = ScenarioContext(common, fixture, output, pdftoppm)
    suite = unittest.TestSuite()
    for method in unittest.defaultTestLoader.getTestCaseNames(test_class):
        test = test_class(method)
        test.context = context
        suite.addTest(test)
    return suite
