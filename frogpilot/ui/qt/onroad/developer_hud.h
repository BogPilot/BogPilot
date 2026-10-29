/**
The MIT License

Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

BogPilot developer HUD. Metric choice, formulas and color thresholds follow
sunnypilot's Qt "Developer UI" (selfdrive/ui/sunnypilot/qt/onroad/developer_ui/
developer_ui.cc, sunnypilot branch master-dev-c3). Layout, angle-control
substitutes and the Qt painting code are BogPilot's own.
***/
#pragma once

#include <QColor>
#include <QPainter>
#include <QPainterPath>
#include <QRect>
#include <QString>
#include <QVector>

// File-backed toggle (the prebuilt params_pyx.so cannot store new Params keys).
// "1", "true" or "on" in this file turns the HUD on. Absent or anything else: off.
const char DEVELOPER_HUD_PATH[] = "/data/params_bogpilot/DeveloperHUD";

bool developerHudFileEnabled(const QString &path = DEVELOPER_HUD_PATH);
bool setDeveloperHudFileEnabled(bool enabled, const QString &path = DEVELOPER_HUD_PATH);

struct DeveloperHudInputs {
  bool is_metric = false;

  float a_ego = 0.0f;               // carState.aEgo, m/s^2
  float v_ego = 0.0f;               // carState.vEgo, m/s
  float steering_angle_deg = 0.0f;  // carState.steeringAngleDeg
  bool steering_pressed = false;    // carState.steeringPressed
  bool lat_active = false;          // carControl.latActive

  bool lead_status = false;         // radarState.leadOne.status
  float lead_d_rel = 0.0f;          // radarState.leadOne.dRel, m
  float lead_v_rel = 0.0f;          // radarState.leadOne.vRel, m/s

  float curvature = 0.0f;           // controlsState.curvature, 1/m
  float desired_curvature = 0.0f;   // controlsState.desiredCurvature, 1/m
  float roll = 0.0f;                // liveParameters.roll, rad

  bool torque_control = false;      // controlsState.lateralControlState is torqueState
  float steer_angle_desired = 0.0f; // angleState/pidState.steeringAngleDesiredDeg
  float friction = 0.0f;            // liveTorqueParameters.frictionCoefficientFiltered
  bool friction_live = false;       // liveTorqueParameters.liveValid

  bool gps_valid = false;           // gpsLocationExternal / gpsLocation alive and fixed
  double altitude = 0.0;            // gps altitude, m

  int memory_usage_percent = 0;     // deviceState.memoryUsagePercent
};

struct DeveloperHudCell {
  QString value;
  QString label;
  QString unit;
  QColor color;
};

QVector<DeveloperHudCell> developerHudCells(const DeveloperHudInputs &in);

// Panel size at scale 1.0 (2 columns x 5 rows).
QSize developerHudSize();

// Panel rectangle with its bottom-right corner at bottomRight, scaled (0.6 to 1.0) to fit maxHeight,
// and down to 0.5 if needed to fit maxWidth.
QRect developerHudRect(const QPoint &bottomRight, int maxHeight, int maxWidth);

// Paints the panel into a rectangle from developerHudRect().
void paintDeveloperHud(QPainter &p, const QRect &panel, const DeveloperHudInputs &in);

// BogPilot redesign style (approved mockup): dark translucent panel, muted small labels above white values,
// small units after the value. Same cells, colours and data as paintDeveloperHud().
QSize developerHudSizeV2();
// Panel with its bottom-right corner at bottomRight, scaled down (to 0.5) only if maxWidth is narrower than the panel.
QRect developerHudRectV2(const QPoint &bottomRight, int maxWidth);
void paintDeveloperHudV2(QPainter &p, const QRect &panel, const DeveloperHudInputs &in);
