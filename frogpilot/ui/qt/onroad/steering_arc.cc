// BogPilot steering arc. QPainter port of upstream openpilot selfdrive/ui/mici/onroad/torque_bar.py (MIT);
// see steering_arc.h for the licence and the list of BogPilot changes.
#include "frogpilot/ui/qt/onroad/steering_arc.h"

#include <algorithm>
#include <cmath>
#include <vector>

#include <QLinearGradient>
#include <QPainterPath>

namespace {

constexpr float GRAVITY = 9.81f;
constexpr float FILTER_K = (0.05f / 0.1f) / (1.0f + 0.05f / 0.1f);   // FirstOrderFilter(rc 0.1 s, dt 1/20 s)
constexpr int CAP_SEGS = 10;
constexpr int MAX_ARC_SEGS = 64;

const QColor WHITE(255, 255, 255);
const QColor PURPLE(150, 100, 255);
const QColor YELLOW(255, 200, 0);
const QColor ORANGE(255, 115, 0);
const QColor DOT_GREY(182, 182, 182);
const QColor HUMAN_CYAN(90, 205, 255);

float interp(float x, float x0, float x1, float y0, float y1) {
  if (x <= x0) return y0;
  if (x >= x1) return y1;
  return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
}

float rad(float deg) { return deg * float(M_PI) / 180.0f; }

QColor withAlpha(QColor c, float a) {
  c.setAlphaF(std::clamp(a, 0.0f, 1.0f));
  return c;
}

QColor blend(const QColor &a, const QColor &b, float k) {
  k = std::clamp(k, 0.0f, 1.0f);
  return QColor::fromRgbF(a.redF() + (b.redF() - a.redF()) * k, a.greenF() + (b.greenF() - a.greenF()) * k,
                          a.blueF() + (b.blueF() - a.blueF()) * k, a.alphaF() + (b.alphaF() - a.alphaF()) * k);
}

// One rounded end of the thick arc (upstream get_cap): a quarter circle at the outer corner and one at the inner corner.
void appendCap(std::vector<QPointF> &out, bool left, float a_deg, float r_mid, float half, float cap) {
  const float nx = std::cos(rad(a_deg)), ny = std::sin(rad(a_deg));   // outward normal
  const float tx = -ny, ty = nx;                                        // tangent (CCW)
  const float mx = nx * r_mid, my = ny * r_mid;

  std::vector<QPointF> top, bottom;
  const float ex = mx + nx * (half - cap), ey = my + ny * (half - cap);
  for (int i = 1; i <= CAP_SEGS; ++i) {            // linspace(..., CAP_SEGS + 2)[1:-1]
    const float f = float(i) / (CAP_SEGS + 1);
    const float a = rad(left ? 180.0f - 90.0f * f : 90.0f - 90.0f * f);
    top.emplace_back(ex + std::cos(a) * cap * tx + std::sin(a) * cap * nx, ey + std::cos(a) * cap * ty + std::sin(a) * cap * ny);
  }
  const float ex2 = mx + nx * (-half + cap), ey2 = my + ny * (-half + cap);
  for (int i = 0; i < CAP_SEGS; ++i) {             // linspace(..., CAP_SEGS + 1)[:-1]
    const float f = float(i) / CAP_SEGS;
    const float a = rad(left ? -90.0f - 90.0f * f : -90.0f * f);
    bottom.emplace_back(ex2 + std::cos(a) * cap * tx + std::sin(a) * cap * nx, ey2 + std::cos(a) * cap * ty + std::sin(a) * cap * ny);
  }
  const std::vector<QPointF> &first = left ? bottom : top, &second = left ? top : bottom;
  out.insert(out.end(), first.begin(), first.end());
  out.insert(out.end(), second.begin(), second.end());
}

}  // namespace

QPolygonF SteeringArc::arcBarPolygon(float r_mid, float thickness, float a0_deg, float a1_deg, float cap_radius) {
  if (a1_deg < a0_deg) std::swap(a0_deg, a1_deg);
  const float half = thickness * 0.5f;
  const float cap = std::min(cap_radius, half);
  const float span = std::max(1e-3f, a1_deg - a0_deg);
  const int segs = std::clamp(int(r_mid * rad(span) / 2.0f), 6, MAX_ARC_SEGS);

  std::vector<QPointF> pts;
  pts.reserve(2 * (segs + 1) + 4 * CAP_SEGS + 1);
  for (int i = 0; i <= segs; ++i) {   // outer arc a0 -> a1
    const float a = rad(a0_deg + (a1_deg - a0_deg) * i / segs);
    pts.emplace_back(std::cos(a) * (r_mid + half), std::sin(a) * (r_mid + half));
  }
  appendCap(pts, false, a1_deg, r_mid, half, cap);
  for (int i = 0; i <= segs; ++i) {   // inner arc a1 -> a0
    const float a = rad(a1_deg + (a0_deg - a1_deg) * i / segs);
    pts.emplace_back(std::cos(a) * (r_mid - half), std::sin(a) * (r_mid - half));
  }
  appendCap(pts, true, a0_deg, r_mid, half, cap);
  // Qt 5.12 on the device has no QVector(iterator, iterator) constructor
  QPolygonF poly;
  poly.reserve(static_cast<int>(pts.size()));
  for (const QPointF &pt : pts) poly << pt;
  return poly;
}

float SteeringArc::angleUtilization(float curvature, float desired_curvature, float v_ego, float roll, float max_lat_accel) {
  const float actual = curvature * v_ego * v_ego;
  const float desired = desired_curvature * v_ego * v_ego;
  const float roll_compensation = roll * GRAVITY * interp(v_ego, 5.0f, 15.0f, 0.0f, 1.0f);
  const float max_lat = max_lat_accel > 0.1f ? max_lat_accel : DEFAULT_MAX_LAT_ACCEL;
  return std::clamp((actual - roll_compensation + (desired - actual)) / max_lat, -1.0f, 1.0f);
}

float SteeringArc::humanUtilization(float curvature, float steering_angle_deg, float v_ego, float roll, float max_lat_accel,
                                    float *lat_weight) {
  const float lat = angleUtilization(curvature, curvature, v_ego, roll, max_lat_accel);
  const float angle = std::clamp(-steering_angle_deg / HUMAN_FULL_ANGLE_DEG, -1.0f, 1.0f);
  const float w = interp(v_ego, HUMAN_ANGLE_SPEED_LO, HUMAN_ANGLE_SPEED_HI, 0.0f, 1.0f);
  if (lat_weight) *lat_weight = w;
  return std::clamp(w * lat + (1.0f - w) * angle, -1.0f, 1.0f);
}

QColor SteeringArc::humanAngleColor(float a) {
  static const QColor BLUE(90, 205, 255), ORANGE_A(255, 140, 0), ORANGE_DEEP(255, 80, 0), HOT_PINK(255, 20, 147);
  static const float PINK_START = 360.0f;
  if (a <= 120.0f) return BLUE;
  if (a <= 180.0f) return blend(BLUE, ORANGE_A, (a - 120.0f) / 60.0f);
  if (a <= PINK_START) return blend(ORANGE_A, ORANGE_DEEP, (a - 180.0f) / (PINK_START - 180.0f));
  return blend(ORANGE_DEEP, HOT_PINK, std::min(1.0f, (a - PINK_START) / (LOCK_DEG - PINK_START)));
}

void SteeringArc::update(const Inputs &in) {
  // Driver source while lateral is off or the driver is overriding; switch only after the request has held 0.3 s
  const bool want_human = !in.lat_active || in.override_;
  if (want_human != human_) {
    if (++human_frames_ >= SOURCE_HYSTERESIS_FRAMES) {
      human_ = want_human;
      human_frames_ = 0;
    }
  } else {
    human_frames_ = 0;
  }

  const float target = human_ ? std::clamp(in.human, -1.0f, 1.0f) : (in.lat_active ? std::clamp(in.op, -1.0f, 1.0f) : 0.0f);
  torque_ = (1.0f - FILTER_K) * torque_ + FILTER_K * target;
  const float op = in.lat_active ? std::clamp(in.op, -1.0f, 1.0f) : 0.0f;
  op_target_ = (1.0f - FILTER_K) * op_target_ + FILTER_K * op;
  alpha_ = (1.0f - FILTER_K) * alpha_ + FILTER_K * (in.active ? 1.0f : 0.55f);
  human_orange_ = std::clamp(in.human_lat_weight, 0.0f, 1.0f);
  angle_ = (1.0f - FILTER_K) * angle_ + FILTER_K * std::fabs(in.steering_angle_deg);
  show_tick_ = human_ && in.lat_active && in.override_;
  engaged_colors_ = in.engaged_colors;
  experimental_ = in.experimental;
}

QRectF SteeringArc::tapRect(const QRect &view) const {
  const float r_mid = RADIUS + THICKNESS_HI / 2;
  const float hw = r_mid * std::sin(rad(SPAN_DEG / 2)) + 30;
  const float h = OFFSET_HI + THICKNESS_HI + 40;   // top of the bar at full lock plus margin, down to the view bottom
  const float cx = view.left() + view.width() / 2.0f;
  return QRectF(cx - hw, view.bottom() + 1 - h, 2 * hw, h);
}

void SteeringArc::paint(QPainter &p, const QRect &view) const {
  const float s = SCALE;
  const float at = std::fabs(torque_);
  const float alpha = std::max(alpha_, 0.05f);
  const float offset = interp(at, 0.5f, 1.0f, OFFSET_LO, OFFSET_HI);
  const float thickness = interp(at, 0.5f, 1.0f, THICKNESS_LO, THICKNESS_HI);
  const float radius = RADIUS;
  const float mid_r = radius + thickness / 2;
  const float cx = view.left() + view.width() / 2.0f;
  const float cy = view.bottom() + 1 + radius - offset;
  const float top = -90.0f;
  const QColor base = experimental_ ? PURPLE : WHITE;

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  p.translate(cx, cy);

  // track (+ a faint dark edge so it reads over a bright path)
  const QPolygonF bg = arcBarPolygon(mid_r, thickness, top - SPAN_DEG / 2, top + SPAN_DEG / 2, 7 * s);
  float bg_alpha = interp(at, 0.5f, 1.0f, 0.25f, 0.5f) * alpha;
  if (!engaged_colors_ && !human_) bg_alpha = 0.15f * alpha;
  if (human_ && alpha_ < 0.9f) bg_alpha = 0.15f + 0.1f * std::max(0.0f, at - 0.5f) * 2.0f;   // dim track while disengaged
  // soft dark halo (stacked round-joined strokes stand in for the mockup's blur)
  p.setBrush(Qt::NoBrush);
  for (const int w : {28, 24, 20, 16, 12, 8, 4}) {
    p.setPen(QPen(QColor(0, 0, 0, int(9 * alpha)), w, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin));
    p.drawPolygon(bg);
  }
  p.setPen(Qt::NoPen);
  p.setBrush(withAlpha(WHITE, bg_alpha));
  p.drawPolygon(bg);

  // fill from the centre toward the steer side (negative = left)
  if (at > 0.005f) {
    const QPolygonF fill = arcBarPolygon(mid_r, thickness, top, top + SPAN_DEG / 2 * torque_, 7 * s);
    QColor c0, c1;
    if (human_) {
      // v5: driver steering coloured by wheel angle (blue -> orange -> deep orange -> hot pink at full lock)
      c0 = c1 = withAlpha(humanAngleColor(angle_), 0.95f);
    } else {
      const float k = std::max(0.0f, at - 0.75f) * 4.0f;
      c0 = blend(withAlpha(base, 0.9f * alpha), withAlpha(experimental_ ? PURPLE : YELLOW, alpha), k);
      c1 = blend(withAlpha(base, 0.9f * alpha), withAlpha(ORANGE, alpha), k);
      if (!engaged_colors_) c0 = c1 = withAlpha(WHITE, 0.35f * alpha);
    }
    const qreal edge = torque_ < 0 ? bg.boundingRect().left() : bg.boundingRect().right();
    QLinearGradient g(0, 0, edge * 0.65, 0);
    g.setColorAt(0.0, c0);
    g.setColorAt(1.0, c1);
    p.setBrush(g);
    p.drawPolygon(fill);
  }

  // override: thin white tick across the bar where openpilot wants to be
  if (show_tick_) {
    const float a = rad(top + SPAN_DEG / 2 * std::clamp(op_target_, -1.0f, 1.0f));
    const float r0 = radius - 11, r1 = radius + thickness + 11;
    p.setPen(QPen(QColor(0, 0, 0, 110), 10, Qt::SolidLine, Qt::RoundCap));
    p.drawLine(QPointF(std::cos(a) * r0, std::sin(a) * r0), QPointF(std::cos(a) * r1, std::sin(a) * r1));
    p.setPen(QPen(QColor(255, 255, 255, 245), 6, Qt::SolidLine, Qt::RoundCap));
    p.drawLine(QPointF(std::cos(a) * r0, std::sin(a) * r0), QPointF(std::cos(a) * r1, std::sin(a) * r1));
    p.setPen(Qt::NoPen);
  }

  // centre dot
  if (at < 0.5f) {
    const float dot_y = -radius - thickness / 2;   // on the centreline: screen y = bottom - offset - thickness / 2
    p.setBrush(withAlpha(DOT_GREY, 0.9f * alpha));
    p.drawEllipse(QPointF(0, dot_y), 5 * s, 5 * s);
  }
  p.restore();
}
