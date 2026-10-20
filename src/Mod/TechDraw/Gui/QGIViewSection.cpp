// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2013 Luke Parry <l.parry@warwick.ac.uk>                 *
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

#include <Mod/TechDraw/App/DrawViewSection.h>

#include "QGIViewSection.h"
#include "ViewProviderViewSection.h"


using namespace TechDrawGui;

void QGIViewSection::draw()
{
    if (!isVisible()) {
        return;
    }

    QGIViewPart::draw();
    drawSectionFace();
}

void QGIViewSection::drawSectionFace()
{
    auto section( dynamic_cast<TechDraw::DrawViewSection *>(getViewObject()) );
    if (!section) {
        return;
    }

    if (!section->hasGeometry() || section->CutSurfaceDisplay.isValue("Hide")) {
        return;
    }

    ViewProviderViewSection* sectionVp = freecad_cast<ViewProviderViewSection*>(QGIView::getViewProvider(section));
    if (!sectionVp) {
        return;
    }

    auto sectionFaces( section->getTDFaceGeometry() );
    if (sectionFaces.empty()) {
        return;
    }

    int i = 0;
    for (const auto& face : sectionFaces) {
        drawSectionStyledFace(face, section->getSectionTopoDSFace(i), *section, *sectionVp);
        ++i;
    }
}

void QGIViewSection::updateView(bool update)
{
    Q_UNUSED(update);
    auto viewPart( dynamic_cast<TechDraw::DrawViewSection *>(getViewObject()) );
    if (!viewPart)
        return;
    draw();
    QGIView::updateView(update);
}
