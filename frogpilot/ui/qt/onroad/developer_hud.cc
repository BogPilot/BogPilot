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

See developer_hud.h for what is derived from sunnypilot.
***/
#include "frogpilot/ui/qt/onroad/developer_hud.h"

#include <algorithm>
#include <cmath>

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFontMetrics>

#include "selfdrive/ui/qt/util.h"

namespace {

constexpr float MS_TO_KPH_HUD = 3.6f;
constexpr float MS_TO_MPH_HUD = 2.23694f;
constexpr float METER_TO_FOOT_HUD = 3.28084f;
constexpr float GRAVITY = 9.81f;

constexpr int PAD = 18;
constexpr int COL_W = 236;
constexpr int ROW_H = 116;
constexpr int VALUE_PX = 64;
constexpr int LABEL_PX = 30;
constexpr int UNIT_PX = 26;

const QColor WHITE(255, 255, 255);
const QColor GREEN(0, 255, 0);
const QColor ORANGE(255, 188, 0);
const QColor RED(255, 0, 0);
const QColor OVERRIDE_GREY(145, 155, 149);

QColor latColor(const DeveloperHudInputs &in) {
  if (!in.lat_active) return WHITE;
  return in.steering_pressed ? OVERRIDE_GREY : GREEN;
}

QColor angleColor(const DeveloperHudInputs &in, QColor base) {
  if (std::fabs(in.steering_angle_deg) > 180) return RED;
  if (std::fabs(in.steering_angle_deg) > 90) return ORANGE;
  return base;
}

QColor leadSpeedColor(const DeveloperHudInputs &in) {
  if (!in.lead_status) return WHITE;
  if (in.lead_v_rel < -4.4704f) return RED;  // closing faster than 10 mph
  if (in.lead_v_rel < 0) return ORANGE;
  return WHITE;
}

QString num(double v, int decimals) {
  QString s = QString::number(v, 'f', decimals);
  return (s == "-0" || s == "-0.0" || s == "-0.00") ? s.mid(1) : s;
}

}  // namespace

bool developerHudFileEnabled(const QString &path) {
  QFile f(path);
  if (!f.open(QIODevice::ReadOnly)) return false;
  const QString text = QString::fromUtf8(f.read(16)).trimmed().toLower();
  return text == "1" || text == "true" || text == "on";
}

bool setDeveloperHudFileEnabled(bool enabled, const QString &path) {
  QDir().mkpath(QFileInfo(path).absolutePath());
  QFile f(path);
  if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) return false;
  return f.write(enabled ? "1\n" : "0\n") > 0;
}

QVector<DeveloperHudCell> developerHudCells(const DeveloperHudInputs &in) {
  const float speed_conv = in.is_metric ? MS_TO_KPH_HUD : MS_TO_MPH_HUD;
  const QString speed_unit = in.is_metric ? "km/h" : "mph";
  const float dist_conv = in.is_metric ? 1.0f : METER_TO_FOOT_HUD;
  const QString dist_unit = in.is_metric ? "m" : "ft";
  const QString accel_unit = "m/s²";

  QVector<DeveloperHudCell> cells;

  // Row 1: ACCEL | REL DIST
  cells.push_back({num(in.a_ego, 1), "ACCEL", accel_unit, WHITE});
  QColor dist_color = WHITE;
  if (in.lead_status) {
    if (in.lead_d_rel < 5) dist_color = RED;
    else if (in.lead_d_rel < 15) dist_color = ORANGE;
  }
  cells.push_back({in.lead_status ? num(in.lead_d_rel * dist_conv, 0) : "-", "REL DIST", dist_unit, dist_color});

  // Row 2: LEAD SPD | REL SPEED
  cells.push_back({in.lead_status ? num((in.v_ego + in.lead_v_rel) * speed_conv, 0) : "-", "LEAD SPD", speed_unit, leadSpeedColor(in)});
  cells.push_back({in.lead_status ? num(in.lead_v_rel * speed_conv, 0) : "-", "REL SPEED", speed_unit, leadSpeedColor(in)});

  // Row 3: LAT ACCEL (desired, roll compensated) | REAL STEER
  const float desired_lat = in.desired_curvature * in.v_ego * in.v_ego - in.roll * GRAVITY;
  cells.push_back({in.lat_active ? num(desired_lat, 2) : "-", "LAT ACCEL", accel_unit, latColor(in)});
  cells.push_back({num(in.steering_angle_deg, 1) + "°", "REAL STEER", "", angleColor(in, latColor(in))});

  // Row 4: FRICTION (torque cars) or DESIRED STEER (angle / PID cars) | ACTUAL LAT
  if (in.torque_control) {
    cells.push_back({num(in.friction, 3), "FRICTION", "", in.friction_live ? GREEN : WHITE});
  } else {
    QColor c = in.lat_active ? angleColor(in, GREEN) : WHITE;
    cells.push_back({in.lat_active ? num(in.steer_angle_desired, 1) + "°" : "-", "DESIRED STEER", "", c});
  }
  const float actual_lat = in.curvature * in.v_ego * in.v_ego - in.roll * GRAVITY;
  cells.push_back({num(actual_lat, 2), "ACTUAL LAT", accel_unit, latColor(in)});

  // Row 5: ALTITUDE | MEM %
  cells.push_back({in.gps_valid ? num(in.altitude * dist_conv, in.is_metric ? 1 : 0) : "-", "ALTITUDE", dist_unit, WHITE});
  cells.push_back({QString::number(in.memory_usage_percent) + "%", "MEM %", "", in.memory_usage_percent > 85 ? ORANGE : WHITE});

  return cells;
}

QSize developerHudSize() {
  return QSize(COL_W * 2 + PAD * 2, ROW_H * 5 + PAD * 2);
}

QRect developerHudRect(const QPoint &bottomRight, int maxHeight, int maxWidth) {
  const QSize base = developerHudSize();
  // Height fit stays in 0.6 to 1.0; a narrow view (map open) may shrink it to 0.5 to keep it clear of the DM icon / map button
  const qreal scale = std::clamp(std::min(std::clamp(maxHeight / qreal(base.height()), 0.6, 1.0), maxWidth / qreal(base.width())), 0.5, 1.0);
  const int w = int(base.width() * scale);
  const int h = int(base.height() * scale);
  return QRect(bottomRight.x() - w, bottomRight.y() - h, w, h);
}

void paintDeveloperHud(QPainter &p, const QRect &panel, const DeveloperHudInputs &in) {
  const QSize base = developerHudSize();
  const qreal scale = panel.height() / qreal(base.height());

  const QVector<DeveloperHudCell> cells = developerHudCells(in);

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setRenderHint(QPainter::TextAntialiasing);
  p.translate(panel.topLeft());
  p.scale(scale, scale);

  p.setPen(QPen(QColor(255, 255, 255, 200), 5));
  p.setBrush(QColor(0, 0, 0, 120));
  p.drawRoundedRect(QRect(QPoint(0, 0), base).adjusted(3, 3, -3, -3), 28, 28);

  const QFont value_font = InterFont(VALUE_PX, QFont::Bold);
  const QFont label_font = InterFont(LABEL_PX, QFont::Bold);
  const QFont unit_font = InterFont(UNIT_PX, QFont::Bold);
  const QFontMetrics value_fm(value_font);
  const QFontMetrics unit_fm(unit_font);

  for (int i = 0; i < cells.size(); ++i) {
    const DeveloperHudCell &c = cells[i];
    const int col = i % 2;
    const int row = i / 2;
    const QRect cell(PAD + col * COL_W, PAD + row * ROW_H, COL_W, ROW_H);
    const QRect value_rect(cell.x(), cell.y() + 4, cell.width(), 72);
    const QRect label_rect(cell.x() - 6, cell.y() + 74, cell.width() + 12, 38);

    p.setFont(value_font);
    p.setPen(c.color);
    p.drawText(value_rect, Qt::AlignCenter, c.value);

    QFont fitted_label = label_font;
    const int label_w = QFontMetrics(fitted_label).horizontalAdvance(c.label);
    if (label_w > label_rect.width()) {
      fitted_label.setPixelSize(std::max(18, LABEL_PX * label_rect.width() / label_w));
    }
    p.setFont(fitted_label);
    p.setPen(WHITE);
    p.drawText(label_rect, Qt::AlignCenter, c.label);

    if (!c.unit.isEmpty()) {
      // Unit rotated -90° on the outer edge of the value: left edge for the left column, right edge for the right.
      const int value_w = value_fm.horizontalAdvance(c.value);
      const int unit_w = unit_fm.horizontalAdvance(c.unit);
      const int edge_x = col == 0 ? std::max(cell.left() - PAD / 2 + unit_fm.ascent(), cell.center().x() - value_w / 2 - 6)
                                  : std::min(cell.right() + PAD / 2, cell.center().x() + value_w / 2 + 6 + unit_fm.ascent());
      p.save();
      p.setFont(unit_font);
      p.setPen(WHITE);
      p.translate(edge_x, value_rect.center().y() + unit_w / 2);
      p.rotate(-90);
      p.drawText(QPoint(0, 0), c.unit);
      p.restore();
    }
  }

  p.restore();
}

// ---------------- BogPilot redesign style ----------------

namespace {
constexpr int V2_W = 400;
constexpr int V2_H = 430;
constexpr int V2_PAD = 20;
}  // namespace

QSize developerHudSizeV2() {
  return QSize(V2_W, V2_H);
}

QRect developerHudRectV2(const QPoint &bottomRight, int maxWidth) {
  const qreal scale = std::clamp(maxWidth / qreal(V2_W), 0.5, 1.0);
  const int w = int(V2_W * scale), h = int(V2_H * scale);
  return QRect(bottomRight.x() - w, bottomRight.y() - h, w, h);
}

void paintDeveloperHudV2(QPainter &p, const QRect &panel, const DeveloperHudInputs &in) {
  const qreal scale = panel.width() / qreal(V2_W);
  QVector<DeveloperHudCell> cells = developerHudCells(in);

  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);
  p.translate(panel.topLeft());
  p.scale(scale, scale);

  p.setPen(QPen(QColor(255, 255, 255, 30), 2));
  p.setBrush(QColor(14, 17, 22, 168));
  p.drawRoundedRect(QRectF(1, 1, V2_W - 2, V2_H - 2), 26, 26);
  p.setPen(QPen(QColor(255, 255, 255, 22), 1.5));
  p.drawLine(QPointF(V2_W / 2.0, 30), QPointF(V2_W / 2.0, V2_H - 30));

  const QFont label_font = InterFont(19, QFont::DemiBold);
  const QFont value_font = InterFont(44, QFont::DemiBold);
  const QFont unit_font = InterFont(20, QFont::Medium);
  const QFontMetricsF value_fm(value_font);
  const qreal cw = (V2_W - 2 * V2_PAD) / 2.0;
  const qreal rh = (V2_H - 2 * V2_PAD) / 5.0;

  for (int i = 0; i < cells.size(); ++i) {
    DeveloperHudCell c = cells[i];
    // short labels; "°" and "%" move from the value into the unit slot
    if (c.label == "DESIRED STEER") c.label = "DES STEER";
    if (c.label == "MEM %") c.label = "MEM";
    if (c.unit.isEmpty() && (c.value.endsWith("°") || c.value.endsWith("%"))) {
      c.unit = c.value.right(1);
      c.value.chop(1);
    }
    const int col = i % 2, row = i / 2;
    const qreal x = V2_PAD + col * cw + 18;
    const qreal y = V2_PAD + row * rh;
    const qreal max_w = cw - 26;

    p.setFont(label_font);
    p.setPen(QColor(255, 255, 255, 120));
    p.drawText(QRectF(x, y + 10, max_w, 28), Qt::AlignLeft | Qt::AlignVCenter, c.label);

    QFont vf = value_font;
    const QFontMetricsF ufm(unit_font);
    const qreal need = value_fm.horizontalAdvance(c.value) + (c.unit.isEmpty() ? 0 : 6 + ufm.horizontalAdvance(c.unit));
    if (need > max_w) vf.setPixelSize(std::max(26, int(44 * max_w / need)));
    p.setFont(vf);
    p.setPen(c.color == WHITE ? QColor(255, 255, 255, 240) : c.color);
    const qreal base = y + 62 + QFontMetricsF(vf).capHeight() / 2;
    p.drawText(QPointF(x, base), c.value);
    if (!c.unit.isEmpty()) {
      p.setFont(unit_font);
      p.setPen(QColor(255, 255, 255, 120));
      p.drawText(QPointF(x + QFontMetricsF(vf).horizontalAdvance(c.value) + 6, base), c.unit);
    }
  }
  p.restore();
}
