#include "frogpilot/ui/qt/onroad/accel_graph.h"

#include <algorithm>
#include <cmath>

#include <QPainterPath>

#include "frogpilot/ui/qt/onroad/bogpilot_redesign.h"

AccelGraph::AccelGraph(int hz, float seconds) : capacity_(std::max(2, int(hz * seconds))), seconds_(seconds) {}

void AccelGraph::push(float cmd, float actual) {
  if (!std::isfinite(cmd)) cmd = 0.0f;
  if (!std::isfinite(actual)) actual = 0.0f;
  cmd_.push_back(cmd);
  act_.push_back(actual);
  while (int(cmd_.size()) > capacity_) {
    cmd_.pop_front();
    act_.pop_front();
  }
}

void AccelGraph::clear() {
  cmd_.clear();
  act_.clear();
}

float AccelGraph::range() const {
  float m = 0.0f;
  for (size_t i = 0; i < cmd_.size(); ++i) {
    m = std::max({m, std::fabs(cmd_[i]), std::fabs(act_[i])});
  }
  return std::clamp(std::ceil((m + 0.25f) * 2.0f) / 2.0f, 1.5f, 4.0f);
}

void AccelGraph::paint(QPainter &p, const QRectF &panel) const {
  using namespace bogpilot;
  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);
  paintPanel(p, panel, 26);

  const qreal x0 = panel.left(), y0 = panel.top(), x1 = panel.right(), y1 = panel.bottom();

  // header + legend
  p.setFont(font(24, QFont::DemiBold));
  p.setPen(MUTED);
  p.drawText(QRectF(x0 + 26, y0 + 12, 120, 40), Qt::AlignLeft | Qt::AlignVCenter, "ACCEL");
  p.setFont(font(22, QFont::Normal));
  p.setPen(QColor(255, 255, 255, 90));
  p.drawText(QRectF(x0 + 116, y0 + 12, 80, 40), Qt::AlignLeft | Qt::AlignVCenter, QString("%1 s").arg(int(std::lround(seconds_))));

  qreal lx = x1 - 250;
  p.setFont(font(22, QFont::Medium));
  p.setPen(QPen(TEXT, 4, Qt::SolidLine, Qt::RoundCap));
  p.drawLine(QPointF(lx, y0 + 32), QPointF(lx + 34, y0 + 32));
  p.setPen(MUTED);
  p.drawText(QRectF(lx + 44, y0 + 12, 70, 40), Qt::AlignLeft | Qt::AlignVCenter, "cmd");
  lx += 112;
  p.setPen(QPen(ACTUAL_GREEN, 4, Qt::SolidLine, Qt::RoundCap));
  p.drawLine(QPointF(lx, y0 + 32), QPointF(lx + 34, y0 + 32));
  p.setPen(MUTED);
  p.drawText(QRectF(lx + 44, y0 + 12, 90, 40), Qt::AlignLeft | Qt::AlignVCenter, "actual");

  // plot area
  const qreal px0 = x0 + 26, py0 = y0 + 64, px1 = x1 - 26, py1 = y1 - 22;
  const float rng = range();
  auto Y = [&](float v) { return py0 + (py1 - py0) * (rng - std::clamp(v, -rng, rng)) / (2 * rng); };

  const float grid = rng >= 3.0f ? 2.0f : 1.0f;
  p.setFont(font(18, QFont::Normal));
  for (const float v : {grid, -grid}) {
    p.setPen(QPen(QColor(255, 255, 255, 22), 1.5));
    p.drawLine(QPointF(px0, Y(v)), QPointF(px1, Y(v)));
    p.setPen(QColor(255, 255, 255, 80));
    p.drawText(QRectF(px1 - 60, Y(v) - 26, 60, 22), Qt::AlignRight | Qt::AlignBottom, QString::asprintf("%+.0f", v));
  }
  p.setPen(QPen(QColor(255, 255, 255, 55), 1.5));
  p.drawLine(QPointF(px0, Y(0)), QPointF(px1, Y(0)));

  const int n = size();
  if (n >= 2) {
    // fixed time axis: newest sample at the right edge, 10 s across
    const qreal dx = (px1 - px0) / (capacity_ - 1);
    auto path = [&](const std::deque<float> &v) {
      QPainterPath pp;
      for (int i = 0; i < n; ++i) {
        const QPointF pt(px1 - (n - 1 - i) * dx, Y(v[i]));
        i == 0 ? pp.moveTo(pt) : pp.lineTo(pt);
      }
      return pp;
    };
    p.setBrush(Qt::NoBrush);
    p.setPen(QPen(ACTUAL_GREEN, 5, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(path(act_));
    p.setPen(QPen(TEXT, 3.5, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPath(path(cmd_));
    p.setPen(Qt::NoPen);
    p.setBrush(ACTUAL_GREEN);
    p.drawEllipse(QPointF(px1, Y(act_.back())), 7, 7);
  }
  p.restore();
}
