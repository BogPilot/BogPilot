#pragma once

// BogPilot "88 mph" flame tire tracks (Back to the Future DeLorean).
//
// When the red blind-spot (BSM) lane fill on one side turns off and the radar's adjacent lead on
// that side (frogpilotRadarState.leadLeft / leadRight) is going faster than 88 mph, two burning
// tire tracks are drawn down that adjacent lane for 3 seconds, then burn out.
//
// Display only. It does not read or change anything the car uses to drive.
//
// File-backed toggle /data/params_bogpilot/BSMFlameTracks, default ON (the prebuilt params_pyx.so
// cannot store new Params keys). Same parser as the other BogPilot file toggles
// ("1"/"true"/"on" = on, "0"/"false"/"off" = off, no file or anything else = on). Re-read every 2 s
// while driving. An explicit "0" written by the settings switch keeps them off.
//
// The trigger logic (FlameTrigger) is plain C++ with no Qt so it can be unit-tested off-device.

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace bogpilot_flames {

constexpr float MPH_TO_MS = 0.44704f;
constexpr float TRIGGER_SPEED_MS = 88.0f * MPH_TO_MS;  // 39.34 m/s; strictly faster than this
constexpr int64_t DURATION_MS = 3000;                  // flames on screen
constexpr int64_t GRACE_MS = 500;                      // lead may cross 88 mph up to 0.5 s after the fill turns off
constexpr int64_t TOGGLE_RECHECK_MS = 2000;
// Flame tongue height multiplier. 1.0 = the original tall tongues; 0.55 = low flames licking along the
// tire track. Track width, geometry and timing do not depend on it.
constexpr float FLAME_HEIGHT_SCALE = 0.55f;
constexpr const char *TOGGLE_KEY = "BSMFlameTracks";
constexpr bool TOGGLE_DEFAULT = true;  // missing/unknown file = on; must match visual_settings.cc

struct FlameSideInput {
  bool red_fill_shown = false;  // the red BSM lane fill was drawn for this side this frame
  bool lead_status = false;     // frogpilotRadarState lead on this side is valid
  float lead_v = 0.0f;          // that lead's absolute speed (vLead), m/s
};

// Edge-triggered: one burn per red-fill off edge. Index 0 = left, 1 = right.
class FlameTrigger {
public:
  void update(int64_t now_ms, bool enabled, const FlameSideInput in[2]) {
    for (int i = 0; i < 2; ++i) {
      Side &s = side[i];
      if (!enabled) {
        s = Side();
        continue;
      }
      if (s.red_was && !in[i].red_fill_shown) {
        s.arm_until_ms = now_ms + GRACE_MS;  // fill just turned off
      }
      if (in[i].red_fill_shown) {
        s.arm_until_ms = -1;                 // back in the blind spot: wait for the next off edge
      }
      s.red_was = in[i].red_fill_shown;

      const bool fast_lead = in[i].lead_status && in[i].lead_v > TRIGGER_SPEED_MS;
      if (s.arm_until_ms >= 0 && now_ms <= s.arm_until_ms && fast_lead) {
        s.start_ms = now_ms;
        s.arm_until_ms = -1;
      } else if (s.arm_until_ms >= 0 && now_ms > s.arm_until_ms) {
        s.arm_until_ms = -1;
      }
      if (s.start_ms >= 0 && now_ms - s.start_ms >= DURATION_MS) {
        s.start_ms = -1;
      }
    }
  }

  bool active(int i) const { return side[i].start_ms >= 0; }

  // 0 at ignition of the burn, 1 when it is gone. Negative when not active.
  float progress(int i, int64_t now_ms) const {
    if (!active(i)) return -1.0f;
    float t = float(now_ms - side[i].start_ms) / float(DURATION_MS);
    return t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
  }

  bool anyActive() const { return active(0) || active(1); }

private:
  struct Side {
    bool red_was = false;
    int64_t arm_until_ms = -1;
    int64_t start_ms = -1;
  };
  Side side[2];
};

}  // namespace bogpilot_flames

#ifndef BOGPILOT_FLAMES_NO_QT

#include <QColor>
#include <QDateTime>
#include <QLineF>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QPolygonF>
#include <QtMath>

#include "frogpilot/ui/qt/widgets/bogpilot_file_toggle.h"

namespace bogpilot_flames {

inline bool fileEnabled() {
  return bogpilot::readFileToggle(QString::fromLatin1(TOGGLE_KEY), TOGGLE_DEFAULT);
}

// Adjacent-lane polygons come from update_line_data(): for N model points the polygon is
// [right_{N-1} .. right_0, left_0 .. left_{N-1}] (near edge first on the left half).
// Returns the point a fraction `across` (0 = left edge, 1 = right edge) across the lane at row i.
inline bool lanePoint(const QPolygonF &poly, int i, float across, QPointF *out) {
  const int n = poly.size() / 2;
  if (n < 2 || i < 0 || i >= n) return false;
  const QPointF left = poly[n + i];
  const QPointF right = poly[n - 1 - i];
  *out = left + (right - left) * across;
  return true;
}

inline float laneWidthPx(const QPolygonF &poly, int i) {
  const int n = poly.size() / 2;
  if (n < 2 || i < 0 || i >= n) return 0.0f;
  return float(QLineF(poly[n + i], poly[n - 1 - i]).length());
}

// Two burning tire tracks down one adjacent lane. `t` is FlameTrigger::progress (0..1).
// The flame front runs away from the car during the first 0.6 s, the tracks then burn out.
inline void paintTracks(QPainter &p, const QPolygonF &lane, float t, int64_t now_ms) {
  const int n = lane.size() / 2;
  if (n < 2 || t < 0.0f) return;

  const float reach = std::min(1.0f, t / 0.2f);              // fraction of the lane length lit
  const float fade = t < 0.6f ? 1.0f : 1.0f - (t - 0.6f) / 0.4f;  // burn out over the last 1.2 s
  const int last = std::max(1, int(std::round((n - 1) * reach)));
  const float tracks[2] = {0.3f, 0.7f};                        // ~1.6 m apart in a 3.7 m lane

  p.save();
  p.setRenderHint(QPainter::Antialiasing, true);
  p.setBrush(Qt::NoBrush);

  for (float across : tracks) {
    for (int i = 0; i < last; ++i) {
      QPointF a, b;
      if (!lanePoint(lane, i, across, &a) || !lanePoint(lane, i + 1, across, &b)) continue;
      const float w = std::max(2.0f, laneWidthPx(lane, i) * 0.07f);   // perspective: wide near, thin far
      // flicker: cheap per-segment noise that moves with time
      const float flick = 0.75f + 0.25f * float(std::sin(now_ms * 0.025 + i * 1.7 + across * 11.0));
      const float far = float(i) / float(n);
      const float alpha = std::clamp(fade * (1.0f - 0.6f * far), 0.0f, 1.0f);

      // outer orange flame, then yellow core, then white-hot centre line
      p.setPen(QPen(QColor::fromRgbF(1.0, 0.35, 0.0, 0.55 * alpha), w * 2.2f * flick, Qt::SolidLine, Qt::RoundCap));
      p.drawLine(a, b);
      p.setPen(QPen(QColor::fromRgbF(1.0, 0.75, 0.1, 0.8 * alpha), w * 1.1f * flick, Qt::SolidLine, Qt::RoundCap));
      p.drawLine(a, b);
      p.setPen(QPen(QColor::fromRgbF(1.0, 1.0, 0.85, 0.9 * alpha), std::max(1.0f, w * 0.35f), Qt::SolidLine, Qt::RoundCap));
      p.drawLine(a, b);

      // flame tongues licking up from the track
      if (i % 2 == 0 && fade > 0.05f) {
        const float h = FLAME_HEIGHT_SCALE * w * (2.5f + 1.5f * flick) * (1.0f - 0.5f * far);
        QPainterPath tongue;
        tongue.moveTo(a + QPointF(-w * 0.6f, 0));
        tongue.quadTo(a + QPointF(-w * 0.2f, -h * 0.6f), a + QPointF(w * 0.15f * flick, -h));
        tongue.quadTo(a + QPointF(w * 0.4f, -h * 0.5f), a + QPointF(w * 0.6f, 0));
        tongue.closeSubpath();
        p.setPen(Qt::NoPen);
        p.setBrush(QColor::fromRgbF(1.0, 0.5, 0.05, 0.45 * alpha));
        p.drawPath(tongue);
        p.setBrush(Qt::NoBrush);
      }
    }
  }
  p.restore();
}

// Owns the toggle cache and the trigger; call once per frame from the lane-line painter.
class FlameTracks {
public:
  // lanes[0] = left adjacent lane polygon, lanes[1] = right.
  void updateAndPaint(QPainter &p, const FlameSideInput in[2], const QPolygonF lanes[2]) {
    const int64_t now = QDateTime::currentMSecsSinceEpoch();
    if (next_check_ms <= now) {
      enabled = fileEnabled();
      next_check_ms = now + TOGGLE_RECHECK_MS;
    }
    trigger.update(now, enabled, in);
    if (!enabled || !trigger.anyActive()) return;
    for (int i = 0; i < 2; ++i) {
      if (trigger.active(i)) {
        paintTracks(p, lanes[i], trigger.progress(i, now), now);
      }
    }
  }

private:
  FlameTrigger trigger;
  bool enabled = false;
  int64_t next_check_ms = 0;
};

}  // namespace bogpilot_flames

#endif  // BOGPILOT_FLAMES_NO_QT
