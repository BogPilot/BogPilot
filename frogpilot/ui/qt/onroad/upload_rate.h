#pragma once

// BogPilot TEMPORARY upload-rate readout (rlog uploader testing). Everything for it is behind BOGPILOT_UPLOAD_RATE:
// set it to 0 (or delete this header and the #if blocks in frogpilot_annotated_camera.*) to remove it.
//
// Data: the comma's rlogsync daemon (v3.1+) rewrites /dev/shm/bogpilot_rlogsync_rate every 2 s with one line
//   <epoch s> <total KB/s> <w1 KB/s> <w2 KB/s> <iface>
// The UI reads it from a 1 Hz timer (never from paint). Shown with '—' when older than 10 s, hidden after 60 s.
// Switch: /data/params_bogpilot/ShowUploadRate. No file = on; "0"/"false"/"off" = off. Re-read every 2 s.
// Drawn last in paintFrogPilotWidgets with no background fill (outlined text only) so the blind-spot lane fill
// stays visible underneath. Bottom-right corner, bottom-left with a right-hand-drive DM.

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
constexpr int BOX_W = 230, BOX_H = 90;
constexpr int STACK_GAP = 18;         // gap above a compass / weather / developer HUD box in the same corner

struct Sample {
  bool valid = false;
  qint64 epoch = 0;
  double total = 0, w1 = 0, w2 = 0;   // KB/s
  bool has_w1 = false, has_w2 = false;
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
  s.valid = true;
  return s;
}

inline Sample readFile(const QString &path = RATE_PATH) {
  QFile f(path);
  if (!f.open(QIODevice::ReadOnly)) return Sample();
  return parse(f.readLine(256));
}

inline QString mbs(double kbs, int decimals = 1) { return QString::number(kbs / 1024.0, 'f', decimals); }

// Area the readout covers (worker-line baseline at anchor.y(); descenders stay inside the 30 px edge inset)
inline QRect boxRect(const QPoint &anchor, bool rhd) {
  const int top = anchor.y() - BOX_H + 6;
  return rhd ? QRect(anchor.x(), top, BOX_W, BOX_H) : QRect(anchor.x() - BOX_W, top, BOX_W, BOX_H);
}

// age_s = now - sample epoch. Returns the box drawn (empty when nothing was drawn).
// anchor = bottom-right corner of the box (LHD) or bottom-left (rhd).
inline QRect paint(QPainter &p, const QPoint &anchor, bool rhd, const Sample &s, qint64 age_s, const QFont &small, const QFont &big) {
  if (!s.valid || age_s > HIDE_S || age_s < -HIDE_S) return QRect();
  const bool stale = age_s > STALE_S;

  const QString value = stale ? QStringLiteral("— MB/s") : mbs(s.total) + QStringLiteral(" MB/s");
  const QString arrow = QStringLiteral(" ↑");
  QString workers;
  if (!stale && (s.has_w1 || s.has_w2)) {
    workers = QStringLiteral("w1 %1 · w2 %2").arg(s.has_w1 ? mbs(s.w1) : QStringLiteral("–"), s.has_w2 ? mbs(s.w2) : QStringLiteral("–"));
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
  p.restore();

  return boxRect(anchor, rhd);
}

}  // namespace bogpilot_uprate

#endif  // BOGPILOT_UPLOAD_RATE
