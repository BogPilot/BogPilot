#pragma once

// BogPilot confidence ball: the comma four "confidence ball" on BogPilot's Qt driving screen.
//
// Ported from openpilot selfdrive/ui/mici/onroad/confidence_ball.py (commaai/openpilot a742df6,
// MIT License, Copyright (c) 2018, Comma.ai, Inc.). Same math and colors: the model's own
// disengage predictions give confidence = (1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs)),
// smoothed by a first-order filter (rc 0.5 s, starts at -0.5). Engaged: the ball rides high and green
// above 50%, orange above 20%, red below. Driver override: white to grey. Disengaged: the target is
// -0.5, so a dark ball slides out of the bottom of the screen.
//
// Layout for the comma 3X (2160x1080): a 60 px strip on the left (default) or right edge of the onroad
// window that covers the 30 px status border and the 30 px camera margin, so the ball never overlaps the
// MAX / speed-limit box, the personality button, the driver-monitoring icon, the Experimental button or
// the Developer HUD panel (all of those start 30 px inside the camera view). The left edge is the default
// because a rear-view mirror can hide the top half of the right side of the screen. When the map panel
// covers the chosen side, the ball moves to the other edge until the map closes.
//
// Display only. Settings are files, because the prebuilt params_pyx.so cannot store new Params keys:
//   /data/params_bogpilot/ConfidenceBall      "0"/"false"/"off" hides it, anything else or no file shows it
//   /data/params_bogpilot/ConfidenceBallSide  "right" puts it on the right edge, anything else or no file: left

#include <algorithm>
#include <utility>

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QLinearGradient>
#include <QPainter>
#include <QWidget>

#include "common/util.h"
#include "selfdrive/ui/ui.h"

namespace bogpilot_confidence {

inline QString togglePath() {
  return QStringLiteral("/data/params_bogpilot/ConfidenceBall");
}

// Same read rules as BogPilot's other /data/params_bogpilot switches; default on.
inline bool fileEnabled(const QString &path = togglePath()) {
  QFile file(path);
  if (!file.open(QIODevice::ReadOnly)) {
    return true;
  }
  const QByteArray text = file.read(64).trimmed();
  return !(text == "0" || text == "false" || text == "off");
}

inline void setFileEnabled(bool on, const QString &path = togglePath()) {
  QDir().mkpath(QFileInfo(path).absolutePath());
  QFile file(path);
  if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    file.write(on ? "1" : "0");
  }
}

inline QString sidePath() {
  return QStringLiteral("/data/params_bogpilot/ConfidenceBallSide");
}

// Default left: only "right" (any case, surrounding whitespace ignored) selects the right edge.
inline bool fileRightSide(const QString &path = sidePath()) {
  QFile file(path);
  if (!file.open(QIODevice::ReadOnly)) {
    return false;
  }
  return file.read(64).trimmed().toLower() == "right";
}

inline void setFileRightSide(bool right, const QString &path = sidePath()) {
  QDir().mkpath(QFileInfo(path).absolutePath());
  QFile file(path);
  if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    file.write(right ? "right" : "left");
  }
}

// Which edge to draw on: the chosen side, unless the open map panel covers it (then the other edge).
// map_open: the map panel is showing; map_on_left: it sits on the left side of the camera view.
inline bool useRightEdge(bool prefer_right, bool map_open, bool map_on_left) {
  if (map_open && prefer_right != map_on_left) {
    return !prefer_right;  // the map covers the chosen edge
  }
  return prefer_right;
}

// Any active openpilot status (engaged, Always On Lateral, traffic mode, ...) counts as engaged.
inline bool engagedStatus(UIStatus status) {
  return status != STATUS_DISENGAGED && status != STATUS_OVERRIDE;
}

template <typename List>
inline float maxOrOne(const List &probs) {
  if (probs.size() == 0) {
    return 1.0f;
  }
  float m = probs[0];
  for (float p : probs) {
    m = std::max(m, p);
  }
  return m;
}

// Python: (1 - max(brakeDisengageProbs or [1])) * (1 - max(steerOverrideProbs or [1]))
inline float modelConfidence(const cereal::ModelDataV2::Reader &model) {
  const auto dp = model.getMeta().getDisengagePredictions();
  return (1.0f - maxOrOne(dp.getBrakeDisengageProbs())) * (1.0f - maxOrOne(dp.getSteerOverrideProbs()));
}

// Top and bottom gradient colors, exactly as on the comma four.
inline std::pair<QColor, QColor> ballColors(UIStatus status, float x) {
  if (engagedStatus(status)) {
    if (x > 0.5f) return {QColor(0, 255, 204), QColor(0, 255, 38)};
    if (x > 0.2f) return {QColor(255, 200, 0), QColor(255, 115, 0)};
    return {QColor(255, 0, 21), QColor(255, 0, 89)};
  }
  if (status == STATUS_OVERRIDE) {
    return {QColor(255, 255, 255), QColor(82, 82, 82)};
  }
  return {QColor(50, 50, 50), QColor(13, 13, 13)};
}

// Vertical gradient clipped to a circle.
inline void paintBall(QPainter &p, const QPointF &center, float radius, const QColor &top, const QColor &bottom) {
  QLinearGradient gradient(center.x(), center.y() - radius, center.x(), center.y() + radius);
  gradient.setColorAt(0.0, top);
  gradient.setColorAt(1.0, bottom);
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setPen(Qt::NoPen);
  p.setBrush(gradient);
  p.drawEllipse(center, radius, radius);
  p.restore();
}

// Ball center height inside a track of the given height: (1 - x) * (h - 2r - 2m) + r + m.
inline float ballCenterY(float x, float track_height, float radius, float margin) {
  return (1.0f - x) * (track_height - 2.0f * radius - 2.0f * margin) + radius + margin;
}

}  // namespace bogpilot_confidence

class ConfidenceBall : public QWidget {
public:
  static constexpr int STRIP_WIDTH = 2 * UI_BORDER_SIZE;  // status border + camera margin
  static constexpr float RADIUS = 27.0f;                  // 54 px ball (comma four: 24 on a much smaller screen)
  static constexpr float MARGIN = 3.0f;

  explicit ConfidenceBall(QWidget *parent) : QWidget(parent) {
    setAttribute(Qt::WA_TransparentForMouseEvents, true);
    setAttribute(Qt::WA_NoSystemBackground, true);
    setVisible(false);
  }

  // Left or right edge of the parent, full height.
  void placeIn(const QRect &parent_rect) {
    parent_area = parent_rect;
    const int x = on_right ? parent_rect.right() + 1 - STRIP_WIDTH : parent_rect.left();
    setGeometry(x, parent_rect.top(), STRIP_WIDTH, parent_rect.height());
  }

  // Called every UI tick (20 Hz). map_open / map_on_left describe the map panel; camera_visible is false
  // when the full-screen map hides the camera view (both edges covered).
  void updateState(const UIState &s, bool map_open, bool map_on_left, bool camera_visible) {
    if (check_frame-- <= 0) {
      enabled = bogpilot_confidence::fileEnabled();
      prefer_right = bogpilot_confidence::fileRightSide();
      check_frame = UI_FREQ * 2;  // re-read the files every 2 s
    }

    const bool right_now = bogpilot_confidence::useRightEdge(prefer_right, map_open, map_on_left);
    if (right_now != on_right) {
      on_right = right_now;
      placeIn(parent_area);
    }

    status = s.status;
    if (status == STATUS_DISENGAGED) {
      filter.update(-0.5f);
    } else {
      filter.update(bogpilot_confidence::modelConfidence((*s.sm)["modelV2"].getModelV2()));
    }

    const bool visible_now = enabled && s.scene.started && camera_visible;
    if (visible_now != isVisible()) {
      setVisible(visible_now);
      if (visible_now) raise();
    }
    if (!visible_now) {
      return;
    }

    const int y = (int)bogpilot_confidence::ballCenterY(filter.x(), height(), RADIUS, MARGIN);
    const auto colors = bogpilot_confidence::ballColors(status, filter.x());
    if (y != drawn_y || colors.first != drawn_top) {
      update();
    }
  }

  float confidence() { return filter.x(); }

protected:
  void paintEvent(QPaintEvent *event) override {
    QPainter p(this);
    const auto colors = bogpilot_confidence::ballColors(status, filter.x());
    drawn_y = (int)bogpilot_confidence::ballCenterY(filter.x(), height(), RADIUS, MARGIN);
    drawn_top = colors.first;
    bogpilot_confidence::paintBall(p, QPointF(width() / 2.0, drawn_y), RADIUS, colors.first, colors.second);
  }

private:
  FirstOrderFilter filter = FirstOrderFilter(-0.5f, 0.5f, 1.0f / UI_FREQ);
  UIStatus status = STATUS_DISENGAGED;
  bool enabled = true;
  bool prefer_right = false;
  bool on_right = false;
  QRect parent_area;
  int check_frame = 0;
  int drawn_y = -1;
  QColor drawn_top;
};
