#pragma once

#include <QElapsedTimer>
#include <QPixmap>
#include <QWidget>

#include "frogpilot/ui/qt/onroad/upload_rate.h"

// Offroad idle face, shown when the offroad screen timeout fires.
// Default off. File toggle /data/params_bogpilot/OffroadScreensaver
// ("1"/"true"/"on" enables; missing file, "0", "false", or "off" leaves the screen blank).
// July's prebuilt params_pyx.so rejects new Params keys, so this is not a Params key.
//
// Pending uploads bounce the same realdata speed (Mbps) and PENDING backlog the offroad
// upload card shows (frogpilot/ui/qt/onroad/upload_rate.h, read by selfdrive/ui/qt/widgets/wifi.cc).
// When that card would say "Up to date", the bounce switches to the BogPilot GitHub mark
// (frogpilot/assets/bogpilot_theme/logo.png) with those words.

namespace bogpilot_screensaver {
constexpr const char TOGGLE_KEY[] = "OffroadScreensaver";
}

class OffroadScreensaver : public QWidget {
  Q_OBJECT

public:
  explicit OffroadScreensaver(QWidget *parent = nullptr);

public slots:
  // True when the offroad timeout should switch to this face. The window shows it.
  bool onInteractiveTimeout();
  void dismiss();

signals:
  void dismissed();

protected:
  void showEvent(QShowEvent *event) override;
  void hideEvent(QHideEvent *event) override;
  void resizeEvent(QResizeEvent *event) override;
  void paintEvent(QPaintEvent *event) override;

private:
  bool refreshSample();
  void relayout();
  void tick();
  QRect contentRect() const;

  QTimer *sampleTimer = nullptr;
  QTimer *animTimer = nullptr;
  QElapsedTimer clock;

  QPixmap logo;
  QPixmap logoScaled;
  bogpilot_uprate::Sample sample;
  qint64 ageS = 0;
  bool caughtUp = false;
  bool rateShown = false;
  bool showPending = false;
  bool stale = false;
  int toggleTicks = 0;

  float x = 40.f;
  float y = 40.f;
  float vx = 260.f;
  float vy = 150.f;
  int contentW = 1;
  int contentH = 1;
};
