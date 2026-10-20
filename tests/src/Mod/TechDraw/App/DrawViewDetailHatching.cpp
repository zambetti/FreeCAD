// SPDX-License-Identifier: LGPL-2.1-or-later

#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <iostream>
#include <memory>
#include <numbers>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <BRepAdaptor_Curve.hxx>
#include <BRepBuilderAPI_MakeEdge.hxx>
#include <BRepBuilderAPI_MakeFace.hxx>
#include <BRepBuilderAPI_MakePolygon.hxx>
#include <BRepBuilderAPI_MakeWire.hxx>
#include <BRepCheck_Analyzer.hxx>
#include <BRepGProp.hxx>
#include <BRepPrimAPI_MakeBox.hxx>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <GProp_GProps.hxx>
#include <GeomAbs_CurveType.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Ax2.hxx>
#include <gp_Dir.hxx>
#include <gp_Elips.hxx>
#include <gp_Pnt.hxx>
#include <gp_Trsf.hxx>
#include <gp_Vec.hxx>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Interpreter.h>
#include <Base/Parameter.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/TechDraw/App/CenterLine.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawTemplate.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewDetail.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>
#include <Mod/TechDraw/App/DrawViewSection.h>
#include <Mod/TechDraw/App/Geometry.h>
#include <Mod/TechDraw/App/GeometryObject.h>
#include <Mod/TechDraw/App/HatchLine.h>
#include <Mod/TechDraw/App/Preferences.h>

#include "src/App/InitApplication.h"

// NOLINTBEGIN(readability-magic-numbers,cppcoreguidelines-avoid-magic-numbers)

namespace
{

constexpr double tolerance = 1e-6;

TopoDS_Face rectangle(double x0, double y0, double x1, double y1, double z = 0.0)
{
    BRepBuilderAPI_MakePolygon wire;
    wire.Add(gp_Pnt(x0, y0, z));
    wire.Add(gp_Pnt(x1, y0, z));
    wire.Add(gp_Pnt(x1, y1, z));
    wire.Add(gp_Pnt(x0, y1, z));
    wire.Close();
    return BRepBuilderAPI_MakeFace(wire.Wire(), true).Face();
}

TopoDS_Compound compound(const std::vector<TopoDS_Face>& faces)
{
    BRep_Builder builder;
    TopoDS_Compound result;
    builder.MakeCompound(result);
    for (const auto& face : faces) {
        builder.Add(result, face);
    }
    return result;
}

void expectArea(const std::vector<TopoDS_Face>& faces, double expected)
{
    double total = 0.0;
    for (const auto& face : faces) {
        ASSERT_FALSE(face.IsNull());
        EXPECT_TRUE(BRepCheck_Analyzer(face).IsValid());
        GProp_GProps properties;
        BRepGProp::SurfaceProperties(face, properties, 1e-10);
        EXPECT_GT(properties.Mass(), 0.0);
        total += properties.Mass();
    }
    EXPECT_NEAR(total, expected, tolerance);
}

void expectSameFaces(const std::vector<TopoDS_Face>& first,
                     const std::vector<TopoDS_Face>& second)
{
    ASSERT_FALSE(first.empty());
    ASSERT_EQ(first.size(), second.size());
    for (std::size_t i = 0; i < first.size(); ++i) {
        EXPECT_TRUE(first[i].IsSame(second[i]));
    }
}

std::size_t centerLineCount(const TechDraw::DrawViewPart& view)
{
    const auto edges = view.getEdgeGeometry();
    return std::count_if(edges.begin(), edges.end(), [](const TechDraw::BaseGeomPtr& edge) {
        return edge->source() == TechDraw::SourceType::CENTERLINE;
    });
}

// Restore both values and the absence of a key; tests must not alter the user's
// face-finder, matting, or drawing-update preferences for later tests.
class PreferencesScope
{
public:
    ~PreferencesScope()
    {
        for (auto it = saved.rbegin(); it != saved.rend(); ++it) {
            if (it->value) {
                it->group->SetAttribute(it->type, it->key.c_str(), it->value->c_str());
            }
            else {
                it->group->RemoveAttribute(it->type, it->key.c_str());
            }
        }
    }

    void setBool(const char* groupName, const char* key, bool value)
    {
        const auto group = remember(groupName, ParameterGrp::ParamType::FCBool, key);
        group->SetBool(key, value);
    }

    void setInt(const char* groupName, const char* key, long value)
    {
        const auto group = remember(groupName, ParameterGrp::ParamType::FCInt, key);
        group->SetInt(key, value);
    }

private:
    struct Saved
    {
        Base::Reference<ParameterGrp> group;
        ParameterGrp::ParamType type;
        std::string key;
        std::optional<std::string> value;
    };
    std::vector<Saved> saved;

    Base::Reference<ParameterGrp> remember(const char* groupName,
                                         ParameterGrp::ParamType type,
                                         const char* key)
    {
        const auto group = TechDraw::Preferences::getPreferenceGroup(groupName);
        Saved entry {group, type, key, std::nullopt};
        for (const auto& [name, value] : group->GetAttributeMap(type)) {
            if (name == key) {
                entry.value = value;
                break;
            }
        }
        saved.push_back(std::move(entry));
        return group;
    }
};

class TestSection: public TechDraw::DrawViewSection
{
public:
    void seedFaces(const std::vector<TopoDS_Face>& faces, bool ready = true)
    {
        m_sectionTopoDSFaces = compound(faces);
        m_sectionFaceScale = getScale();
        m_sectionFaceRotation = Rotation.getValue();
        m_faceExtractionSucceeded = ready && !faces.empty();
        m_sectionFacesReady = ready;
    }

    void setFacesReady(bool ready)
    {
        m_sectionFacesReady = ready;
    }

    void completeFaces()
    {
        sectionFacesFinished();
    }

    void seedEmptyCut()
    {
        // Preserve the previous projection until the real completion callback runs.
        m_cutPieces = compound({});
        m_sectionFacesReady = false;
        waitingForCut(true);
    }

    void seedPattern(std::string spec = "0,0,0,0,1")
    {
        TechDraw::LineSet line;
        line.setPATLineSpec(TechDraw::PATLineSpec(spec));
        m_lineSets = {line};
    }
};

class TestDetail: public TechDraw::DrawViewDetail
{
public:
    int executeCalls = 0;

    App::DocumentObjectExecReturn* execute() override
    {
        ++executeCalls;
        return DrawViewDetail::execute();
    }

    void seedAcceptedFrame(const TopoDS_Shape& sectionFaces, double radius,
                           const gp_Trsf& transform = {})
    {
        m_hatchSource = dynamic_cast<TechDraw::DrawViewSection*>(BaseView.getValue());
        m_hatchSectionFaces = sectionFaces;
        m_hatchTransform = transform;
        m_hatchRadius = radius;
        m_hatchCacheValid = false;
        m_faceExtractionSucceeded = true;
        m_inheritedFacesReady = true;
    }

    void replaceCoverage(const std::vector<TechDraw::FacePtr>& faces)
    {
        geometryObject = std::make_shared<TechDraw::GeometryObject>(getNameInDocument(), this);
        for (const auto& face : faces) {
            geometryObject->addFaceGeom(face);
            for (const auto* wire : face->wires) {
                for (const auto& edge : wire->geoms) {
                    geometryObject->addEdge(edge);
                }
            }
        }
        // Deliberately do not invalidate the cache: its coverage key must notice
        // replacement FacePtrs, just as it must after face extraction.
    }

    void setFacesReady(bool ready) { m_inheritedFacesReady = ready; }
    void setCapturedRadius(double radius) { m_hatchRadius = radius; }
    bool facesReady() const { return m_inheritedFacesReady; }
    bool cacheValid() const { return m_hatchCacheValid; }
    bool pending() const { return m_pendingRecompute; }
    bool updateActive() const { return m_detailUpdateActive; }
    double capturedRadius() const { return m_hatchRadius; }
    const TopoDS_Shape& capturedSection() const { return m_hatchSectionFaces; }
    const std::vector<int>& cachedExclusions() const { return m_cachedExcludedFaces; }

    void beginAcceptedUpdate()
    {
        m_detailUpdateActive = true;
        m_faceExtractionSucceeded = false;
        m_inheritedFacesReady = false;
        m_hatchCacheValid = false;
        overrideKeepUpdated(true);
    }

    void completeFaceExtraction(bool succeeded)
    {
        m_faceExtractionSucceeded = succeeded;
        postFaceExtractionTasks();
    }
};

class ObservedDimension: public TechDraw::DrawViewDimension
{
public:
    int executeCalls = 0;
    bool wasAuthorized = false;
    std::size_t centerLinesAtExecute = 0;

    App::DocumentObjectExecReturn* execute() override
    {
        ++executeCalls;
        wasAuthorized = keepUpdated();
        if (const auto* view = getViewPart()) {
            centerLinesAtExecute = centerLineCount(*view);
        }
        return DrawViewDimension::execute();
    }
};

class DrawViewDetailHatchingTest: public ::testing::Test
{
protected:
    static void SetUpTestSuite()
    {
        tests::initApplication();
        // Register base types before constructing the test subclasses. Calling
        // addObject<TestDetail>() would create a base DrawViewDetail, not our subclass.
        Base::Interpreter().loadModule("TechDraw");
    }

    void SetUp() override
    {
        preferences.setBool("General", "HandleFaces", true);
        preferences.setBool("General", "IdentifyVoids", true);
        preferences.setBool("General", "GlobalUpdateDrawings", true);
        preferences.setInt("General", "FaceFinderVersion",
                           static_cast<long>(TechDraw::FaceFinderVersion::v26_3));
        preferences.setInt("Decorations", "MattingStyle", 1);
        preferences.setBool("Dimensions", "AutoCorrectRefs", false);
        preferences.setBool("debug", "debugDetail", false);
        preferences.setBool("debug", "debugSection", false);

        documentName = App::GetApplication().getUniqueDocumentName("DetailHatchingTest");
        document = App::GetApplication().newDocument(documentName.c_str(), "testUser");
        page = document->addObject<TechDraw::DrawPage>("Page");
        page->KeepUpdated.setValue(false);
        // Even a headless dimension's page-fit check requires a sized template.
        auto* pageTemplate = document->addObject<TechDraw::DrawTemplate>("Template");
        pageTemplate->Width.setValue(210.0);
        pageTemplate->Height.setValue(297.0);
        page->Template.setValue(pageTemplate);

        auto ownedSection = std::make_unique<TestSection>();
        document->addObject(ownedSection.get(), "Section");
        section = ownedSection.release();
        auto ownedDetail = std::make_unique<TestDetail>();
        document->addObject(ownedDetail.get(), "Detail");
        detail = ownedDetail.release();
        page->addView(section, false);
        page->addView(detail, false);
        section->ScaleType.setValue("Custom");
        section->Scale.setValue(1.0);
        section->SectionNormal.setValue(0, 0, 1);
        section->XDirection.setValue(1, 0, 0);
        section->CoarseView.setValue(false);
        detail->BaseView.setValue(section);
        detail->ScaleType.setValue("Custom");
        detail->Scale.setValue(1.0);
        detail->Direction.setValue(0, 0, 1);
        detail->XDirection.setValue(1, 0, 0);
        detail->CoarseView.setValue(false);
        detail->ShowMatting.setValue(true);
    }

    void TearDown() override
    {
        if (document) {
            App::GetApplication().closeDocument(documentName.c_str());
        }
    }

    void seed(const TopoDS_Face& face = rectangle(-6, -4, 6, 4), double radius = 10.0)
    {
        section->seedFaces({face});
        section->seedPattern();
        detail->replaceCoverage({std::make_shared<TechDraw::Face>(face)});
        detail->seedAcceptedFrame(section->getSectionFaces(), radius);
    }

    void buildBoxPipeline(double sectionRotation = 0.0, double detailRotation = 0.0)
    {
        ASSERT_FALSE(TechDraw::DrawUtil::isGuiUp());
        auto* box = document->addObject<Part::Feature>("Box");
        box->Shape.setValue(BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, -2), 20, 20, 4).Shape());
        auto* base = document->addObject<TechDraw::DrawViewPart>("Base");
        page->addView(base, false);
        base->Source.setValues({box});
        base->Direction.setValue(0, 0, 1);
        base->XDirection.setValue(1, 0, 0);
        base->ScaleType.setValue("Custom");
        base->Scale.setValue(1.0);
        base->CoarseView.setValue(false);
        section->BaseView.setValue(base);
        section->SectionOrigin.setValue(0, 0, 0);
        section->Scale.setValue(1.5);
        section->Rotation.setValue(sectionRotation);
        detail->Radius.setValue(3.0);
        detail->Scale.setValue(2.0);
        detail->AnchorPoint.setValue(2, 1, 0);
        detail->Rotation.setValue(detailRotation);

        // Real document recompute, section cut, HLR and face extraction. In this
        // App-only runner the completion callbacks execute synchronously.
        page->forceRedraw(true);
        document->recompute();
        page->forceRedraw(false);
        ASSERT_TRUE(section->hasCompletedSectionFaces());
        ASSERT_TRUE(section->faceExtractionSucceeded());
        ASSERT_TRUE(section->hasGeometry());
        ASSERT_FALSE(section->getSectionFaces().IsNull());
        ASSERT_TRUE(detail->hasGeometry());
        ASSERT_TRUE(detail->faceExtractionSucceeded());
        ASSERT_TRUE(detail->facesReady());
        ASSERT_FALSE(detail->waitingForResult());
    }

    PreferencesScope preferences;
    std::string documentName;
    App::Document* document = nullptr;
    TechDraw::DrawPage* page = nullptr;
    TestSection* section = nullptr;
    TestDetail* detail = nullptr;
};

TEST_F(DrawViewDetailHatchingTest, styleChangesAndRepaintReuseOccCacheWithoutChangingSelectionFaces)
{
    seed();
    const auto selectionFaces = detail->getFaceGeometry();
    const auto inherited = detail->getInheritedSectionFaces();
    expectArea(inherited, 96.0);
    ASSERT_TRUE(detail->cacheValid());

    for (const auto* style : {"Hide", "Color", "SvgHatch"}) {
        section->CutSurfaceDisplay.setValue(style);
        section->HatchScale.setValue(2.0);
        section->HatchRotation.setValue(37.0);
        section->HatchOffset.setValue(0.25, 0.5, 0);
        section->requestPaint();
        detail->requestPaint();
        expectSameFaces(inherited, detail->getInheritedSectionFaces());
        EXPECT_EQ(detail->getFaceGeometry(), selectionFaces);
    }
}

TEST_F(DrawViewDetailHatchingTest, hundredSectionAndCoverageFacesReuseCacheAcrossStyleChanges)
{
    constexpr int side = 10;
    constexpr int faceCount = side * side;
    constexpr int cachedCalls = 100;
    constexpr double expectedArea = faceCount * 4.0;
    std::vector<TopoDS_Face> sectionFaces;
    std::vector<TechDraw::FacePtr> coverage;
    sectionFaces.reserve(faceCount);
    coverage.reserve(faceCount);
    for (int row = 0; row < side; ++row) {
        for (int column = 0; column < side; ++column) {
            const double x = 3.0 * column - 14.5;
            const double y = 3.0 * row - 14.5;
            sectionFaces.push_back(rectangle(x, y, x + 2.0, y + 2.0));
            // Adjacent masks overlap inside the next section rectangle; the
            // inherited union must fill each 2 x 2 section exactly once.
            coverage.push_back(std::make_shared<TechDraw::Face>(
                rectangle(x - 1.0, y, x + 3.5, y + 2.0)));
        }
    }
    section->seedFaces(sectionFaces);
    section->HatchScale.setValue(1.0);
    detail->replaceCoverage(coverage);
    detail->seedAcceptedFrame(section->getSectionFaces(), 50.0);
    ASSERT_EQ(sectionFaces.size(), faceCount);
    ASSERT_EQ(detail->getFaceGeometry().size(), faceCount);
    ASSERT_TRUE(detail->faceExtractionSucceeded());
    ASSERT_TRUE(detail->facesReady());
    ASSERT_FALSE(detail->cacheValid());
    const int executions = detail->executeCalls;

    using Clock = std::chrono::steady_clock;
    const auto firstStart = Clock::now();
    const auto inherited = detail->getInheritedSectionFaces();
    const auto firstElapsed = Clock::now() - firstStart;
    ASSERT_TRUE(detail->cacheValid());
    ASSERT_FALSE(inherited.empty());
    // OCC may retain partitions at mask overlaps; summed area must not duplicate fill.
    expectArea(inherited, expectedArea);

    // Measure only getters, excluding style changes and topology/area assertions.
    Clock::duration cachedElapsed = Clock::duration::zero();
    for (int iteration = 0; iteration < cachedCalls; ++iteration) {
        SCOPED_TRACE(iteration);
        if (iteration % 10 == 0) {
            section->HatchScale.setValue(2.0 + iteration / 10);
        }
        const auto cachedStart = Clock::now();
        const auto cached = detail->getInheritedSectionFaces();
        cachedElapsed += Clock::now() - cachedStart;
        EXPECT_TRUE(detail->cacheValid());
        expectSameFaces(inherited, cached);
    }
    EXPECT_EQ(detail->getFaceGeometry(), coverage);
    EXPECT_EQ(detail->executeCalls, executions);
    std::cout << "[ detail hatch 100x100 ] first getter: "
              << std::chrono::duration<double, std::milli>(firstElapsed).count()
              << " ms; " << cachedCalls << " cached getters total: "
              << std::chrono::duration<double, std::milli>(cachedElapsed).count()
              << " ms\n";
}

TEST_F(DrawViewDetailHatchingTest, normalizedExclusionsReuseCacheAndChangedExclusionsRestoreCoverage)
{
    seed();
    const auto full = std::make_shared<TechDraw::Face>(rectangle(-6, -4, 6, 4));
    const auto left = std::make_shared<TechDraw::Face>(rectangle(-6, -4, -2, 4));
    const auto right = std::make_shared<TechDraw::Face>(rectangle(2, -4, 6, 4));
    detail->replaceCoverage({full, left, right});
    const auto selectionFaces = detail->getFaceGeometry();

    const auto middle = detail->getInheritedSectionFaces({2, 1, 2});
    expectArea(middle, 32.0);
    EXPECT_EQ(detail->cachedExclusions(), (std::vector<int> {1, 2}));
    expectSameFaces(middle, detail->getInheritedSectionFaces({1, 2}));
    expectArea(detail->getInheritedSectionFaces({1}), 64.0);
    const auto restored = detail->getInheritedSectionFaces();
    expectArea(restored, 96.0);
    expectSameFaces(restored, detail->getInheritedSectionFaces());
    EXPECT_TRUE(detail->getInheritedSectionFaces({0}).empty());
    EXPECT_TRUE(detail->cacheValid());
    EXPECT_EQ(detail->getFaceGeometry(), selectionFaces);
}

TEST_F(DrawViewDetailHatchingTest, replacementCoverageInvalidatesCacheEvenWithEqualGeometry)
{
    seed();
    const auto original = detail->getInheritedSectionFaces();
    ASSERT_EQ(original.size(), 1);
    detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(-6, -4, 6, 4))});
    const auto replaced = detail->getInheritedSectionFaces();
    ASSERT_EQ(replaced.size(), 1);
    expectArea(replaced, 96.0);
    EXPECT_FALSE(original.front().IsSame(replaced.front()));
    expectSameFaces(replaced, detail->getInheritedSectionFaces());

    detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(0, -4, 6, 4))});
    expectArea(detail->getInheritedSectionFaces(), 48.0);
}

TEST_F(DrawViewDetailHatchingTest, mutatingTheSameFaceToHollowInvalidatesCachedCoverage)
{
    seed();
    const auto coverage = detail->getFaceGeometry();
    ASSERT_EQ(coverage.size(), 1);
    const auto original = detail->getInheritedSectionFaces();
    expectArea(original, 96.0);
    ASSERT_TRUE(detail->cacheValid());
    expectSameFaces(original, detail->getInheritedSectionFaces());

    coverage.front()->setRepresentation(TechDraw::FaceRepresentation::Hollow);
    ASSERT_EQ(detail->getFaceGeometry(), coverage);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());

    coverage.front()->setRepresentation(TechDraw::FaceRepresentation::Common);
    ASSERT_EQ(detail->getFaceGeometry(), coverage);
    const auto restored = detail->getInheritedSectionFaces();
    expectArea(restored, 96.0);
    EXPECT_TRUE(detail->cacheValid());
    expectSameFaces(restored, detail->getInheritedSectionFaces());
}

TEST_F(DrawViewDetailHatchingTest, readinessAndBusyStagesDoNotPublishOrCachePrematureEmptyResults)
{
    seed();
    section->setFacesReady(false);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_FALSE(detail->cacheValid());
    section->setFacesReady(true);
    detail->setFacesReady(false);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_FALSE(detail->cacheValid());
    detail->setFacesReady(true);

    detail->waitingForDetail(true);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    detail->waitingForDetail(false);
    detail->waitingForHlr(true);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    detail->waitingForHlr(false);
    detail->waitingForFaces(true);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    detail->waitingForFaces(false);
    section->waitingForCut(true);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    section->waitingForCut(false);
    EXPECT_FALSE(detail->cacheValid());

    const auto completed = detail->getInheritedSectionFaces();
    expectArea(completed, 96.0);
    section->setFacesReady(false);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    section->setFacesReady(true);
    expectSameFaces(completed, detail->getInheritedSectionFaces());
}

TEST_F(DrawViewDetailHatchingTest, emptyOrFailedFaceExtractionDoesNotPublishOrCacheInheritedFaces)
{
    for (const bool failedRepresentation : {false, true}) {
        SCOPED_TRACE(failedRepresentation ? "failed representation" : "no extracted faces");
        seed();
        expectArea(detail->getInheritedSectionFaces(), 96.0);
        ASSERT_TRUE(detail->cacheValid());
        const auto coverage = detail->getFaceGeometry();
        ASSERT_EQ(coverage.size(), 1);

        detail->beginAcceptedUpdate();
        if (failedRepresentation) {
            coverage.front()->setRepresentation(TechDraw::FaceRepresentation::Failed);
        }
        else {
            detail->getGeometryObject()->clearFaceGeom();
            ASSERT_TRUE(detail->getFaceGeometry().empty());
        }
        // HLR edges can survive even when face extraction supplies no usable faces.
        ASSERT_TRUE(detail->hasGeometry());
        ASSERT_FALSE(detail->getEdgeGeometry().empty());
        ASSERT_TRUE(section->hasCompletedSectionFaces());
        detail->completeFaceExtraction(false);

        EXPECT_FALSE(detail->faceExtractionSucceeded());
        EXPECT_FALSE(detail->facesReady());
        EXPECT_FALSE(detail->cacheValid());
        EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
        EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
        EXPECT_FALSE(detail->cacheValid());
        EXPECT_FALSE(detail->updateActive());
        EXPECT_FALSE(detail->pending());
        EXPECT_FALSE(detail->waitingForResult());
        EXPECT_FALSE(detail->overrideKeepUpdated());

        // A later successful extraction, not a repaint, makes the frame cacheable again.
        detail->beginAcceptedUpdate();
        detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(-6, -4, 6, 4))});
        detail->completeFaceExtraction(true);
        EXPECT_TRUE(detail->faceExtractionSucceeded());
        EXPECT_TRUE(detail->facesReady());
        const auto recovered = detail->getInheritedSectionFaces();
        expectArea(recovered, 96.0);
        EXPECT_TRUE(detail->cacheValid());
        expectSameFaces(recovered, detail->getInheritedSectionFaces());
    }
}

TEST_F(DrawViewDetailHatchingTest, completedEmptyIntersectionIsCachedButInvalidClipIsNot)
{
    seed();
    detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(20, 20, 22, 22))});
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_TRUE(detail->cacheValid());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(-6, -4, 6, 4))});
    expectArea(detail->getInheritedSectionFaces(), 96.0);

    detail->seedAcceptedFrame(section->getSectionFaces(), 0.0);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_FALSE(detail->cacheValid());
    detail->setCapturedRadius(2.0);
    expectArea(detail->getInheritedSectionFaces(), 16.0);
    EXPECT_TRUE(detail->cacheValid());
}

TEST_F(DrawViewDetailHatchingTest, hollowCoverageKeepsOrdinaryFaceExclusionIndices)
{
    seed();
    auto hollow = std::make_shared<TechDraw::Face>(rectangle(-6, -4, 6, 4));
    hollow->setRepresentation(TechDraw::FaceRepresentation::Hollow);
    const auto left = std::make_shared<TechDraw::Face>(rectangle(-6, -4, 0, 4));
    const auto right = std::make_shared<TechDraw::Face>(rectangle(0, -4, 6, 4));
    detail->replaceCoverage({hollow, left, right});

    expectArea(detail->getInheritedSectionFaces({2}), 48.0);
    expectArea(detail->getInheritedSectionFaces({0}), 96.0);
    EXPECT_TRUE(detail->getInheritedSectionFaces({1, 2}).empty());
    EXPECT_EQ(detail->getFaceGeometry(), (std::vector<TechDraw::FacePtr> {hollow, left, right}));
}

TEST_F(DrawViewDetailHatchingTest, frozenPropertyEditsUseTheLastAcceptedFrameAndRadius)
{
    section->seedFaces({rectangle(2, -1, 6, 1)});
    detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(-10, -10, 10, 10))});
    gp_Trsf transform;
    transform.SetTranslation(gp_Vec(-4, 0, 0));
    detail->seedAcceptedFrame(section->getSectionFaces(), 3.0, transform);
    const auto accepted = detail->getInheritedSectionFaces();
    expectArea(accepted, 8.0);
    ASSERT_FALSE(page->KeepUpdated.getValue());
    ASSERT_FALSE(detail->keepUpdated());

    detail->Radius.setValue(0.5);
    detail->Scale.setValue(4.0);
    detail->Rotation.setValue(90.0);
    detail->AnchorPoint.setValue(50, 50, 0);
    ASSERT_TRUE(detail->recomputeFeature());
    EXPECT_DOUBLE_EQ(detail->capturedRadius(), 3.0);
    expectSameFaces(accepted, detail->getInheritedSectionFaces());
}

TEST_F(DrawViewDetailHatchingTest, frozenBaseViewSwitchDoesNotReuseThePreviousSectionsAcceptedFrame)
{
    seed();
    const auto accepted = detail->getInheritedSectionFaces();
    expectArea(accepted, 96.0);
    ASSERT_TRUE(detail->cacheValid());
    expectSameFaces(accepted, detail->getInheritedSectionFaces());

    auto ownedSection = std::make_unique<TestSection>();
    document->addObject(ownedSection.get(), "SectionB");
    auto* sectionB = ownedSection.release();
    page->addView(sectionB, false);
    sectionB->seedFaces({rectangle(-2, -4, 2, 4)});
    ASSERT_TRUE(sectionB->hasCompletedSectionFaces());
    ASSERT_FALSE(sectionB->waitingForResult());
    ASSERT_FALSE(page->KeepUpdated.getValue());
    ASSERT_FALSE(detail->keepUpdated());

    detail->BaseView.setValue(sectionB);
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    detail->requestPaint();
    ASSERT_TRUE(detail->recomputeFeature());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->overrideKeepUpdated());

    // Coverage becomes publishable only after accepting a frame from the new source.
    detail->replaceCoverage({std::make_shared<TechDraw::Face>(rectangle(-6, -4, 6, 4))});
    detail->seedAcceptedFrame(sectionB->getSectionFaces(), 10.0);
    const auto updated = detail->getInheritedSectionFaces();
    expectArea(updated, 32.0);
    expectSameFaces(updated, detail->getInheritedSectionFaces());
}

TEST_F(DrawViewDetailHatchingTest, mattingShapeAndVisibilityArePartOfTheCacheKey)
{
    seed(rectangle(-5, -5, 5, 5), 2.0);
    const auto square = detail->getInheritedSectionFaces();
    expectArea(square, 16.0);
    preferences.setInt("Decorations", "MattingStyle", 0);
    const auto circle = detail->getInheritedSectionFaces();
    expectArea(circle, 4 * std::numbers::pi);
    ASSERT_EQ(square.size(), 1);
    ASSERT_EQ(circle.size(), 1);
    EXPECT_FALSE(square.front().IsSame(circle.front()));
    expectSameFaces(circle, detail->getInheritedSectionFaces());

    detail->ShowMatting.setValue(false);
    expectArea(detail->getInheritedSectionFaces(), 4 * 1.01 * 1.01 * std::numbers::pi);
    detail->ShowMatting.setValue(true);
    expectArea(detail->getInheritedSectionFaces(), 4 * std::numbers::pi);
}

using Segment = std::array<double, 6>;

std::vector<Segment> segments(std::vector<TechDraw::LineSet> lines)
{
    std::vector<Segment> result;
    for (auto& line : lines) {
        for (const auto& edge : line.getEdges()) {
            BRepAdaptor_Curve curve(edge);
            const auto start = curve.Value(curve.FirstParameter());
            const auto end = curve.Value(curve.LastParameter());
            std::array<double, 3> first {start.X(), start.Y(), start.Z()};
            std::array<double, 3> last {end.X(), end.Y(), end.Z()};
            if (last < first) {
                std::swap(first, last);
            }
            result.push_back({first[0], first[1], first[2], last[0], last[1], last[2]});
        }
    }
    std::sort(result.begin(), result.end());
    return result;
}

TEST_F(DrawViewDetailHatchingTest, sectionPatFaceOverloadMatchesIndexAndPreservesThinHoleOffPlane)
{
    const auto outer = rectangle(-9, -1, 9, 1, 5);
    const gp_Elips ellipse(gp_Ax2(gp_Pnt(0, 0, 5), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)), 8, 0.02);
    const auto hole = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(ellipse).Edge()).Wire();
    BRepBuilderAPI_MakeFace builder(BRepTools::OuterWire(outer), true);
    builder.Add(TopoDS::Wire(hole.Reversed()));
    const auto face = builder.Face();
    section->seedFaces({face});
    section->seedPattern("0,0,0,0,0.1");
    const auto indexed = segments(section->getDrawableLines(0));
    const auto explicitFace = segments(section->getDrawableLines(face));
    ASSERT_FALSE(indexed.empty());
    ASSERT_EQ(indexed.size(), explicitFace.size());
    for (std::size_t i = 0; i < indexed.size(); ++i) {
        for (std::size_t j = 0; j < indexed[i].size(); ++j) {
            EXPECT_NEAR(indexed[i][j], explicitFace[i][j], tolerance);
        }
    }
    double centerLength = 0.0;
    int centerSegments = 0;
    for (const auto& segment : explicitFace) {
        EXPECT_NEAR(segment[2], 0.0, tolerance);
        EXPECT_NEAR(segment[5], 0.0, tolerance);
        if (std::abs(segment[1]) < tolerance && std::abs(segment[4]) < tolerance) {
            ++centerSegments;
            centerLength += segment[3] - segment[0];
            EXPECT_TRUE(segment[3] <= -8 + tolerance || segment[0] >= 8 - tolerance);
        }
    }
    EXPECT_EQ(centerSegments, 2);
    EXPECT_NEAR(centerLength, 2.0, tolerance);
    EXPECT_TRUE(section->getDrawableLines(TopoDS_Face()).empty());
    EXPECT_TRUE(section->getDrawableLines(1).empty());
}

TEST_F(DrawViewDetailHatchingTest, inheritedPatUsesLiveSectionStyleWithoutRebuildingCachedFaces)
{
    seed(rectangle(-4, -2, 4, 2), 5.0);
    const auto inherited = detail->getInheritedSectionFaces();
    ASSERT_EQ(inherited.size(), 1);
    const auto horizontal = segments(section->getDrawableLines(inherited.front()));
    ASSERT_FALSE(horizontal.empty());
    for (const auto& line : horizontal) {
        EXPECT_NEAR(line[1], line[4], tolerance);
    }

    section->HatchScale.setValue(2.0);
    section->HatchRotation.setValue(90.0);
    section->HatchOffset.setValue(0.25, 0.5, 0);
    const auto vertical = segments(section->getDrawableLines(inherited.front()));
    ASSERT_FALSE(vertical.empty());
    for (const auto& line : vertical) {
        EXPECT_NEAR(line[0], line[3], tolerance);
        const double repeat = (line[0] - 0.25) / 2.0;
        EXPECT_NEAR(repeat, std::round(repeat), tolerance);
    }
    expectSameFaces(inherited, detail->getInheritedSectionFaces());
}

TEST_F(DrawViewDetailHatchingTest, headlessDocumentPipelinePublishesCompletedInheritedSectionFaces)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline());
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    EXPECT_TRUE(detail->capturedSection().IsSame(section->getSectionFaces()));
    EXPECT_DOUBLE_EQ(detail->capturedRadius(), 6.0);
    const auto inherited = detail->getInheritedSectionFaces();
    expectArea(inherited, 144.0);
    ASSERT_FALSE(inherited.empty());
    expectSameFaces(inherited, detail->getInheritedSectionFaces());
    const auto selectable = detail->getFaceGeometry();
    ASSERT_FALSE(selectable.empty());
    for (const auto& face : selectable) {
        EXPECT_NE(face->getRepresentation(), TechDraw::FaceRepresentation::Hollow);
    }
    EXPECT_EQ(detail->getFaceGeometry(), selectable);
}

TEST_F(DrawViewDetailHatchingTest, emptySectionCutClearsOrdinaryProjectionAndCancelsPendingDetail)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline());
    ASSERT_FALSE(section->getEdgeGeometry().empty());
    ASSERT_FALSE(section->getFaceGeometry().empty());
    ASSERT_TRUE(section->getBoundingBox().IsValid());
    ASSERT_FALSE(detail->getInheritedSectionFaces().empty());
    ASSERT_TRUE(detail->cacheValid());

    section->seedEmptyCut();
    section->overrideKeepUpdated(true);
    ASSERT_TRUE(section->waitingForCut());
    ASSERT_FALSE(section->hasCompletedSectionFaces());
    page->forceRedraw(true);
    ASSERT_TRUE(detail->recomputeFeature());
    page->forceRedraw(false);
    ASSERT_FALSE(page->KeepUpdated.getValue());
    ASSERT_TRUE(detail->pending());
    ASSERT_TRUE(detail->waitingForResult());
    ASSERT_TRUE(detail->overrideKeepUpdated());
    const int deferredExecutions = detail->executeCalls;

    // Exercise the void-cut branch and its dependent notification, not a manual cleanup.
    ASSERT_NO_THROW(section->onSectionCutFinished());
    EXPECT_FALSE(section->waitingForCut());
    EXPECT_FALSE(section->waitingForHlr());
    EXPECT_FALSE(section->waitingForFaces());
    EXPECT_FALSE(section->waitingForResult());
    EXPECT_FALSE(section->overrideKeepUpdated());
    EXPECT_TRUE(section->hasCompletedSectionFaces());
    EXPECT_TRUE(section->getShapeForDetail().IsNull());
    EXPECT_TRUE(section->getSectionFaces().IsNull());
    EXPECT_TRUE(section->getTDFaceGeometry().empty());
    EXPECT_TRUE(section->getEdgeGeometry().empty());
    EXPECT_TRUE(section->getFaceGeometry().empty());
    EXPECT_FALSE(section->getBoundingBox().IsValid());

    // Only inherited state is in scope for the detail, not its historical display retention.
    EXPECT_GT(detail->executeCalls, deferredExecutions);
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    EXPECT_FALSE(detail->facesReady());
    EXPECT_TRUE(detail->capturedSection().IsNull());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_FALSE(detail->cacheValid());
}

TEST_F(DrawViewDetailHatchingTest, detailRedrawUsesFrozenSectionFaceScaleAndRotationSnapshots)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline(45.0));
    const auto sectionFaces = section->getSectionFaces();
    ASSERT_DOUBLE_EQ(section->getSectionFaceScale(), 1.5);
    ASSERT_DOUBLE_EQ(section->getSectionFaceRotation(), 45.0);
    ASSERT_FALSE(page->KeepUpdated.getValue());

    section->Rotation.setValue(90.0);
    detail->AnchorPoint.setValue(9, 9, 0);
    for (const double scale : {1.5, 6.0}) {
        SCOPED_TRACE(scale);
        section->Scale.setValue(scale);
        ASSERT_TRUE(section->recomputeFeature());
        ASSERT_TRUE(sectionFaces.IsSame(section->getSectionFaces()));
        EXPECT_DOUBLE_EQ(section->getSectionFaceScale(), 1.5);
        EXPECT_DOUBLE_EQ(section->getSectionFaceRotation(), 45.0);

        // Accept only the detail update; the section's displayed faces stay frozen.
        detail->overrideKeepUpdated(true);
        ASSERT_TRUE(detail->recomputeFeature());
        ASSERT_TRUE(detail->faceExtractionSucceeded());
        EXPECT_TRUE(detail->facesReady());
        EXPECT_FALSE(detail->waitingForResult());
        EXPECT_FALSE(detail->overrideKeepUpdated());
        EXPECT_TRUE(sectionFaces.IsSame(detail->capturedSection()));
        const auto inherited = detail->getInheritedSectionFaces();
        // The 90-degree square source ends at (10, 10). The matted crop starts
        // at (6, 6), so a 4 x 4 region remains, enlarged by the detail scale of 2.
        expectArea(inherited, 64.0);
        EXPECT_TRUE(detail->cacheValid());
        expectSameFaces(inherited, detail->getInheritedSectionFaces());
    }
}

TEST_F(DrawViewDetailHatchingTest, rotatedSquareUnmattingUsesTheAcceptedCropWithoutRebuildingGeometry)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline(0.0, 45.0));
    const auto coverage = detail->getFaceGeometry();
    const double cropRadius = 6.0 * 1.01;
    const double cornerLeg = 12.0 - std::sqrt(2.0) * cropRadius;
    const double mattedArea = 144.0 - 2.0 * cornerLeg * cornerLeg;
    expectArea(detail->getInheritedSectionFaces(), mattedArea);

    // A frozen property edit must not change the orientation of the accepted crop.
    detail->Rotation.setValue(15.0);
    ASSERT_TRUE(detail->recomputeFeature());
    const int executions = detail->executeCalls;
    detail->ShowMatting.setValue(false);
    const auto unmatted = detail->getInheritedSectionFaces();
    expectArea(unmatted, 4.0 * cropRadius * cropRadius);
    EXPECT_TRUE(detail->cacheValid());
    expectSameFaces(unmatted, detail->getInheritedSectionFaces());
    detail->requestPaint();
    expectSameFaces(unmatted, detail->getInheritedSectionFaces());

    detail->ShowMatting.setValue(true);
    expectArea(detail->getInheritedSectionFaces(), mattedArea);
    EXPECT_EQ(detail->getFaceGeometry(), coverage);
    EXPECT_EQ(detail->executeCalls, executions);
    EXPECT_FALSE(detail->overrideKeepUpdated());
}

TEST_F(DrawViewDetailHatchingTest, cropOutsideSourceDoesNotPublishOrCacheAndLaterValidCropRecovers)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline());
    const auto previous = detail->getInheritedSectionFaces();
    expectArea(previous, 144.0);
    ASSERT_TRUE(detail->cacheValid());

    detail->AnchorPoint.setValue(100, 100, 0);
    detail->overrideKeepUpdated(true);
    ASSERT_TRUE(detail->recomputeFeature());
    EXPECT_FALSE(detail->facesReady());
    EXPECT_FALSE(detail->cacheValid());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    detail->requestPaint();
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    EXPECT_FALSE(detail->cacheValid());
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());

    detail->AnchorPoint.setValue(2, 1, 0);
    detail->overrideKeepUpdated(true);
    ASSERT_TRUE(detail->recomputeFeature());
    EXPECT_TRUE(detail->faceExtractionSucceeded());
    EXPECT_TRUE(detail->facesReady());
    const auto recovered = detail->getInheritedSectionFaces();
    expectArea(recovered, 144.0);
    EXPECT_TRUE(detail->cacheValid());
    expectSameFaces(recovered, detail->getInheritedSectionFaces());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());
}

TEST_F(DrawViewDetailHatchingTest, acceptedFrozenRedrawRemainsPendingUntilSectionCompletion)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline());
    const auto previous = detail->getInheritedSectionFaces();
    ASSERT_FALSE(previous.empty());
    section->setFacesReady(false);
    detail->Radius.setValue(2.0);

    page->forceRedraw(true);
    ASSERT_TRUE(detail->recomputeFeature());
    page->forceRedraw(false);
    ASSERT_FALSE(page->KeepUpdated.getValue());
    ASSERT_TRUE(detail->pending());
    EXPECT_TRUE(detail->overrideKeepUpdated());
    EXPECT_TRUE(detail->waitingForResult());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
    const int deferredExecutions = detail->executeCalls;
    detail->onBaseSectionReady();
    EXPECT_EQ(detail->executeCalls, deferredExecutions);

    // Use the actual section notification rather than manually recomputing the detail.
    section->completeFaces();
    EXPECT_GT(detail->executeCalls, deferredExecutions);
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    EXPECT_FALSE(detail->keepUpdated());
    EXPECT_TRUE(detail->facesReady());
    EXPECT_DOUBLE_EQ(detail->capturedRadius(), 4.0);
    const auto updated = detail->getInheritedSectionFaces();
    expectArea(updated, 64.0);
    ASSERT_FALSE(updated.empty());
    EXPECT_FALSE(previous.front().IsSame(updated.front()));
    const int completedExecutions = detail->executeCalls;
    section->completeFaces();
    EXPECT_EQ(detail->executeCalls, completedExecutions);
    expectSameFaces(updated, detail->getInheritedSectionFaces());
}

TEST_F(DrawViewDetailHatchingTest, clearingBaseViewCancelsPendingFrozenRedrawAndItsOverride)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline());
    ASSERT_FALSE(detail->getInheritedSectionFaces().empty());
    section->setFacesReady(false);

    page->forceRedraw(true);
    ASSERT_TRUE(detail->recomputeFeature());
    page->forceRedraw(false);
    ASSERT_FALSE(page->KeepUpdated.getValue());
    ASSERT_TRUE(detail->pending());
    ASSERT_TRUE(detail->waitingForResult());
    ASSERT_TRUE(detail->overrideKeepUpdated());

    detail->BaseView.setValue(nullptr);
    ASSERT_TRUE(detail->recomputeFeature());
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    EXPECT_FALSE(detail->keepUpdated());
    EXPECT_FALSE(detail->facesReady());
    EXPECT_FALSE(detail->cacheValid());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());

    const int cancelledExecutions = detail->executeCalls;
    section->completeFaces();
    EXPECT_EQ(detail->executeCalls, cancelledExecutions);
    // Also simulate a completion callback already queued before the link was cleared.
    detail->onBaseSectionReady();
    EXPECT_EQ(detail->executeCalls, cancelledExecutions);
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    EXPECT_TRUE(detail->getInheritedSectionFaces().empty());
}

TEST_F(DrawViewDetailHatchingTest, pendingFrozenRedrawWaitsForBothParentAndOwnBusyStages)
{
    ASSERT_NO_FATAL_FAILURE(buildBoxPipeline());
    section->setFacesReady(false);
    section->waitingForCut(true);
    detail->waitingForDetail(true);
    detail->waitingForHlr(true);
    detail->waitingForFaces(true);
    detail->overrideKeepUpdated(true);
    ASSERT_TRUE(detail->recomputeFeature());
    ASSERT_TRUE(detail->pending());
    const int deferredExecutions = detail->executeCalls;

    section->completeFaces();
    EXPECT_EQ(detail->executeCalls, deferredExecutions);
    section->waitingForCut(false);
    section->completeFaces();
    EXPECT_EQ(detail->executeCalls, deferredExecutions);
    detail->waitingForDetail(false);
    detail->onBaseSectionReady();
    EXPECT_EQ(detail->executeCalls, deferredExecutions);
    detail->waitingForHlr(false);
    detail->onBaseSectionReady();
    EXPECT_EQ(detail->executeCalls, deferredExecutions);
    EXPECT_TRUE(detail->overrideKeepUpdated());

    // The normal face-finished path must preserve the pending request while
    // finalizing the older update, then resume it on the frozen page.
    detail->onFacesFinished();
    EXPECT_GT(detail->executeCalls, deferredExecutions);
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->waitingForResult());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    expectArea(detail->getInheritedSectionFaces(), 144.0);
}

TEST_F(DrawViewDetailHatchingTest, acceptedFaceFinalizationAddsCenterlinesBeforeRecomputingDimensions)
{
    seed();
    ASSERT_EQ(detail->getFaceGeometry().size(), 1);
    ASSERT_FALSE(detail->getFaceEdgesByIndex(0).empty());
    // TechDraw subelement names use zero-based indices, including builder references.
    TechDraw::CenterLine* centerLine = nullptr;
    ASSERT_NO_THROW(centerLine = TechDraw::CenterLine::CenterLineBuilder(detail, {"Face0"}));
    ASSERT_NE(centerLine, nullptr);
    detail->addCenterLine(centerLine);
    ASSERT_EQ(centerLineCount(*detail), 0);

    auto ownedDimension = std::make_unique<ObservedDimension>();
    document->addObject(ownedDimension.get(), "Dimension");
    auto* dimension = ownedDimension.release();
    ASSERT_NO_THROW(page->addView(dimension, false));
    dimension->Type.setValue("Distance");
    dimension->MeasureType.setValue("Projected");
    dimension->References2D.setValues({detail}, {"Edge0"});
    dimension->setLinearPoints(Base::Vector3d(), Base::Vector3d());
    const auto reference = detail->getGeomByIndex(0);
    ASSERT_NE(reference, nullptr);
    const double expectedLength = (reference->getEndPoint() - reference->getStartPoint()).Length();
    ASSERT_GT(expectedLength, 0.0);
    ASSERT_FALSE(page->KeepUpdated.getValue());
    ASSERT_FALSE(dimension->keepUpdated());
    const int previousExecutions = dimension->executeCalls;

    detail->beginAcceptedUpdate();
    ASSERT_NO_THROW(detail->postHlrTasks());
    EXPECT_TRUE(detail->updateActive());
    EXPECT_TRUE(detail->overrideKeepUpdated());
    EXPECT_FALSE(detail->facesReady());
    EXPECT_EQ(centerLineCount(*detail), 0);
    EXPECT_EQ(dimension->executeCalls, previousExecutions);
    ASSERT_NO_THROW(detail->completeFaceExtraction(true));

    EXPECT_EQ(centerLineCount(*detail), 1);
    EXPECT_GT(dimension->executeCalls, previousExecutions);
    EXPECT_TRUE(dimension->wasAuthorized);
    EXPECT_EQ(dimension->centerLinesAtExecute, 1);
    const auto points = dimension->getLinearPoints();
    EXPECT_NEAR((points.second() - points.first()).Length(), expectedLength, tolerance);
    EXPECT_FALSE(dimension->overrideKeepUpdated());
    EXPECT_TRUE(detail->facesReady());
    EXPECT_FALSE(detail->updateActive());
    EXPECT_FALSE(detail->pending());
    EXPECT_FALSE(detail->overrideKeepUpdated());
    expectArea(detail->getInheritedSectionFaces(), 96.0);
}

}  // namespace

// NOLINTEND(readability-magic-numbers,cppcoreguidelines-avoid-magic-numbers)
