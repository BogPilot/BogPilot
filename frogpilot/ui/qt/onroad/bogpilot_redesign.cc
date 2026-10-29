#include "frogpilot/ui/qt/onroad/bogpilot_redesign.h"

#include <algorithm>
#include <cmath>

#include <QFile>
#include <QFontMetricsF>
#include <QPainterPath>

bool bogpilotRedesignFileEnabled(const QString &path) {
  QFile f(path);
  if (!f.open(QIODevice::ReadOnly)) return true;
  const QString text = QString::fromUtf8(f.read(16)).trimmed().toLower();
  return !(text == "0" || text == "false" || text == "off");
}

namespace bogpilot {

namespace {

constexpr qreal CAP_RADIUS = 44;
constexpr qreal COL_MAX = 140, COL_SPEED = 370, COL_LIMIT = 612;   // screen 170 / 400 / 642
constexpr qreal DIV_1 = 250, DIV_2 = 490;                          // screen 280 / 520
#define BASELINE (qreal(speedRowTop()) + 114)   // shared numeral baseline (screen y 168; follows SwapSpeedGraph)
#define LABEL_BASELINE (BASELINE + 36)          // screen y 204
constexpr int SIDE_PX = 66, BIG_PX = 100, LABEL_PX = 22;
constexpr int TILE_LABEL_PX = 16;

const QColor SIGN_FILL(244, 244, 242, 238);
const QColor SIGN_EDGE(20, 20, 20);
const QColor SIGN_INK(14, 14, 14);
const QColor SIGN_LABEL(55, 55, 55);

}  // namespace

QFont font(int px, QFont::Weight weight) {
  QFont f("Inter");
  f.setPixelSize(px);
  f.setWeight(weight);
  return f;
}

void paintPanel(QPainter &p, const QRectF &r, qreal radius, const QColor &fill, const QColor &stroke, qreal stroke_w) {
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setBrush(fill);
  p.setPen(stroke.alpha() > 0 ? QPen(stroke, stroke_w) : QPen(Qt::NoPen));
  p.drawRoundedRect(r, radius, radius);
  p.restore();
}

void drawCentredBaseline(QPainter &p, qreal cx, qreal baseline, const QString &text) {
  const qreal w = QFontMetricsF(p.font()).horizontalAdvance(text);
  p.drawText(QPointF(cx - w / 2, baseline), text);
}

QString spaced(const QString &s) {
  QString out;
  for (int i = 0; i < s.size(); ++i) {
    if (i) out += ' ';
    out += s[i];
  }
  return out;
}

// ---------------- speed row ----------------

SpeedRowGeometry speedRowGeometry(const SpeedRowInputs &in) {
  SpeedRowGeometry g;
  const qreal y0 = speedRowTop(), y1 = speedRowTop() + SPEED_ROW_HEIGHT;
  qreal x1 = DIV_2 + 14;
  if (in.show_limit) {
    qreal tx0, tx1, ty0, ty1;
    if (in.vienna) {
      const qreal d = 128;
      tx0 = COL_LIMIT - d / 2; tx1 = COL_LIMIT + d / 2;
      ty0 = BASELINE - 92; ty1 = ty0 + d;
    } else {
      const qreal nw = QFontMetricsF(font(SIDE_PX, QFont::Bold)).horizontalAdvance(in.limit);
      const qreal lw = QFontMetricsF(font(TILE_LABEL_PX, QFont::Bold)).horizontalAdvance(in.limit_label);
      const qreal half = std::max(nw, lw) / 2 + 22;
      tx0 = COL_LIMIT - half; tx1 = COL_LIMIT + half;
      ty0 = BASELINE - 92; ty1 = BASELINE + 40;
    }
    g.tile = QRectF(QPointF(tx0, ty0), QPointF(tx1, ty1));
    x1 = std::max<qreal>(tx1 + 44, SPEED_ROW_MIN_RIGHT);
    if (!in.offset.isEmpty()) {
      const qreal bw = QFontMetricsF(font(20, QFont::Bold)).horizontalAdvance(in.offset) + 18;
      const qreal bx = tx1 - 4, by = std::max(ty0 + 2, y0 + 22);
      g.badge = QRectF(bx - bw / 2, by - 15, bw, 30);
    }
  }
  g.capsule = QRectF(QPointF(EDGE, y0), QPointF(x1, y1));
  g.rec_center = QPointF(REC_CX, REC_CY);
  return g;
}

QRectF maxSlotRect() {
  return QRectF(QPointF(EDGE + 6, speedRowTop() + 6), QPointF(DIV_1 - 6, speedRowTop() + SPEED_ROW_HEIGHT - 6));
}

void paintCurveGlyph(QPainter &p, const QPointF &c, qreal size, const QColor &color, bool left, qreal stroke) {
  const qreal sgn = left ? -1.0 : 1.0;
  const qreal R = size * 0.30;
  const qreal stem = c.x() - sgn * size * 0.16;
  const qreal top = c.y() + size * 0.06;
  QPainterPath path(QPointF(stem, c.y() + size * 0.48));
  path.lineTo(stem, top);
  const qreal ccx = stem + sgn * R;
  QPointF end;
  for (int i = 1; i <= 24; ++i) {
    const qreal a = M_PI / 2 * i / 24;
    end = QPointF(ccx - sgn * R * std::cos(a), top - R * std::sin(a));
    path.lineTo(end);
  }
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setBrush(Qt::NoBrush);
  p.setPen(QPen(color, stroke, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
  p.drawPath(path);
  const qreal ah = size * 0.30;
  QPolygonF head;
  head << QPointF(end.x() + sgn * ah * 0.75, end.y()) << QPointF(end.x() - sgn * 0.02 * ah, end.y() - ah * 0.55)
       << QPointF(end.x() - sgn * 0.02 * ah, end.y() + ah * 0.55);
  p.setPen(Qt::NoPen);
  p.setBrush(color);
  p.drawPolygon(head);
  p.restore();
}

SpeedRowGeometry paintSpeedRow(QPainter &p, const SpeedRowInputs &in) {
  const SpeedRowGeometry g = speedRowGeometry(in);
  const qreal y0 = g.capsule.top(), y1 = g.capsule.bottom();

  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);

  if (in.traffic_mode) {
    paintPanel(p, g.capsule, CAP_RADIUS, SPEED_PANEL, QColor(201, 34, 49), 5);
  } else {
    paintPanel(p, g.capsule, CAP_RADIUS, SPEED_PANEL);
  }

  // MAX (cluster set speed), or the Curve Speed Controller slot while it is lowering the speed
  static const QColor CSC_AMBER(255, 176, 32);
  if (in.csc_active) {
    const QRectF slot = maxSlotRect();
    p.setPen(QPen(QColor(255, 176, 32, 210), 3));
    p.setBrush(QColor(255, 176, 32, 46));
    p.drawRoundedRect(slot, 38, 38);
    if (!in.csc_label.isEmpty()) {
      paintCurveGlyph(p, QPointF(COL_MAX, y0 + 42), 42, CSC_AMBER, in.csc_left, 7);
      p.setFont(font(64, QFont::DemiBold));
      p.setPen(CSC_AMBER);
      drawCentredBaseline(p, COL_MAX, BASELINE + 8, in.csc_speed);
      p.setFont(font(26, QFont::DemiBold));
      p.setPen(QColor(255, 205, 110, 245));
      drawCentredBaseline(p, COL_MAX, LABEL_BASELINE + 8, in.csc_label);
    } else {
      // target == N: glyph + number only
      paintCurveGlyph(p, QPointF(COL_MAX, y0 + 50), 46, CSC_AMBER, in.csc_left, 7.5);
      p.setFont(font(70, QFont::DemiBold));
      p.setPen(CSC_AMBER);
      drawCentredBaseline(p, COL_MAX, BASELINE + 26, in.csc_speed);
    }
  } else {
    if (in.csc_training) {
      const qreal k = std::clamp(in.csc_pulse, 0.0, 1.0);
      p.setPen(QPen(QColor(255, 176, 32, int(70 + 140 * k)), 2 + k));
      p.setBrush(Qt::NoBrush);
      p.drawRoundedRect(maxSlotRect(), 38, 38);
      paintCurveGlyph(p, QPointF(COL_MAX, y0 + 42), 38, QColor(255, 176, 32, 200), in.csc_left, 6);
    }
    if (in.show_max || in.csc_training) {
      p.setFont(font(SIDE_PX, QFont::DemiBold));
      p.setPen(in.set_speed_color);
      drawCentredBaseline(p, COL_MAX, in.csc_training ? BASELINE + 8 : BASELINE, in.set_speed);
      p.setFont(font(in.csc_training ? 24 : LABEL_PX, QFont::DemiBold));
      p.setPen(in.csc_training ? QColor(255, 205, 110, 220) : in.max_color);
      drawCentredBaseline(p, COL_MAX, in.csc_training ? LABEL_BASELINE + 8 : LABEL_BASELINE,
                          in.csc_training ? in.csc_label : spaced(in.max_label));
    }
  }

  // current speed
  if (in.show_speed) {
    p.setFont(font(BIG_PX, QFont::Bold));
    p.setPen(QColor(255, 255, 255));
    drawCentredBaseline(p, COL_SPEED, BASELINE, in.speed);
    p.setFont(font(LABEL_PX, QFont::DemiBold));
    p.setPen(MUTED);
    drawCentredBaseline(p, COL_SPEED, LABEL_BASELINE, spaced(in.unit.toUpper()));
  }

  p.setPen(QPen(QColor(255, 255, 255, 36), 2));
  p.drawLine(QPointF(DIV_1, y0 + 40), QPointF(DIV_1, y1 - 40));
  if (in.show_limit) {
    p.drawLine(QPointF(DIV_2, y0 + 40), QPointF(DIV_2, y1 - 40));
  }

  // LIMIT tile (US) or round sign (EU)
  if (in.show_limit) {
    p.save();
    p.setOpacity(in.limit_opacity);
    if (in.vienna) {
      p.setPen(Qt::NoPen);
      p.setBrush(QColor(255, 255, 255));
      p.drawEllipse(g.tile);
      p.setPen(QPen(QColor(220, 30, 30), 13));
      p.setBrush(Qt::NoBrush);
      p.drawEllipse(g.tile.adjusted(9, 9, -9, -9));
      p.setPen(SIGN_INK);
      p.setFont(font(in.limit.size() >= 3 ? 46 : 56, QFont::Bold));
      p.drawText(g.tile, Qt::AlignCenter, in.limit);
    } else {
      p.setPen(QPen(SIGN_EDGE, 3));
      p.setBrush(SIGN_FILL);
      p.drawRoundedRect(g.tile, 20, 20);

      // centre the LIMIT + number pair on their actual ink bounds
      const QFont lf = font(TILE_LABEL_PX, QFont::Bold), nf = font(SIDE_PX, QFont::Bold);
      const QRectF lb = QFontMetricsF(lf).tightBoundingRect(in.limit_label);
      const QRectF nb = QFontMetricsF(nf).tightBoundingRect(in.limit);
      const qreal gap = 10;
      const qreal top = g.tile.center().y() - (lb.height() + gap + nb.height()) / 2;
      p.setFont(lf);
      p.setPen(SIGN_LABEL);
      drawCentredBaseline(p, COL_LIMIT, top - lb.top(), in.limit_label);
      p.setFont(nf);
      p.setPen(SIGN_INK);
      drawCentredBaseline(p, COL_LIMIT, top + lb.height() + gap - nb.top(), in.limit);
    }
    p.restore();

    // +offset badge on the tile corner, inside the capsule
    if (!g.badge.isEmpty()) {
      p.setPen(QPen(QColor(14, 17, 22), 2));
      p.setBrush(BADGE_GREEN);
      p.drawRoundedRect(g.badge, 15, 15);
      p.setFont(font(20, QFont::Bold));
      p.setPen(QColor(255, 255, 255));
      drawCentredBaseline(p, g.badge.center().x(), g.badge.center().y() + 7, in.offset);
    }
  }

  p.restore();
  return g;
}

// ---------------- personality pill ----------------

void paintPersonalityPill(QPainter &p, const QRectF &pill, int personality, bool traffic_mode, const QString &label) {
  static const QColor TRAFFIC_RED(232, 70, 80);

  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);

  const qreal d = std::min(pill.width(), pill.height());
  const QRectF disc(pill.center().x() - d / 2, pill.center().y() - d / 2, d, d);
  paintPanel(p, disc, d / 2, PANEL_DARK, traffic_mode ? TRAFFIC_RED : QColor(255, 255, 255, 60), 3);

  p.setFont(font(20, QFont::DemiBold));
  p.setPen(traffic_mode ? TRAFFIC_RED : QColor(255, 255, 255, 200));
  p.drawText(QRectF(disc.left(), disc.top() - PILL_LABEL_SPACE, disc.width(), PILL_LABEL_SPACE - 4), Qt::AlignCenter, label.toUpper());

  // one / two / three bars (more bars = longer following gap), same bar size as the old pill segments
  const int nb = traffic_mode ? 1 : std::clamp(personality, 0, 2) + 1;
  p.setPen(Qt::NoPen);
  p.setBrush(traffic_mode ? TRAFFIC_RED : QColor(245, 245, 245, 240));
  const qreal mx = disc.center().x();
  for (int k = 0; k < nb; ++k) {
    const qreal yb = disc.center().y() - (nb - 1) * 15 + k * 30;
    p.drawRoundedRect(QRectF(mx - 34, yb - 7.5, 68, 15), 7.5, 7.5);
  }
  p.restore();
}

int personalityAt(const QRectF &pill, const QPointF &pos, int current) {
  const QPointF d = pos - pill.center();   // circle only (label excluded)
  if (d.x() * d.x() + d.y() * d.y() > (pill.width() / 2) * (pill.width() / 2)) return -1;
  // enum aggressive=0, standard=1, relaxed=2: step down like the stalk / controlsd (Relaxed -> Standard -> Aggressive)
  return (std::clamp(current, 0, 2) + 2) % 3;
}

QColor leadDistanceColor(float d_rel, bool adjacent, float v_ego) {
  const float v = std::max(0.0f, v_ego);
  const QColor far0 = adjacent ? QColor(150, 150, 155, 200) : QColor(255, 255, 255, 245);
  if (v < WHITE_BELOW_V) return far0;   // crawling / stop-and-go: always white (muted grey)
  const float FAR = std::max(2.0f * v, 10.0f), MID = std::max(1.0f * v, 5.0f), NEAR = std::max(0.5f * v, 2.0f);
  const QColor far = adjacent ? QColor(150, 150, 155, 200) : QColor(255, 255, 255, 245);
  const QColor mid = adjacent ? QColor(190, 130, 80, 210) : QColor(255, 140, 0, 250);
  const QColor near = adjacent ? QColor(185, 80, 80, 215) : QColor(240, 40, 40, 255);
  auto mix = [](const QColor &a, const QColor &b, float k) {
    k = std::clamp(k, 0.0f, 1.0f);
    return QColor(qRound(a.red() + (b.red() - a.red()) * k), qRound(a.green() + (b.green() - a.green()) * k),
                  qRound(a.blue() + (b.blue() - a.blue()) * k), qRound(a.alpha() + (b.alpha() - a.alpha()) * k));
  };
  if (d_rel >= MID) return mix(mid, far, (d_rel - MID) / (FAR - MID));
  return mix(near, mid, (d_rel - NEAR) / (MID - NEAR));
}

// ---------------- lead lock-on box ----------------

QRectF leadLockBox(const QPointF &contact, float d_rel, qreal max_x, qreal max_y) {
  const qreal sz = std::clamp(750.0 / (d_rel / 3.0 + 30.0), 15.0, 30.0) * 2.35;
  const qreal w = sz * 3.2, h = w * 0.86;
  const qreal cx = std::clamp(contact.x(), w / 2 + 8, max_x - w / 2 - 8);
  const qreal bottom = std::min(contact.y() + h * 0.1, max_y - 8);
  return QRectF(cx - w / 2, bottom - h, w, h);
}

namespace {

// One label pill: number + lighter unit, vertically centred on the digits' cap height, clamped inside view
QRectF paintLabelPill(QPainter &p, qreal cx, qreal cy, qreal h, qreal pad, const QString &number, int num_px, const QColor &num_color,
                      const QString &unit, int unit_px, const QRectF &view, const QColor &accent = QColor()) {
  const QFont nf = font(num_px, QFont::Bold), uf = font(unit_px, QFont::DemiBold);
  const QString u = " " + unit;
  const qreal nw = QFontMetricsF(nf).horizontalAdvance(number), uw = QFontMetricsF(uf).horizontalAdvance(u);
  const qreal w = nw + uw + 2 * pad;
  qreal x0 = cx - w / 2;
  x0 = std::max(x0, view.left() + 8);
  x0 = std::min(x0, view.right() - 8 - w);
  const QRectF r(x0, cy - h / 2, w, h);
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(20, 22, 26, 205));
  p.drawRoundedRect(r, h / 2, h / 2);
  if (accent.isValid()) {
    p.setBrush(accent);
    p.drawRoundedRect(QRectF(x0 + pad, r.bottom() - 7, nw + uw, 4), 2, 2);
  }
  const qreal baseline = cy + QFontMetricsF(nf).capHeight() / 2;
  p.setFont(nf);
  p.setPen(num_color);
  p.drawText(QPointF(x0 + pad, baseline), number);
  p.setFont(uf);
  p.setPen(MUTED);
  p.drawText(QPointF(x0 + pad + nw, baseline), u);
  return r;
}

}  // namespace

qreal leadTagHudOverlap(const QRectF &r) {
  constexpr qreal M = 8;
  const QRectF row = QRectF(EDGE, speedRowTop(), SPEED_ROW_MIN_RIGHT - EDGE, SPEED_ROW_HEIGHT).adjusted(-M, -M, M, M);
  const QRectF graph = accelGraphRect().adjusted(-M, -M, M, M);
  auto area = [&](const QRectF &k) {
    const QRectF i = r.intersected(k);
    return i.isEmpty() ? 0.0 : i.width() * i.height();
  };
  return area(row) + area(graph);
}

bool paintLeadLabels(QPainter &p, const QRectF &box, const LeadLabels &l, const QRectF &view, qreal keepout_top) {
  constexpr qreal GAP = 8 + 14;          // bracket thickness + spacing, as for the old distance pill
  constexpr qreal TAG_H = 46;

  paintLeadSpeedInBox(p, box, l.speed, l.vision_only, l.accent);
  if (l.dist.isEmpty()) return false;

  // Tag geometry for both sides (same width; x clamping does not change the vertical choice)
  const qreal tag_w = QFontMetricsF(font(30, QFont::Bold)).horizontalAdvance(l.dist) +
                      QFontMetricsF(font(22, QFont::DemiBold)).horizontalAdvance(" " + l.dist_unit) + 2 * 18;
  const qreal cx = std::clamp(box.center().x(), view.left() + 8 + tag_w / 2, view.right() - 8 - tag_w / 2);
  const QRectF below(cx - tag_w / 2, box.bottom() + GAP, tag_w, TAG_H);
  const QRectF above(cx - tag_w / 2, box.top() - GAP - TAG_H, tag_w, TAG_H);
  // Cost of each side: area hidden under the speed row / accel graph, plus any reach into the steering arc's area
  const qreal below_cost = leadTagHudOverlap(below) + std::max<qreal>(0, below.bottom() - keepout_top) * tag_w;
  const qreal above_cost = leadTagHudOverlap(above) + std::max<qreal>(0, view.top() + 8 - above.top()) * tag_w;
  const bool put_above = below_cost > 0 && above_cost < below_cost;

  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);
  const qreal tag_cy = put_above ? above.center().y() : below.center().y();
  paintLabelPill(p, box.center().x(), tag_cy, TAG_H, 18, l.dist, 30, QColor(255, 255, 255), l.dist_unit, 22, view);
  p.restore();
  return put_above;
}

void paintLeadLock(QPainter &p, const QRectF &box, const QString &dist, const QColor &color) {
  const qreal thick = 8, thin = 3.5, r = 9, leg = 0.30;
  const QRectF centre = box.adjusted(-thick / 2, -thick / 2, thick / 2, thick / 2);
  const qreal cr = r + thick / 2;

  QPainterPath corners;
  const qreal lw = box.width() * leg, lh = box.height() * leg, o = thick * 2;
  corners.addRect(QRectF(QPointF(box.left() - o, box.top() - o), QPointF(box.left() + lw, box.top() + lh)));
  corners.addRect(QRectF(QPointF(box.right() - lw, box.top() - o), QPointF(box.right() + o, box.top() + lh)));
  corners.addRect(QRectF(QPointF(box.left() - o, box.bottom() - lh), QPointF(box.left() + lw, box.bottom() + o)));
  corners.addRect(QRectF(QPointF(box.right() - lw, box.bottom() - lh), QPointF(box.right() + o, box.bottom() + o)));

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setBrush(Qt::NoBrush);

  auto ring = [&](qreal w, const QColor &c, bool clipped) {
    p.save();
    if (clipped) p.setClipPath(corners, Qt::IntersectClip);
    p.setPen(QPen(c, w, Qt::SolidLine, Qt::FlatCap, Qt::RoundJoin));
    p.drawRoundedRect(centre, cr, cr);
    p.restore();
  };
  // soft shadow (two widened passes stand in for the mockup's blur)
  for (const qreal grow : {6.0, 3.0}) {
    const QColor sh(0, 0, 0, grow > 4 ? 40 : 60);
    ring(thin + grow, sh, false);
    ring(thick + grow, sh, true);
  }
  ring(thin, color, false);
  ring(thick, color, true);

  if (!dist.isEmpty()) {
    const QFont f = font(38, QFont::Bold);
    const qreal tw = QFontMetricsF(f).horizontalAdvance(dist);
    const qreal px = box.center().x(), py = box.top() - thick - 14 - 30;
    const QRectF pill(px - tw / 2 - 22, py - 30, tw + 44, 60);
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(20, 22, 26, 205));
    p.drawRoundedRect(pill, 30, 30);
    p.setFont(f);
    p.setPen(QColor(255, 255, 255));
    p.drawText(pill, Qt::AlignCenter, dist);
  }
  p.restore();
}

namespace {

// Number pill centred in a lock-on box: 0.42 x box height (24-64 px), shrunk until it fits inside the corner brackets
// (41 px on the smallest box, 97 px tall; 112 on the smallest box: 37 px).
struct InBoxPill {
  QRectF rect;
  int px = 0;
};

InBoxPill inBoxPill(const QRectF &box, const QString &text) {
  constexpr qreal BRACKET = 8 + 6;                  // corner bracket thickness + breathing room, each side
  InBoxPill out;
  const qreal avail_w = box.width() - 2 * BRACKET, avail_h = box.height() - 2 * BRACKET;
  if (avail_w <= 0 || avail_h <= 0 || text.isEmpty()) return out;
  int px = std::clamp(qRound(box.height() * 0.42), 24, 64);
  qreal w = 0, h = 0;
  for (int i = 0; i < 8; ++i) {
    const qreal pad = px * 0.32;
    w = QFontMetricsF(font(px, QFont::Bold)).horizontalAdvance(text) + 2 * pad;
    h = px * 1.28;
    const qreal k = std::min(avail_w / w, avail_h / h);
    if (k >= 1.0 || px <= 18) break;
    px = std::max(18, int(std::floor(px * k)));
  }
  const QPointF c = box.center();
  out.rect = QRectF(c.x() - w / 2, c.y() - h / 2, w, h);
  out.px = px;
  return out;
}

}  // namespace

QVector<QRectF> bottomCornerKeepout(qreal widget_w, bool rhd, bool map_button, bool pedals, const QVector<QRectF> &status_icons) {
  auto mirrored = [&](const QRectF &r) { return rhd ? QRectF(widget_w - r.x() - r.width(), r.y(), r.width(), r.height()) : r; };
  QVector<QRectF> k;
  k.append(mirrored(QRectF(REC_CX - REC_HIT / 2, REC_CY - REC_HIT / 2, REC_HIT, REC_HIT)));
  k.append(mirrored(QRectF(NOGPS_CX - REC_DIAMETER / 2, NOGPS_CY - REC_DIAMETER / 2, REC_DIAMETER, REC_DIAMETER)));
  const qreal cluster_w = PILL_W + CLUSTER_GAP + CLUSTER_D + (map_button ? CLUSTER_GAP + CLUSTER_D : 0);
  k.append(mirrored(QRectF(CLUSTER_LEFT, CLUSTER_CY - CLUSTER_D / 2 - PILL_LABEL_SPACE, cluster_w, CLUSTER_D + PILL_LABEL_SPACE)));
  if (pedals) {
    k.append(mirrored(QRectF(EDGE, STACK_BOTTOM + PEDAL_GAP, 2 * PEDAL_W, PEDAL_H)));
  }
  k += status_icons;
  return k;
}

AdjacentSpeedPlacement placeAdjacentSpeed(const QRectF &box, const QString &speed, const QVector<QRectF> &keepout,
                                          bool rhd, const QRectF &view) {
  AdjacentSpeedPlacement out;
  const InBoxPill pill = inBoxPill(box, speed);
  out.rect = pill.rect;
  out.px = pill.px;
  if (pill.px == 0 || keepout.isEmpty()) return out;

  const qreal m = ADJ_LABEL_MARGIN;
  auto hits = [&](const QRectF &r) {
    QVector<QRectF> h;
    const QRectF grown = r.adjusted(-m, -m, m, m);
    for (const QRectF &k : keepout) {
      if (grown.intersects(k)) h.append(k);
    }
    return h;
  };
  if (hits(pill.rect).isEmpty()) return out;   // clear of the icons: stays in the box

  const qreal w = pill.rect.width(), h = pill.rect.height();
  const qreal cy = box.center().y();
  QRectF r(rhd ? box.left() - ADJ_LABEL_OUTSIDE - w : box.right() + ADJ_LABEL_OUTSIDE, cy - h / 2, w, h);
  for (int pass = 0; pass < 8; ++pass) {
    const QVector<QRectF> hs = hits(r);
    if (hs.isEmpty()) break;
    qreal edge = rhd ? hs.first().left() : hs.first().right();
    for (const QRectF &k : hs) edge = rhd ? std::min(edge, k.left()) : std::max(edge, k.right());
    r.moveLeft(rhd ? edge - m - ADJ_LABEL_SLIDE_GAP - w : edge + m + ADJ_LABEL_SLIDE_GAP);
  }
  if (view.isValid()) {
    r.moveLeft(std::clamp(r.left(), view.left() + 8, std::max(view.left() + 8, view.right() - 8 - w)));
  }
  out.rect = r;
  out.outside = true;
  const qreal gap = rhd ? box.left() - r.right() : r.left() - box.right();
  if (gap > ADJ_LABEL_LEADER_MIN) {
    out.leader = true;
    out.leader_line = rhd ? QLineF(box.left() - ADJ_LABEL_BRACKET, cy, r.right(), cy)
                          : QLineF(box.right() + ADJ_LABEL_BRACKET, cy, r.left(), cy);
  }
  return out;
}

void paintAdjacentSpeed(QPainter &p, const QRectF &box, const QString &speed, const QColor &box_color,
                        const QVector<QRectF> &keepout, bool rhd, const QRectF &view) {
  const AdjacentSpeedPlacement place = placeAdjacentSpeed(box, speed, keepout, rhd, view);
  if (place.px == 0) return;
  const QRectF &r = place.rect;
  const QColor ink(std::min(255, box_color.red() + 70), std::min(255, box_color.green() + 70),
                   std::min(255, box_color.blue() + 70), 240);
  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);
  if (place.leader) {
    QColor lc = box_color;
    lc.setAlpha(150);
    p.setPen(QPen(lc, 3, Qt::SolidLine, Qt::RoundCap));
    p.drawLine(place.leader_line);
  }
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(20, 22, 26, 165));
  p.drawRoundedRect(r, r.height() / 2, r.height() / 2);
  p.setFont(font(place.px, QFont::Bold));
  p.setPen(ink);
  p.drawText(r, Qt::AlignCenter, speed);
  p.restore();
}

void paintLeadSpeedInBox(QPainter &p, const QRectF &box, const QString &speed, bool vision_only, const QColor &accent) {
  const InBoxPill pill = inBoxPill(box, speed);
  if (pill.px == 0) return;
  const QRectF &r = pill.rect;
  const QFont f = font(pill.px, QFont::Bold);
  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);
  p.setPen(Qt::NoPen);
  p.setBrush(QColor(20, 22, 26, 190));              // a little darker than the adjacent pill (165)
  p.drawRoundedRect(r, r.height() / 2, r.height() / 2);
  if (accent.isValid()) {
    const qreal tw = QFontMetricsF(f).horizontalAdvance(speed);
    const qreal bar_h = std::max<qreal>(3, r.height() * 0.06);
    p.setBrush(accent);
    p.drawRoundedRect(QRectF(r.center().x() - tw / 2, r.bottom() - std::max<qreal>(5, r.height() * 0.10), tw, bar_h), 2, 2);
  }
  p.setFont(f);
  // camera-only leads keep near-white ink (the "~" marks them) so the number stays readable on the 3X screen
  p.setPen(vision_only ? QColor(255, 255, 255, 230) : QColor(255, 255, 255));
  p.drawText(r, Qt::AlignCenter, speed);
  p.restore();
}

// ---------------- LKAS Fault border ----------------

bool isLkasFaultAlert(const QString &alert_type, const QString &text1, const QString &text2) {
  return alert_type.startsWith("steerUnavailable/") || text1.contains("LKAS Fault", Qt::CaseInsensitive) ||
         text2.contains("LKAS Fault", Qt::CaseInsensitive);
}

QColor lkasFaultBorderColor(double millis) {
  const long long phase = (long long)std::floor(std::max(0.0, millis) / LKAS_FAULT_FLASH_MS);
  return (phase % 2 == 0) ? LKAS_FAULT_RED : LKAS_FAULT_ORANGE;
}

// ---------------- round buttons ----------------

void paintRecordButton(QPainter &p, const QRectF &hit, bool recording, qreal pulse) {
  // disc of REC_DIAMETER (or the hit rect if smaller) centred in the touch target; dot / stop square scale with it
  const QPointF c = hit.center();
  const qreal d = std::min<qreal>(REC_DIAMETER, std::min(hit.width(), hit.height()));
  const QRectF r(c.x() - d / 2, c.y() - d / 2, d, d);
  const qreal dot = d * 0.19, sq = d * 0.35;
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  if (recording) {
    QColor glow(225, 60, 60);
    glow.setAlphaF(0.4 + 0.6 * std::clamp(pulse, 0.0, 1.0));
    p.setBrush(QColor(14, 17, 22, 190));
    p.setPen(QPen(glow, 5));
    p.drawEllipse(r.adjusted(3, 3, -3, -3));
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(225, 60, 60, 235));
    p.drawRoundedRect(QRectF(c.x() - sq / 2, c.y() - sq / 2, sq, sq), 7, 7);
  } else {
    p.setBrush(QColor(14, 17, 22, 190));
    p.setPen(QPen(QColor(255, 255, 255, 60), 3));
    p.drawEllipse(r.adjusted(2, 2, -2, -2));
    p.setPen(Qt::NoPen);
    p.setBrush(QColor(225, 60, 60, 240));
    p.drawEllipse(c, dot, dot);
  }
  p.restore();
}

void paintIconButton(QPainter &p, const QRectF &r, const QPixmap &icon, qreal opacity) {
  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::SmoothPixmapTransform);
  p.setPen(Qt::NoPen);
  p.setBrush(PANEL_DARK);
  p.drawEllipse(r);
  p.setOpacity(opacity);
  p.drawPixmap(QPointF(r.center().x() - icon.width() / 2.0, r.center().y() - icon.height() / 2.0), icon);
  p.restore();
}

void paintNoGpsIndicator(QPainter &p, const QPointF &c, bool slash_on) {
  const qreal d = REC_DIAMETER;
  const QRectF r(c.x() - d / 2, c.y() - d / 2, d, d);
  const qreal u = d / 124.0 * 1.18;   // satellite drawing unit (mockup scale)
  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.setBrush(QColor(14, 17, 22, 190));
  p.setPen(QPen(QColor(255, 255, 255, 60), 3));
  p.drawEllipse(r.adjusted(2, 2, -2, -2));

  // satellite: body, two solar panels on struts, dish below
  const QColor white(255, 255, 255);
  const qreal sx = c.x(), sy = c.y() - 4 * u;
  p.setPen(Qt::NoPen);
  p.setBrush(white);
  p.drawRect(QRectF(sx - 12 * u, sy - 12 * u, 24 * u, 24 * u));
  p.setBrush(Qt::NoBrush);
  for (int g : {-1, 1}) {
    const qreal a = std::min(sx + g * 18 * u, sx + g * 44 * u), b = std::max(sx + g * 18 * u, sx + g * 44 * u);
    p.setPen(QPen(white, 4 * u, Qt::SolidLine, Qt::SquareCap, Qt::MiterJoin));
    p.drawRect(QRectF(a, sy - 9 * u, b - a, 18 * u));
    p.drawLine(QPointF(sx + g * 12 * u, sy), QPointF(sx + g * 18 * u, sy));
    p.setPen(QPen(white, 3 * u));
    p.drawLine(QPointF((a + b) / 2, sy - 9 * u), QPointF((a + b) / 2, sy + 9 * u));
  }
  p.setPen(QPen(white, 4 * u, Qt::SolidLine, Qt::FlatCap));
  p.drawLine(QPointF(sx, sy + 12 * u), QPointF(sx, sy + 22 * u));
  p.drawArc(QRectF(sx - 14 * u, sy + 16 * u, 28 * u, 20 * u), -200 * 16, 140 * 16);

  if (slash_on) {
    const qreal rr = d * 48.0 / 124.0, w = d * 9.0 / 124.0;
    p.setPen(QPen(QColor(225, 60, 60), w, Qt::SolidLine, Qt::FlatCap));
    p.drawEllipse(c, rr, rr);
    const qreal q = (rr - w / 2) * 0.7071;
    p.drawLine(QPointF(c.x() - q, c.y() - q), QPointF(c.x() + q, c.y() + q));
  }
  p.restore();
}

// ---------------- alert banner ----------------

QRect alertBannerRect(const QSize &widget, bool mid) {
  if (widget.width() >= 1800) {
    const int left = REC_CX + REC_HIT / 2 + ALERT_GAP;                    // 36 px right of the record button
    const int right = widget.width() - ALERT_RIGHT_MARGIN;                // right margin = record button width
    const int top = STACK_BOTTOM + ALERT_GAP;                             // 36 px below the speed row / graph stack
    const int bottom = CLUSTER_CY - CLUSTER_D / 2 - PILL_LABEL_SPACE - ALERT_GAP;   // 36 px above the button row + label
    const int h = mid ? bottom - top : std::min(bottom - top, 170);
    const int y = top + (bottom - top - h) / 2;
    return QRect(left, y, right - left, h);
  }
  const int h = mid ? 300 : 170;
  return QRect(40, widget.height() - 230 - h, widget.width() - 80, h);
}

void paintAlertBanner(QPainter &p, const QRect &r, bool mid, const QString &text1, const QString &text2, const QColor &fill) {
  p.save();
  p.setRenderHints(QPainter::Antialiasing | QPainter::TextAntialiasing);
  // FrogPilot alert style: status colour plus the 5 % -> 35 % dark vertical gradient, 30 px corners
  paintPanel(p, r, 30, fill, STROKE, 2);
  QLinearGradient g(0, r.top(), 0, r.bottom());
  g.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.05));
  g.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0.35));
  p.setPen(Qt::NoPen);
  p.setBrush(g);
  p.drawRoundedRect(r, 30, 30);

  auto fitted = [&](const QString &text, int px, QFont::Weight w) {
    QFont f = font(px, w);
    const qreal max_w = r.width() - 120;   // generous 60 px side margins
    const qreal tw = QFontMetricsF(f).horizontalAdvance(text);
    if (tw > max_w) f.setPixelSize(std::max(28, int(px * max_w / tw)));
    return f;
  };

  // roughly FrogPilot's mid-alert sizing (Inter Bold 88 / Regular 66), a touch larger for the wider banner
  p.setPen(TEXT);
  if (mid) {
    const QFont f1 = fitted(text1, 96, QFont::Bold), f2 = fitted(text2, 70, QFont::Normal);
    const qreal h1 = QFontMetricsF(f1).capHeight(), h2 = QFontMetricsF(f2).capHeight(), gap = 56;
    const qreal y0 = r.center().y() - (h1 + gap + h2) / 2;
    p.setFont(f1);
    drawCentredBaseline(p, r.center().x(), y0 + h1, text1);
    p.setFont(f2);
    drawCentredBaseline(p, r.center().x(), y0 + h1 + gap + h2, text2);
  } else {
    p.setFont(fitted(text1, 80, QFont::DemiBold));
    p.drawText(r, Qt::AlignCenter, text1);
  }
  p.restore();
}

}  // namespace bogpilot
