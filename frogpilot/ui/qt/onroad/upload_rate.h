#pragma once

// BogPilot TEMPORARY upload-rate readout (rlog uploader testing). Everything for it is behind BOGPILOT_UPLOAD_RATE:
// set it to 0 (or delete this header and the #if blocks in frogpilot_annotated_camera.*) to remove it.
//
// Data: the comma's rlogsync daemon (v3.1+) rewrites /dev/shm/bogpilot_rlogsync_rate every 2 s with one line
//   <epoch s> <total KB/s> <w1 KB/s> <w2 KB/s> <iface>
// v3.2 appends the upload backlog: <pending bytes> <pending segments> <trend B/min | -> <scan epoch>
// A 5-field (v3.1) line shows only the speed; the PENDING block appears once the extra fields are there.
// The UI reads it from a 1 Hz timer (never from paint). Shown with '—' when older than 10 s, hidden after 60 s.
// Switch: /data/params_bogpilot/ShowUploadRate. No file = on; "0"/"false"/"off" = off. Re-read every 2 s.
// The same switch controls the PENDING block (left of the speed, right of it with a right-hand-drive DM).
// Drawn last in paintFrogPilotWidgets with no background fill (outlined text only) so the blind-spot lane fill
// stays visible underneath. Bottom-right corner, bottom-left with a right-hand-drive DM.
// Speeds are shown in Mbps (decimal megabits/s, like an internet plan); backlog sizes stay MB/GB.
// The offroad home card (selfdrive/ui/qt/widgets/wifi.cc, page 4) reads the same file through these helpers.

#ifndef BOGPILOT_UPLOAD_RATE
#define BOGPILOT_UPLOAD_RATE 1
#endif

#if BOGPILOT_UPLOAD_RATE

#include <QByteArray>
#include <QColor>
#include <QFile>
#include <QFont>
#include <QFontMetrics>
#include <QList>
#include <QPainter>
#include <QPainterPath>
#include <QRect>
#include <QString>

namespace bogpilot_uprate {

constexpr const char *RATE_PATH = "/dev/shm/bogpilot_rlogsync_rate";
constexpr const char *TOGGLE_PATH = "/data/params_bogpilot/ShowUploadRate";
constexpr int STALE_S = 10;           // older: value shows '—'
constexpr int HIDE_S = 60;            // older: not drawn
constexpr int READ_INTERVAL_MS = 1000;
constexpr int BOX_W = 280, BOX_H = 90;   // fits "88.8 Mbps ↑" (281 px at Inter 44 Bold)
constexpr int STACK_GAP = 18;         // gap above a compass / weather / developer HUD box in the same corner
constexpr int PEND_W = 250, PEND_GAP = 44;   // PENDING block width, gap to the speed block
constexpr double TREND_FLAT_BPM = 0.5 * 1048576.0;   // |trend| below this (B/min) = flat

struct Sample {
  bool valid = false;
  qint64 epoch = 0;
  double total = 0, w1 = 0, w2 = 0;   // KB/s
  bool has_w1 = false, has_w2 = false;
  bool has_pending = false, has_trend = false;   // v3.2 fields
  qint64 pending_bytes = 0, pending_segs = 0, scan_epoch = 0;
  double trend = 0;                    // backlog change, B/min (< 0 = draining)
};

// Missing file = on, same rule as the other default-on /data/params_bogpilot switches
inline bool toggleEnabled(const QString &path = TOGGLE_PATH) {
  QFile f(path);
  if (!f.open(QIODevice::ReadOnly)) return true;
  const QByteArray t = f.read(16).trimmed().toLower();
  return !(t == "0" || t == "false" || t == "off");
}

// "<epoch> <total> <w1> <w2> <iface>"; w1/w2 may be "-" when that worker is idle
inline Sample parse(const QByteArray &line) {
  Sample s;
  const QList<QByteArray> f = line.simplified().split(' ');
  if (f.size() < 2) return s;
  bool ok_e = false, ok_t = false;
  s.epoch = f[0].toLongLong(&ok_e);
  s.total = f[1].toDouble(&ok_t);
  if (!ok_e || !ok_t || s.epoch <= 0 || s.total < 0) return s;
  if (f.size() > 2) s.w1 = f[2].toDouble(&s.has_w1);
  if (f.size() > 3) s.w2 = f[3].toDouble(&s.has_w2);
  if (f.size() > 6) {
    bool ok_b = false, ok_n = false;
    const qint64 b = f[5].toLongLong(&ok_b), n = f[6].toLongLong(&ok_n);
    if (ok_b && ok_n && b >= 0 && n >= 0) {
      s.has_pending = true;
      s.pending_bytes = b;
      s.pending_segs = n;
      if (f.size() > 7) s.trend = f[7].toDouble(&s.has_trend);   // "-" = not enough history yet
      if (f.size() > 8) s.scan_epoch = f[8].toLongLong();
    }
  }
  s.valid = true;
  return s;
}

// 4.8 MB / 640 MB / 4.8 GB / 20 GB
inline QString sizeText(qint64 bytes) {
  const double mb = bytes / 1048576.0;
  if (mb < 10) return QString::number(mb, 'f', 1) + QStringLiteral(" MB");
  if (mb < 1024) return QString::number(qRound(mb)) + QStringLiteral(" MB");
  const double gb = mb / 1024.0;
  return (gb < 10 ? QString::number(gb, 'f', 1) : QString::number(qRound(gb))) + QStringLiteral(" GB");
}

// ETA < 1 min / ETA 41 min / ETA 1 h 25 min / ETA > 24 h
inline QString etaText(double minutes) {
  if (minutes < 1) return QStringLiteral("ETA < 1 min");
  const int m = qRound(minutes);
  if (m < 60) return QStringLiteral("ETA %1 min").arg(m);
  if (m >= 24 * 60) return QStringLiteral("ETA > 24 h");
  return QStringLiteral("ETA %1 h %2 min").arg(m / 60).arg(m % 60);
}

enum class Backlog { None, CaughtUp, Draining, Growing, Flat, NoTrend };

inline Backlog backlogState(const Sample &s) {
  if (!s.valid || !s.has_pending) return Backlog::None;
  if (s.pending_bytes == 0) return Backlog::CaughtUp;
  if (!s.has_trend) return Backlog::NoTrend;
  if (s.trend < -TREND_FLAT_BPM) return Backlog::Draining;
  if (s.trend > TREND_FLAT_BPM) return Backlog::Growing;
  return Backlog::Flat;
}

// Sub-line under the PENDING value and its colour
inline QString pendingSubLine(const Sample &s, QColor *color = nullptr) {
  const QColor dim(255, 255, 255, 190), green(61, 220, 151, 255), amber(255, 196, 60, 255);
  const QString segs = QStringLiteral("%1 seg").arg(s.pending_segs);
  QString t;
  QColor c = dim;
  switch (backlogState(s)) {
    case Backlog::CaughtUp: t = QStringLiteral("all sent"); c = green; break;
    case Backlog::Draining: t = etaText(s.pending_bytes / -s.trend); c = green; break;
    case Backlog::Growing: t = segs + QStringLiteral(" · growing"); c = amber; break;
    case Backlog::Flat: case Backlog::NoTrend: t = segs; break;
    case Backlog::None: break;
  }
  if (color) *color = c;
  return t;
}

inline Sample readFile(const QString &path = RATE_PATH) {
  QFile f(path);
  if (!f.open(QIODevice::ReadOnly)) return Sample();
  return parse(f.readLine(256));
}

// The rate file's KB/s is KiB/s (bytes / 1024). Shown as decimal megabits/s: x 1024 x 8 / 1e6.
inline double toMbps(double kbs) { return kbs * 1024.0 * 8.0 / 1e6; }

// 0.0 .. 99.9 with one decimal, 100 and up as whole numbers (so "888 Mbps" is no wider than "88.8 Mbps")
inline QString mbps(double kbs) {
  const double v = toMbps(kbs);
  return v < 99.95 ? QString::number(v, 'f', 1) : QString::number(qRound(v));
}

// Drawn at all: valid sample no older (or newer, clock skew) than HIDE_S
inline bool fresh(const Sample &s, qint64 age_s) { return s.valid && age_s <= HIDE_S && age_s >= -HIDE_S; }

// Area the readout covers (worker-line baseline at anchor.y(); descenders stay inside the 30 px edge inset)
inline QRect boxRect(const QPoint &anchor, bool rhd) {
  const int top = anchor.y() - BOX_H + 6;
  return rhd ? QRect(anchor.x(), top, BOX_W, BOX_H) : QRect(anchor.x() - BOX_W, top, BOX_W, BOX_H);
}

// PENDING block, beside the speed block on the side away from the corner
inline QRect pendingRect(const QPoint &anchor, bool rhd) {
  const int top = anchor.y() - BOX_H + 6;
  return rhd ? QRect(anchor.x() + BOX_W + PEND_GAP, top, PEND_W, BOX_H) : QRect(anchor.x() - BOX_W - PEND_GAP - PEND_W, top, PEND_W, BOX_H);
}

// Both blocks, for stacking above corner widgets
inline QRect readoutRect(const QPoint &anchor, bool rhd, bool pending) {
  return pending ? boxRect(anchor, rhd).united(pendingRect(anchor, rhd)) : boxRect(anchor, rhd);
}

// age_s = now - sample epoch. Returns the box drawn (empty when nothing was drawn).
// anchor = bottom-right corner of the box (LHD) or bottom-left (rhd).
inline QRect paint(QPainter &p, const QPoint &anchor, bool rhd, const Sample &s, qint64 age_s, const QFont &small, const QFont &big) {
  if (!fresh(s, age_s)) return QRect();
  const bool stale = age_s > STALE_S;

  const QString value = stale ? QStringLiteral("— Mbps") : mbps(s.total) + QStringLiteral(" Mbps");
  const QString arrow = QStringLiteral(" ↑");
  QString workers;
  if (!stale && (s.has_w1 || s.has_w2)) {
    workers = QStringLiteral("w1 %1 · w2 %2").arg(s.has_w1 ? mbps(s.w1) : QStringLiteral("–"), s.has_w2 ? mbps(s.w2) : QStringLiteral("–"));
  }

  // baselines match the approved mockup: title 66 px, value 26 px above the bottom (worker line) baseline
  const int y_workers = anchor.y();
  const int y_value = anchor.y() - 26;
  const int y_title = anchor.y() - 66;

  p.save();
  p.setRenderHint(QPainter::Antialiasing);
  const QPen outline(QColor(0, 0, 0, 200), 6, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);
  auto text = [&](const QString &t, const QFont &font, int x_edge, int y, const QColor &c) {
    const int w = QFontMetrics(font).horizontalAdvance(t);
    const int x = rhd ? x_edge : x_edge - w;
    QPainterPath path;
    path.setFillRule(Qt::WindingFill);   // Inter's overlapping contours ("4", "8") punch holes with the default odd-even fill
    path.addText(x, y, font, t);
    p.strokePath(path, outline);
    p.fillPath(path, c);
    return w;
  };
  const QColor white(255, 255, 255, 245), dim(255, 255, 255, 190), green(61, 220, 151, 255);

  text(QStringLiteral("UPLOAD"), small, anchor.x(), y_title, dim);
  if (rhd) {
    const int w = text(value, big, anchor.x(), y_value, white);
    text(arrow, big, anchor.x() + w, y_value, stale ? dim : green);
  } else {
    const int w = text(arrow, big, anchor.x(), y_value, stale ? dim : green);
    text(value, big, anchor.x() - w, y_value, white);
  }
  if (!workers.isEmpty()) {
    text(workers, small, anchor.x(), y_workers, dim);
  }

  QRect drawn = boxRect(anchor, rhd);
  if (s.has_pending) {
    const QRect pr = pendingRect(anchor, rhd);
    const int edge = rhd ? pr.left() : pr.right() + 1;
    text(QStringLiteral("PENDING"), small, edge, y_title, dim);
    text(stale ? QStringLiteral("— GB") : (s.pending_bytes == 0 ? QStringLiteral("0 MB") : sizeText(s.pending_bytes)), big, edge, y_value, white);
    if (!stale) {
      QColor c;
      const QString sub = pendingSubLine(s, &c);
      if (!sub.isEmpty()) text(sub, small, edge, y_workers, c);
    }
    drawn = drawn.united(pr);
  }
  p.restore();

  return drawn;
}

}  // namespace bogpilot_uprate

#endif  // BOGPILOT_UPLOAD_RATE
