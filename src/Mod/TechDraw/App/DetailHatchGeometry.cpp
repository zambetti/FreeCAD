// SPDX-License-Identifier: LGPL-2.1-or-later

#include "DetailHatchGeometry.h"

#include <cstddef>
#include <sstream>

#include <BOPAlgo_Operation.hxx>
#include <BRepAlgoAPI_BooleanOperation.hxx>
#include <BRepBndLib.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <Bnd_Box.hxx>
#include <Standard_Failure.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopTools_ListOfShape.hxx>

namespace
{

bool hasFaces(const TopoDS_Shape& shape)
{
    return !shape.IsNull() && TopExp_Explorer(shape, TopAbs_FACE).More();
}

void addCandidate(
    const TopoDS_Face& face,
    const Bnd_Box& clipBounds,
    TopTools_IndexedMapOfShape& candidates
)
{
    if (face.IsNull()) {
        return;
    }
    Bnd_Box bounds;
    BRepBndLib::Add(face, bounds, false);
    if (!bounds.IsOut(clipBounds)) {
        candidates.Add(face);
    }
}

TopoDS_Shape booleanOperation(
    BOPAlgo_Operation operation,
    const TopTools_ListOfShape& arguments,
    const TopTools_ListOfShape& tools,
    const char* context
)
{
    BRepAlgoAPI_BooleanOperation builder;
    builder.SetOperation(operation);
    builder.SetArguments(arguments);
    builder.SetTools(tools);
    builder.SetNonDestructive(true);
    builder.Build();

    // Warnings can describe skipped edges or faces: do not return a partial
    // result that the caller could mistake for completed, cacheable geometry.
    if (!builder.IsDone() || builder.HasErrors() || builder.HasWarnings()) {
        std::ostringstream message;
        message << "DetailHatchGeometry: " << context << " failed.\n";
        builder.DumpErrors(message);
        builder.DumpWarnings(message);
        throw Standard_Failure(message.str().c_str());
    }
    if (builder.Shape().IsNull()) {
        throw Standard_Failure("DetailHatchGeometry: boolean returned a null shape");
    }
    return builder.Shape();
}

TopoDS_Shape booleanOperation(
    BOPAlgo_Operation operation,
    const TopoDS_Shape& argument,
    const TopoDS_Shape& tool,
    const char* context
)
{
    TopTools_ListOfShape arguments;
    TopTools_ListOfShape tools;
    arguments.Append(argument);
    tools.Append(tool);
    return booleanOperation(operation, arguments, tools, context);
}

TopoDS_Shape unionFaces(const TopTools_IndexedMapOfShape& faces, const char* context)
{
    if (faces.IsEmpty()) {
        return {};
    }
    if (faces.Extent() == 1) {
        return faces(1);
    }

    // Supply individual faces, not an overlapping compound masquerading as a
    // union. Fuse partitions overlapping coverage into non-overlapping faces.
    TopTools_ListOfShape arguments;
    TopTools_ListOfShape tools;
    arguments.Append(faces(1));
    for (int i = 2; i <= faces.Extent(); ++i) {
        tools.Append(faces(i));
    }
    return booleanOperation(BOPAlgo_FUSE, arguments, tools, context);
}

}  // namespace

std::vector<TopoDS_Face> TechDraw::DetailHatchGeometry::intersect(
    const TopoDS_Shape& sectionFaces,
    const gp_Trsf& toDetail,
    const std::vector<TopoDS_Face>& detailFaces,
    const TopoDS_Face& clipFace,
    const std::vector<int>& excludedFaces
)
{
    if (clipFace.IsNull() || detailFaces.empty() || !hasFaces(sectionFaces)) {
        return {};
    }

    Bnd_Box clipBounds;
    BRepBndLib::Add(clipFace, clipBounds, false);
    TopTools_IndexedMapOfShape coverage;
    for (const auto& face : detailFaces) {
        addCandidate(face, clipBounds, coverage);
    }
    if (coverage.IsEmpty()) {
        return {};
    }

    BRepBuilderAPI_Transform transform(sectionFaces, toDetail, true);
    if (!transform.IsDone() || transform.Shape().IsNull()) {
        throw Standard_Failure("DetailHatchGeometry: section transform failed");
    }
    TopTools_IndexedMapOfShape section;
    for (TopExp_Explorer face(transform.Shape(), TopAbs_FACE); face.More(); face.Next()) {
        addCandidate(TopoDS::Face(face.Current()), clipBounds, section);
    }
    if (section.IsEmpty()) {
        return {};
    }

    TopoDS_Shape result = booleanOperation(
        BOPAlgo_COMMON,
        unionFaces(section, "section union"),
        clipFace,
        "clip intersection"
    );
    if (!hasFaces(result)) {
        return {};
    }
    result = booleanOperation(
        BOPAlgo_COMMON,
        result,
        unionFaces(coverage, "detail coverage union"),
        "detail coverage intersection"
    );
    if (!hasFaces(result)) {
        return {};
    }

    // Exclusions subtract geometry even where another (included) detail face
    // overlaps it. Removing excluded entries from coverage would not do that.
    TopTools_IndexedMapOfShape excluded;
    for (int index : excludedFaces) {
        if (index >= 0 && static_cast<std::size_t>(index) < detailFaces.size()) {
            const auto& face = detailFaces[static_cast<std::size_t>(index)];
            if (!face.IsNull() && coverage.Contains(face)) {
                excluded.Add(face);
            }
        }
    }
    if (!excluded.IsEmpty()) {
        result = booleanOperation(
            BOPAlgo_CUT,
            result,
            unionFaces(excluded, "exclusion union"),
            "explicit hatch subtraction"
        );
    }

    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(result, TopAbs_FACE, faces);
    std::vector<TopoDS_Face> output;
    output.reserve(faces.Extent());
    for (int i = 1; i <= faces.Extent(); ++i) {
        output.push_back(TopoDS::Face(faces(i)));
    }
    return output;
}
