#pragma once

#include <QPushButton>

#include "selfdrive/ui/ui.h"

const int btn_size = 192;
const int img_size = (btn_size / 4) * 3;

class ExperimentalButton : public QPushButton {
  Q_OBJECT

public:
  explicit ExperimentalButton(QWidget *parent = 0);
  void updateState(const UIState &s, const FrogPilotUIState &fs, const QJsonObject &frogpilot_toggles);
  void changeMode();  // BogPilot: public so the redesign's steering arc tap can toggle Experimental Mode

private:
  void paintEvent(QPaintEvent *event) override;

  Params params;
  QPixmap engage_img;
  QPixmap experimental_img;
  bool experimental_mode;
  bool engageable;

  // FrogPilot variables
  void showEvent(QShowEvent *event) override;
  void updateBackgroundColor();
  void updateTheme();

  bool use_stock_wheel;

  int steering_angle;

  Params params_memory{"/dev/shm/params"};

  QColor background_color;

  QPixmap wheel_img;

  QSharedPointer<QMovie> wheel_gif;
};


class MapSettingsButton : public QPushButton {
  Q_OBJECT

public:
  explicit MapSettingsButton(QWidget *parent = 0);

  // FrogPilot variables
  bool road_name_ui;

  // BogPilot redesign: 150 px round button, directions icon at 58 % of the button
  void setRedesign(bool on);

private:
  void paintEvent(QPaintEvent *event) override;

  QPixmap settings_img;

  bool redesign = false;
  QPixmap redesign_img;
};

void drawIcon(QPainter &p, const QPoint &center, const QPixmap &img, const QBrush &bg, float opacity, const int &angle = 0);
