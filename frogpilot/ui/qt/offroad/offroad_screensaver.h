#pragma once

#include <QElapsedTimer>
#include <QPixmap>
#include <QWidget>

#include "frogpilot/ui/qt/onroad/upload_rate.h"

// Offroad idle face, shown when the offroad screen timeout fires.
// Default ON. File toggle /data/params_bogpilot/OffroadScreensaver
// ("0"/"false"/"off" leaves the screen blank; "1"/"true"/"on", a missing file, or anything else
// shows the screensaver). An explicit "0" written by the settings switch keeps it off.
// July's prebuilt params_pyx.so rejects new Params keys, so this is not a Params key.
//
// Pending uploads bounce the same realdata speed (Mbps) and PENDING backlog the offroad
// upload card shows (frogpilot/ui/qt/onroad/upload_rate.h, read by selfdrive/ui/qt/widgets/wifi.cc).
// When nothing is left to upload, the bounce switches to the BogPilot road mark
// (selfdrive/assets/img_spinner_comma.png), with no caption.

namespace bogpilot_screensaver {
constexpr const char TOGGLE_KEY[] = "OffroadScreensaver";
constexpr bool TOGGLE_DEFAULT = true;  // missing/unknown file = on; must match device_settings.cc
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
