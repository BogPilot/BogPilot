#pragma once

#include <QElapsedTimer>
#include <QPixmap>
#include <QWidget>

#include "frogpilot/ui/qt/onroad/upload_rate.h"

// Offroad idle face, shown when the offroad screen timeout fires.
// Default OFF on AGNOS 17.2. File toggle /data/params_bogpilot/OffroadScreensaver
// ("1"/"true"/"on" shows the screensaver; "0"/"false"/"off", a missing file, or anything else
// leaves the screen blank). An explicit "1" written by the settings switch turns it on.
// July's prebuilt params_pyx.so rejects new Params keys, so this is not a Params key.
//
// Pending uploads bounce the same realdata speed (Mbps) and PENDING backlog the offroad
// upload card shows (frogpilot/ui/qt/onroad/upload_rate.h, read by selfdrive/ui/qt/widgets/wifi.cc).
// When nothing is left to upload, the bounce switches to the BogPilot logo
// (frogpilot/assets/bogpilot_theme/logo.png, mark plus "BogPilot" wordmark), no caption.

namespace bogpilot_screensaver {
constexpr const char TOGGLE_KEY[] = "OffroadScreensaver";
constexpr bool TOGGLE_DEFAULT = false;  // missing/unknown file = off; must match device_settings.cc
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
  bool upToDateText = false;  // BogPilot: caption only when the card says "Up to date"
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
