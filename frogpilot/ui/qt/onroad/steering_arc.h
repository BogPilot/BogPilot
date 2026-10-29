/**
The MIT License

Copyright (c) 2018, Comma.ai, Inc.

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

BogPilot steering arc: a QPainter port of upstream openpilot's comma four torque bar
(selfdrive/ui/mici/onroad/torque_bar.py, MIT): rounded thick-arc polygon (arc_bar_pts), first-order
filtered lateral utilisation, thickness/offset growth past 50 %, centre-to-65 % gradient that blends to
yellow/orange past 75 %, and the grey centre dot below 50 %. BogPilot changes: arc "B" geometry for the
2160 px screen (1800 px radius, 17 degree span, raised 112 px so it clears the road-name pill), purple in
Experimental Mode, stays visible (dimmed) while disengaged because it is also the Experimental Mode tap
target, the fill grows toward the turn direction (left turn fills left), and the driver's own steering is
shown in soft cyan while disengaged or overriding, with a white tick at openpilot's target during an override.
***/
#pragma once

#include <QPainter>
#include <QPolygonF>
#include <QRect>
#include <QRectF>

class SteeringArc {
public:
  // Geometry (screen px; the 2.6x SCALE is kept for the cap radius and the centre dot)
  static constexpr float SPAN_DEG = 17.0f;          // round 3c: trimmed from 24 so the 411 px pill fits and even the shadow clears the map button
  static constexpr float SCALE = 2.6f;
  static constexpr float RADIUS = 1800.0f;          // inner radius of the bar
  static constexpr float OFFSET_LO = 112.0f;        // inner edge above the view bottom, |util| <= 0.5
  static constexpr float OFFSET_HI = 122.4f;        // ... at |util| = 1 (upstream 22 -> 26 growth, 10.4 px)
  static constexpr float THICKNESS_LO = 22.1f;      // 8.5 * SCALE
  static constexpr float THICKNESS_HI = 52.0f;      // 20 * SCALE
  static constexpr float DEFAULT_MAX_LAT_ACCEL = 3.0f;  // m/s^2

  // Driver-steering display: below HUMAN_ANGLE_SPEED_LO the fill follows the steering-wheel angle
  // (HUMAN_FULL_ANGLE_DEG = full arc), above HUMAN_ANGLE_SPEED_HI it follows lateral acceleration like openpilot's
  // value, linearly blended in between. 180 deg is about where the two agree around 7 m/s on a Model S
  // (steer ratio ~15, wheelbase ~3 m), so the hand-over is smooth. The near-limit orange only applies to the
  // lateral-acceleration part.
  static constexpr float HUMAN_FULL_ANGLE_DEG = 180.0f;
  static constexpr float HUMAN_ANGLE_SPEED_LO = 5.0f;   // m/s
  static constexpr float HUMAN_ANGLE_SPEED_HI = 9.0f;   // m/s
  static constexpr int SOURCE_HYSTERESIS_FRAMES = 6;    // 0.3 s at UI_FREQ 20 Hz

  struct Inputs {
    float op = 0.0f;              // openpilot utilisation [-1, 1], negative fills left
    float human = 0.0f;           // driver utilisation [-1, 1] (humanUtilization)
    float human_lat_weight = 1.0f;  // share of the lateral-acceleration term in `human` (scales the orange blend)
    bool lat_active = false;      // carControl.latActive
    bool override_ = false;       // UI status is STATUS_OVERRIDE (driver steering while engaged)
    bool active = false;          // openpilot engaged in any form (track at full strength)
    bool engaged_colors = false;  // white / purple OP fill; otherwise faded white like upstream
    bool experimental = false;
    float steering_angle_deg = 0.0f;  // carState.steeringAngleDeg, colours the driver arc (v5)
  };

  void update(const Inputs &in);
  void setFilteredValue(float v) { torque_ = v; }   // demos / tests
  void setOpTarget(float v) { op_target_ = v; }     // demos / tests
  void setHumanSource(bool human) { human_ = human; human_frames_ = 0; }   // demos / tests (skips hysteresis)
  float value() const { return torque_; }
  bool humanSource() const { return human_; }

  void paint(QPainter &p, const QRect &view) const;
  // Generous tap target around the arc (replaces the removed top-right wheel button)
  QRectF tapRect(const QRect &view) const;

  // Upstream utilisation for angle / curvature lateral control:
  // (curvature*v^2 - roll*g*interp(v,[5,15],[0,1]) + (desired - actual lateral accel)) / maxLateralAccel.
  // controlsState.curvature is -VM.calc_curvature(steeringAngle), so positive curvature is a RIGHT turn and a
  // left turn comes out negative, which fills to the left (same as upstream torque_bar.py, no extra negation).
  // v5 driver-arc colour by steering-wheel angle (mockup 2c, AP1 lock ~430 deg: logs -426.5 / +383.5):
  // blue to 120, blend to orange by 180, orange deepening to 360, blend to hot pink at LOCK_DEG.
  static constexpr float LOCK_DEG = 430.0f;
  static QColor humanAngleColor(float abs_angle_deg);

  static float angleUtilization(float curvature, float desired_curvature, float v_ego, float roll, float max_lat_accel);
  // Driver steering: angleUtilization(curvature, curvature, ...) (actual lateral acceleration, no OP target) blended
  // with -steeringAngleDeg / HUMAN_FULL_ANGLE_DEG at low speed (steeringAngleDeg is positive to the left).
  // lat_weight (optional) receives the lateral-acceleration share.
  static float humanUtilization(float curvature, float steering_angle_deg, float v_ego, float roll, float max_lat_accel,
                                float *lat_weight = nullptr);

  // Rounded thick-arc polygon centred on the origin (upstream arc_bar_pts), angles in degrees, y down.
  static QPolygonF arcBarPolygon(float r_mid, float thickness, float a0_deg, float a1_deg, float cap_radius);

private:
  float torque_ = 0.0f;       // filtered displayed value (OP or driver)
  float op_target_ = 0.0f;    // filtered OP value, drawn as a tick during an override
  float alpha_ = 0.0f;
  float human_orange_ = 1.0f;
  float angle_ = 0.0f;        // filtered |steering angle| for the driver-arc colour
  bool human_ = false;        // displaying the driver's steering
  int human_frames_ = 0;      // frames the requested source has differed from the shown one
  bool show_tick_ = false;
  bool engaged_colors_ = false;
  bool experimental_ = false;
};
