// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <initializer_list>
#include <numbers>
#include <sstream>
#include <string>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepClass_FaceClassifier.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_CurveType.hxx>
#include <Precision.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopAbs_State.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Ax1.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Elips.hxx>
#include <gp_Pnt.hxx>
#include <gp_Vec.hxx>

#include <Mod/TechDraw/App/DetailHatchGeometry.h>
#include <Mod/TechDraw/App/DrawGeomHatch.h>
#include <Mod/TechDraw/App/HatchLine.h>

#include "src/App/InitApplication.h"

// NOLINTBEGIN(readability-magic-numbers,cppcoreguidelines-avoid-magic-numbers)

namespace
{

using TechDraw::DetailHatchGeometry;

constexpr double areaTolerance = 1e-7;

TopoDS_Wire rectangleWire(double x0, double y0, double x1, double y1, double z = 0.0)
{
    BRepBuilderAPI_MakePolygon polygon;
    polygon.Add(gp_Pnt(x0, y0, z));
    polygon.Add(gp_Pnt(x1, y0, z));
    polygon.Add(gp_Pnt(x1, y1, z));
    polygon.Add(gp_Pnt(x0, y1, z));
    polygon.Close();
    return polygon.Wire();
}

TopoDS_Face rectangle(double x0, double y0, double x1, double y1, double z = 0.0)
{
    return BRepBuilderAPI_MakeFace(rectangleWire(x0, y0, x1, y1, z), true).Face();
}

TopoDS_Wire circleWire(double radius)
{
    const gp_Circ circle(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), radius);
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circle).Edge()).Wire();
}

TopoDS_Face disk(double radius)
{
    return BRepBuilderAPI_MakeFace(circleWire(radius), true).Face();
}

TopoDS_Wire ellipseWire(double majorRadius, double minorRadius)
{
    const gp_Elips ellipse(
        gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)),
        majorRadius,
        minorRadius
    );
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(ellipse).Edge()).Wire();
}

TopoDS_Face withHole(const TopoDS_Wire& outer, const TopoDS_Wire& hole)
{
    BRepBuilderAPI_MakeFace face(outer, true);
    face.Add(TopoDS::Wire(hole.Reversed()));
    return face.Face();
}

TopoDS_Compound compound(std::initializer_list<TopoDS_Shape> shapes)
{
    BRep_Builder builder;
    TopoDS_Compound result;
    builder.MakeCompound(result);
    for (const auto& shape : shapes) {
        builder.Add(result, shape);
    }
    return result;
}

double area(const TopoDS_Shape& shape)
{
    GProp_GProps properties;
    BRepGProp::SurfaceProperties(shape, properties, 1e-10);
    return properties.Mass();
}

void expectArea(const std::vector<TopoDS_Face>& faces, double expected)
{
    double total = 0.0;
    for (const auto& face : faces) {
        ASSERT_FALSE(face.IsNull());
        EXPECT_TRUE(BRepCheck_Analyzer(face).IsValid());
        const double faceArea = area(face);
        EXPECT_GT(faceArea, 0.0);
        total += faceArea;
    }
    // Sum individual faces, not their union: duplicated fill must fail this check.
    EXPECT_NEAR(total, expected, areaTolerance);
}

std::size_t coverageAt(const std::vector<TopoDS_Face>& faces, double x, double y)
{
    return std::count_if(faces.begin(), faces.end(), [=](const TopoDS_Face& face) {
        BRepClass_FaceClassifier classifier(face, gp_Pnt(x, y, 0), Precision::Confusion());
        return classifier.State() == TopAbs_IN || classifier.State() == TopAbs_ON;
    });
}

int subshapeCount(const TopoDS_Shape& shape, TopAbs_ShapeEnum type)
{
    int count = 0;
    for (TopExp_Explorer explorer(shape, type); explorer.More(); explorer.Next()) {
        ++count;
    }
    return count;
}

std::string snapshot(const TopoDS_Shape& shape)
{
    std::ostringstream stream;
    BRepTools::Write(shape, stream);
    return stream.str();
}

class DetailHatchGeometryTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
    }
};

TEST_F(DetailHatchGeometryTest, associatesPartialFacesGeometricallyRegardlessOfOrder)
{
    const auto left = rectangle(0, 0, 6, 4);
    const auto right = rectangle(12, 0, 16, 4);
    const auto clip = rectangle(-1, -2, 25, 6);
    std::vector<TopoDS_Face> details {
        rectangle(14, -1, 18, 3),
        rectangle(3, 1, 9, 5),
        rectangle(50, 0, 51, 1)
    };

    auto result = DetailHatchGeometry::intersect(compound({left, right}), {}, details, clip, {});
    ASSERT_EQ(result.size(), 2);
    expectArea(result, 15.0);
    EXPECT_EQ(coverageAt(result, 4, 2), 1);
    EXPECT_EQ(coverageAt(result, 15, 2), 1);
    EXPECT_EQ(coverageAt(result, 1, 2), 0);
    EXPECT_EQ(coverageAt(result, 7, 2), 0);
    EXPECT_EQ(coverageAt(result, 13, 2), 0);
    EXPECT_EQ(coverageAt(result, 17, 2), 0);

    std::reverse(details.begin(), details.end());
    result = DetailHatchGeometry::intersect(compound({right, left}), {}, details, clip, {});
    ASSERT_EQ(result.size(), 2);
    expectArea(result, 15.0);
    EXPECT_EQ(coverageAt(result, 4, 2), 1);
    EXPECT_EQ(coverageAt(result, 15, 2), 1);
}

TEST_F(DetailHatchGeometryTest, unionsOverlappingAndRepeatedSectionAndDetailFaces)
{
    const auto left = rectangle(0, 0, 6, 4);
    const auto right = rectangle(4, 0, 10, 4);
    const auto section = compound({compound({left, right}), left});
    const auto clip = rectangle(-1, -2, 11, 6);
    std::vector<TopoDS_Face> details {rectangle(1, -1, 7, 5), rectangle(5, -1, 9, 5)};
    details.push_back(details.front());
    // Coincident geometry with distinct topology must not be counted twice either.
    details.push_back(rectangle(1, -1, 7, 5));

    auto result = DetailHatchGeometry::intersect(section, {}, details, clip, {});
    expectArea(result, 32.0);
    EXPECT_EQ(coverageAt(result, 2, 2), 1);
    EXPECT_EQ(coverageAt(result, 5.5, 2), 1);
    EXPECT_EQ(coverageAt(result, 8, 2), 1);
    EXPECT_EQ(coverageAt(result, 0.5, 2), 0);
    EXPECT_EQ(coverageAt(result, 9.5, 2), 0);

    std::reverse(details.begin(), details.end());
    result = DetailHatchGeometry::intersect(section, {}, details, clip, {});
    expectArea(result, 32.0);
}

TEST_F(DetailHatchGeometryTest, keepsExactCircularClipRatherThanItsBoundingBox)
{
    const auto section = rectangle(-10, -10, 10, 10);
    const auto clip = disk(3);
    const auto result = DetailHatchGeometry::intersect(section, {}, {section}, clip, {});

    ASSERT_EQ(result.size(), 1);
    expectArea(result, 9 * std::numbers::pi);
    EXPECT_EQ(coverageAt(result, 2.9, 0), 1);
    EXPECT_EQ(coverageAt(result, 2.9, 2.9), 0);
    EXPECT_EQ(subshapeCount(result.front(), TopAbs_WIRE), 1);
    for (TopExp_Explorer edge(result.front(), TopAbs_EDGE); edge.More(); edge.Next()) {
        BRepAdaptor_Curve curve(TopoDS::Edge(edge.Current()));
        ASSERT_EQ(curve.GetType(), GeomAbs_Circle);
        EXPECT_NEAR(curve.Circle().Radius(), 3.0, areaTolerance);
    }
}

TEST_F(DetailHatchGeometryTest, keepsExactSquareClipIncludingItsCorners)
{
    const auto section = disk(10);
    const auto detail = rectangle(-12, -12, 12, 12);
    const auto clip = rectangle(-3, -3, 3, 3);
    const auto result = DetailHatchGeometry::intersect(section, {}, {detail}, clip, {});

    ASSERT_EQ(result.size(), 1);
    expectArea(result, 36.0);
    EXPECT_EQ(coverageAt(result, 2.9, 2.9), 1);
    EXPECT_EQ(coverageAt(result, 3.1, 0), 0);
    EXPECT_EQ(subshapeCount(result.front(), TopAbs_EDGE), 4);
    for (TopExp_Explorer edge(result.front(), TopAbs_EDGE); edge.More(); edge.Next()) {
        EXPECT_EQ(BRepAdaptor_Curve(TopoDS::Edge(edge.Current())).GetType(), GeomAbs_Line);
    }
}

TEST_F(DetailHatchGeometryTest, preservesThinEllipticalHoleAndAnalyticCurve)
{
    const auto section = withHole(rectangleWire(-10, -2, 10, 2), ellipseWire(8, 0.02));
    const auto detail = rectangle(-9, -1.5, 9, 1.5);
    const auto clip = rectangle(-9, -1, 9, 1);
    const auto result = DetailHatchGeometry::intersect(section, {}, {detail}, clip, {});

    ASSERT_EQ(result.size(), 1);
    expectArea(result, 36 - std::numbers::pi * 8 * 0.02);
    EXPECT_EQ(subshapeCount(result.front(), TopAbs_WIRE), 2);
    EXPECT_EQ(coverageAt(result, 0, 0), 0);
    EXPECT_EQ(coverageAt(result, 0, 0.04), 1);
    EXPECT_EQ(coverageAt(result, 8.5, 0), 1);

    int ellipseEdges = 0;
    for (TopExp_Explorer edge(result.front(), TopAbs_EDGE); edge.More(); edge.Next()) {
        BRepAdaptor_Curve curve(TopoDS::Edge(edge.Current()));
        if (curve.GetType() == GeomAbs_Ellipse) {
            ++ellipseEdges;
            EXPECT_NEAR(curve.Ellipse().MajorRadius(), 8.0, areaTolerance);
            EXPECT_NEAR(curve.Ellipse().MinorRadius(), 0.02, areaTolerance);
        }
    }
    EXPECT_GT(ellipseEdges, 0);
}

TEST_F(DetailHatchGeometryTest, preservesHolesInDetailCoverage)
{
    const auto section = rectangle(-6, -6, 6, 6);
    const auto detail = withHole(rectangleWire(-5, -5, 5, 5), circleWire(1));
    const auto clip = rectangle(-4, -4, 4, 4);
    const auto result = DetailHatchGeometry::intersect(section, {}, {detail}, clip, {});

    ASSERT_EQ(result.size(), 1);
    expectArea(result, 64 - std::numbers::pi);
    EXPECT_EQ(subshapeCount(result.front(), TopAbs_WIRE), 2);
    EXPECT_EQ(coverageAt(result, 0, 0), 0);
    EXPECT_EQ(coverageAt(result, 2, 0), 1);
}

TEST_F(DetailHatchGeometryTest, subtractsUnionOfExplicitFacesEvenUnderOtherCoverageAndRestores)
{
    const auto section = rectangle(0, 0, 10, 10);
    const auto clip = rectangle(-1, -1, 11, 11);
    const std::vector<TopoDS_Face> details {
        section,
        rectangle(4, 2, 12, 8),
        rectangle(6, 4, 9, 12)
    };

    auto result = DetailHatchGeometry::intersect(section, {}, details, clip, {1, 2, 1});
    // Inside the section, the exclusions cover 36 + 18 - 12 square units.
    expectArea(result, 58.0);
    EXPECT_EQ(coverageAt(result, 5, 3), 0);
    EXPECT_EQ(coverageAt(result, 7, 9), 0);
    EXPECT_EQ(coverageAt(result, 7, 5), 0);
    EXPECT_EQ(coverageAt(result, 1, 1), 1);
    EXPECT_EQ(coverageAt(result, 9.5, 9), 1);

    result = DetailHatchGeometry::intersect(section, {}, details, clip, {1});
    expectArea(result, 64.0);
    EXPECT_EQ(coverageAt(result, 7, 9), 1);
    result = DetailHatchGeometry::intersect(section, {}, details, clip, {});
    expectArea(result, 100.0);
    EXPECT_EQ(coverageAt(result, 7, 5), 1);
    EXPECT_TRUE(DetailHatchGeometry::intersect(section, {}, details, clip, {0}).empty());
}

TEST_F(DetailHatchGeometryTest, partialExclusionSplitsOneRegionIntoDisconnectedFaces)
{
    const auto section = rectangle(0, 0, 10, 4);
    const auto clip = rectangle(-1, -1, 11, 5);
    const std::vector<TopoDS_Face> details {section, rectangle(4, -1, 6, 5)};
    const auto result = DetailHatchGeometry::intersect(section, {}, details, clip, {1});

    ASSERT_EQ(result.size(), 2);
    expectArea(result, 32.0);
    EXPECT_EQ(coverageAt(result, 2, 2), 1);
    EXPECT_EQ(coverageAt(result, 5, 2), 0);
    EXPECT_EQ(coverageAt(result, 8, 2), 1);
    expectArea(DetailHatchGeometry::intersect(section, {}, details, clip, {}), 40.0);
}

TEST_F(DetailHatchGeometryTest, exclusionWithAHoleLeavesTheIslandInsideIt)
{
    const auto section = rectangle(-5, -5, 5, 5);
    const auto exclusion = withHole(rectangleWire(-3, -3, 3, 3), circleWire(1));
    const auto result = DetailHatchGeometry::intersect(
        section, {}, {section, exclusion}, section, {1}
    );

    ASSERT_EQ(result.size(), 2);
    expectArea(result, 64 + std::numbers::pi);
    EXPECT_EQ(coverageAt(result, 0, 0), 1);
    EXPECT_EQ(coverageAt(result, 2, 0), 0);
    EXPECT_EQ(coverageAt(result, 4, 0), 1);
}

TEST_F(DetailHatchGeometryTest, nullHollowEntriesKeepExclusionIndicesStable)
{
    const auto section = rectangle(0, 0, 10, 4);
    const auto clip = rectangle(-1, -1, 11, 5);
    std::vector<TopoDS_Face> details {
        {}, rectangle(0, 0, 4, 4), {}, rectangle(6, 0, 10, 4)
    };

    auto result = DetailHatchGeometry::intersect(section, {}, details, clip, {3, 3, -1, 4});
    expectArea(result, 16.0);
    EXPECT_EQ(coverageAt(result, 2, 2), 1);
    EXPECT_EQ(coverageAt(result, 8, 2), 0);
    expectArea(DetailHatchGeometry::intersect(section, {}, details, clip, {0, 2, -1, 4}), 32.0);
    EXPECT_TRUE(DetailHatchGeometry::intersect(section, {}, details, clip, {1, 3}).empty());

    std::reverse(details.begin(), details.end());
    result = DetailHatchGeometry::intersect(section, {}, details, clip, {0});
    expectArea(result, 16.0);
    EXPECT_EQ(coverageAt(result, 2, 2), 1);
    EXPECT_EQ(coverageAt(result, 8, 2), 0);
}

TEST_F(DetailHatchGeometryTest, transformsSectionBeforeIntersectionButNotDetailOrClip)
{
    const auto section = rectangle(0, 0, 3, 1);
    const auto detail = rectangle(8.5, 21, 12, 25);
    const auto clip = rectangle(7, 22, 11, 24);
    gp_Trsf scale;
    scale.SetScale(gp_Pnt(0, 0, 0), 2);
    gp_Trsf rotation;
    rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), std::numbers::pi / 2);
    gp_Trsf translation;
    translation.SetTranslation(gp_Vec(10, 20, 0));
    const gp_Trsf toDetail = translation * rotation * scale;

    const auto result = DetailHatchGeometry::intersect(section, toDetail, {detail}, clip, {});
    ASSERT_EQ(result.size(), 1);
    expectArea(result, 3.0);
    EXPECT_EQ(coverageAt(result, 9, 23), 1);
    EXPECT_EQ(coverageAt(result, 8.25, 23), 0);
    EXPECT_NEAR(area(section), 3.0, areaTolerance);
    EXPECT_TRUE(DetailHatchGeometry::intersect(section, {}, {detail}, clip, {}).empty());
}

TEST_F(DetailHatchGeometryTest, supportsReflectionAndTranslationOntoTheDetailPlane)
{
    const auto section = rectangle(0, 1, 3, 2, 5);
    const auto detail = rectangle(-1, -3, 4, 0);
    const auto clip = rectangle(1, -2, 2, -1);
    gp_Trsf mirror;
    mirror.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)));
    gp_Trsf translation;
    translation.SetTranslation(gp_Vec(0, 0, -5));

    const auto result = DetailHatchGeometry::intersect(
        section, translation * mirror, {detail}, clip, {}
    );
    ASSERT_EQ(result.size(), 1);
    expectArea(result, 1.0);
    EXPECT_EQ(coverageAt(result, 1.5, -1.5), 1);
}

TEST_F(DetailHatchGeometryTest, returnsEmptyForNullEmptyAndFacelessInputs)
{
    const auto face = rectangle(0, 0, 2, 2);
    EXPECT_TRUE(DetailHatchGeometry::intersect({}, {}, {face}, face, {}).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(face, {}, {}, face, {}).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(face, {}, {face}, {}, {}).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(compound({}), {}, {face}, face, {}).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(face, {}, {TopoDS_Face(), TopoDS_Face()}, face, {}).empty());
    const auto edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(1, 0, 0)).Edge();
    EXPECT_TRUE(DetailHatchGeometry::intersect(compound({edge}), {}, {face}, face, {}).empty());
}

TEST_F(DetailHatchGeometryTest, returnsEmptyForDisjointAndBoundaryOnlyIntersections)
{
    const auto section = rectangle(0, 0, 1, 1);
    const auto clip = rectangle(-3, -3, 3, 3);
    EXPECT_TRUE(DetailHatchGeometry::intersect(
        section, {}, {rectangle(2, 0, 3, 1)}, clip, {}
    ).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(
        section, {}, {rectangle(1, 0, 2, 1)}, clip, {}
    ).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(
        section, {}, {rectangle(1, 1, 2, 2)}, clip, {}
    ).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(
        section, {}, {rectangle(0, 0, 1, 1, 1)}, clip, {}
    ).empty());
    EXPECT_TRUE(DetailHatchGeometry::intersect(
        section, {}, {section}, rectangle(2, 2, 3, 3), {}
    ).empty());
    // Bounding boxes overlap here, but the actual regions do not.
    EXPECT_TRUE(DetailHatchGeometry::intersect(
        disk(1), {}, {rectangle(0.8, 0.8, 1.2, 1.2)}, clip, {}
    ).empty());
}

TEST_F(DetailHatchGeometryTest, doesNotModifyInputTopologyGeometryOrTolerances)
{
    const auto section = compound({rectangle(0, 0, 6, 4), rectangle(4, 0, 10, 4)});
    const std::vector<TopoDS_Face> details {
        rectangle(1, -1, 8, 5), rectangle(6, -1, 13, 5), rectangle(5, 1, 7, 3)
    };
    const auto clip = rectangle(0, 0, 10, 4);
    const auto inputs = compound({section, details[0], details[1], details[2], clip});
    const auto before = snapshot(inputs);
    gp_Trsf toDetail;
    toDetail.SetTranslation(gp_Vec(2, 0, 0));

    const auto result = DetailHatchGeometry::intersect(section, toDetail, details, clip, {2});
    EXPECT_EQ(snapshot(inputs), before);
    expectArea(result, 28.0);
}

TEST_F(DetailHatchGeometryTest, patTrimmingDoesNotBridgeThinEllipticalHole)
{
    const auto section = withHole(rectangleWire(-10, -2, 10, 2), ellipseWire(8, 0.02));
    const auto detail = rectangle(-9, -1.5, 9, 1.5);
    const auto clip = rectangle(-9, -1, 9, 1);
    const auto result = DetailHatchGeometry::intersect(section, {}, {detail}, clip, {});
    ASSERT_EQ(result.size(), 1);

    // An in-memory PAT specification avoids resource files and document setup.
    std::string spec = "0,0,0,0,0.1";
    TechDraw::LineSet pattern;
    pattern.setPATLineSpec(TechDraw::PATLineSpec(spec));
    auto lines = TechDraw::DrawGeomHatch::getTrimmedLines(
        nullptr, {pattern}, result.front(), 1.0
    );
    ASSERT_EQ(lines.size(), 1);
    const auto edges = lines.front().getEdges();
    ASSERT_FALSE(edges.empty());

    double centerLineLength = 0.0;
    int centerLineEdges = 0;
    for (const auto& edge : edges) {
        BRepAdaptor_Curve curve(edge);
        ASSERT_EQ(curve.GetType(), GeomAbs_Line);
        const auto start = curve.Value(curve.FirstParameter());
        const auto end = curve.Value(curve.LastParameter());
        const auto middle = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2);
        EXPECT_EQ(coverageAt(result, middle.X(), middle.Y()), 1);
        if (std::abs(start.Y()) < Precision::Confusion()
            && std::abs(end.Y()) < Precision::Confusion()) {
            ++centerLineEdges;
            centerLineLength += start.Distance(end);
            EXPECT_GT(std::abs(middle.X()), 8.0);
        }
    }
    // At y = 0 only [-9,-8] and [8,9] may be hatched.
    EXPECT_EQ(centerLineEdges, 2);
    EXPECT_NEAR(centerLineLength, 2.0, areaTolerance);
}

}  // namespace

// NOLINTEND(readability-magic-numbers,cppcoreguidelines-avoid-magic-numbers)
