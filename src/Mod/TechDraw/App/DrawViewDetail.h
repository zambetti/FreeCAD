// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2016 WandererFan <wandererfan@gmail.com>                *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/

#pragma once

#include <gp_Ax2.hxx>
#include <gp_Trsf.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>

#include <App/DocumentObject.h>
#include <App/FeaturePython.h>
#include <App/PropertyLinks.h>
#include <Mod/TechDraw/TechDrawGlobal.h>

#include "DrawViewPart.h"


class gp_Pln;
class gp_Ax2;
class TopoDS_Face;

namespace TechDraw
{
class Face;
}

namespace TechDraw
{


class TechDrawExport DrawViewDetail : public DrawViewPart
{
    PROPERTY_HEADER_WITH_OVERRIDE(TechDraw::DrawViewDetail);

public:
    /// Constructor
    DrawViewDetail();
    ~DrawViewDetail() override;

    App::PropertyLink   BaseView;
    App::PropertyVector AnchorPoint;
    App::PropertyFloat   Radius;
    App::PropertyString Reference;

    App::PropertyBool   ShowMatting;
    App::PropertyBool   ShowHighlight;

    short mustExecute() const override;
    App::DocumentObjectExecReturn *execute() override;
    void onChanged(const App::Property* prop) override;
    const char* getViewProviderName() const override {
        return "TechDrawGui::ViewProviderViewPart";
    }
    void unsetupObject() override;
    void handleChangedPropertyType(
        Base::XMLReader &reader, const char * TypeName, App::Property * prop) override;

    std::string makeCaption();

    void detailExec(TopoDS_Shape& s,
                    DrawViewPart* baseView,
                    DrawViewSection* sectionAlias);
    void makeDetailShape(const TopoDS_Shape& shape,
                         DrawViewPart* dvp,
                         DrawViewSection* dvs);
    void postHlrTasks(void) override;
    void postFaceExtractionTasks() override;
    void waitingForDetail(bool s) { m_waitingForDetail = s; }
    bool waitingForDetail(void) const { return m_waitingForDetail; }
    bool waitingForResult() const override;

    double getFudgeRadius(void);
    TopoDS_Shape projectEdgesOntoFace(TopoDS_Shape& edgeShape,
                                      TopoDS_Face& projFace,
                                      gp_Dir& projDir);

    std::vector<DrawViewDetail*> getDetailRefs() const override;
    TopoDS_Shape getDetailShape() const { return m_detailShape; }

    Base::Vector3d mapPoint3dToDetail(const Base::Vector3d& inPoint) const;

    // Residual OCC cut faces, in the same paper coordinates as section cut faces.
    std::vector<TopoDS_Face> getInheritedSectionFaces(
        const std::vector<int>& excludedDetailFaceIndices = {}) const;
    void onBaseSectionReady();

public Q_SLOTS:
    void onMakeDetailFinished(void);

protected:
    TopoDS_Shape getShapeForGeometryBuild() const override;

    void getParameters(void);
    double m_fudge;
    bool debugDetail() const;

    TopoDS_Shape m_scaledShape;
    gp_Ax2 m_viewAxis;

    QMetaObject::Connection connectDetailWatcher;
    QFutureWatcher<void> m_detailWatcher;
    QFuture<void> m_detailFuture;
    bool m_waitingForDetail;
    bool m_scaleAdded = false;
    bool m_refAdded = false;

    DrawViewPart* m_saveDvp;
    DrawViewSection* m_saveDvs;

    TopoDS_Shape m_detailShape;

    // Capture the frame of an accepted geometry update, not later property edits
    // on a frozen page. Only publish/cache coverage after face extraction finishes.
    DrawViewSection* m_hatchSource = nullptr; // Identity only; BaseView owns the dependency.
    TopoDS_Shape m_hatchSectionFaces;
    gp_Trsf m_hatchTransform;
    double m_hatchRadius = 0.0;
    struct DetailParameters {
        Base::Vector3d direction;
        Base::Vector3d anchor;
        gp_Ax2 viewAxis;
        double radius = 0.0;
        double scale = 1.0;
        double rotation = 0.0;
        bool square = false;
    } m_detailParameters;
    bool m_detailShapeReady = false;
    bool m_inheritedFacesReady = false;
    bool m_pendingRecompute = false;
    bool m_detailUpdateActive = false;
    void finishDetailUpdate();
    void cancelInheritedUpdate();

    mutable bool m_hatchCacheValid = false;
    mutable bool m_cachedMatting = false;
    mutable bool m_cachedSquareClip = false;
    mutable std::vector<FacePtr> m_cachedDetailFaces;
    mutable std::vector<bool> m_cachedHollowFaces;
    mutable std::vector<int> m_cachedExcludedFaces;
    mutable std::vector<TopoDS_Face> m_cachedInheritedFaces;
};

using DrawViewDetailPython = App::FeaturePythonT<DrawViewDetail>;

} //namespace TechDraw