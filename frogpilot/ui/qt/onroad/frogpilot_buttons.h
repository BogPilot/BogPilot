#pragma once

#include <QElapsedTimer>
#include <QMovie>

#include "selfdrive/ui/qt/onroad/buttons.h"

class DistanceButton : public QPushButton {
  Q_OBJECT

public:
  explicit DistanceButton(QWidget *parent = 0);

  void updateState(const UIScene &scene, const FrogPilotUIScene &frogpilot_scene);

  // BogPilot redesign: segmented personality pill (Aggressive | Standard | Relaxed); a tap selects that personality
  void setRedesign(bool on);
  bool redesignActive() const { return redesign; }

private:
  void mouseReleaseEvent(QMouseEvent *event) override;
  bool hitButton(const QPoint &pos) const override;   // redesign: only the circle, not the label above it
  void paintEvent(QPaintEvent *event) override;
  QRectF pillRect() const;

  bool redesign = false;
  int pending_personality = -1;
  int livePersonality(const UIScene &scene);   // controlsState personality, or the AP1 stalk's named detent until it catches up
  float stalk_follow_s = 0.0f;                 // last carState.cruiseState.speedOffset seen (AP1 stalk follow seconds)
  int stalk_personality = -1;                  // personality from the last named stalk detent, -1 = none pending
  int stalk_cs_personality = -1;               // controlsState personality when that detent landed
  QElapsedTimer pending_timer;
  QElapsedTimer press_timer;   // hold length of the current press (Traffic Mode hold = 2.5 s)
  Params params;
  void showEvent(QShowEvent *event) override;
  void updateTheme();

  bool traffic_mode_active;

  int personality;

  Params params_memory{"/dev/shm/params"};

  QMap<int, QPair<QPixmap, QSharedPointer<QMovie>>> icon_map;
};
