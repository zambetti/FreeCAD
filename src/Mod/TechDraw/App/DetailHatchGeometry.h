// SPDX-License-Identifier: LGPL-2.1-or-later

#pragma once

#include <vector>

#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <gp_Trsf.hxx>

#include <Mod/TechDraw/TechDrawGlobal.h>

namespace TechDraw
{

class TechDrawExport DetailHatchGeometry
{
public:
    /**
     * Return (union(transformed section faces) intersect union(detailFaces)
     * intersect clipFace) minus union(explicitly excluded detail faces).
     *
     * Only sectionFaces is transformed by toDetail; the other faces must already
     * be in detail coordinates. Association is geometric, never by face number.
     * excludedFaces contains zero-based indices into detailFaces. Null entries
     * (e.g. Hollow faces) retain their indices but provide no coverage. Invalid
     * exclusion indices are ignored.
     *
     * Inputs are not modified. OCC curves, inner wires and disconnected regions
     * are retained without conversion to display geometry or enlarged tolerances.
     * Null/empty inputs and intersections without faces return an empty vector.
     * A failed transform or a boolean reporting errors/warnings throws
     * Standard_Failure rather than returning a potentially partial result.
     */
    static std::vector<TopoDS_Face> intersect(
        const TopoDS_Shape& sectionFaces,
        const gp_Trsf& toDetail,
        const std::vector<TopoDS_Face>& detailFaces,
        const TopoDS_Face& clipFace,
        const std::vector<int>& excludedFaces
    );
};

}  // namespace TechDraw
