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
  void paintEvent(QPaintEvent *event) override;
  QRectF pillRect() const;

  bool redesign = false;
  int pending_personality = -1;
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
