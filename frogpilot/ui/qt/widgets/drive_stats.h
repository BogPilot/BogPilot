#pragma once

#include "selfdrive/ui/ui.h"

struct StatsLabels {
  QLabel *distance;
  QLabel *distance_unit;
  QLabel *hours;
  QLabel *routes;
};

class DriveStats : public QFrame {
  Q_OBJECT

public:
  explicit DriveStats(QWidget *parent = 0);

private:
  QLabel *addStatsLayouts(const QString &title, StatsLabels &labels, bool FrogPilot = false);
  QJsonObject localAllTime();
  QJsonObject localPastWeek();
  void showEvent(QShowEvent *event) override;
  void updateFrogPilotStatsForLabel(StatsLabels &labels);
  void updateStats();
  void updateStatsForLabel(const QJsonObject &obj, StatsLabels &labels);

  bool apiHasData = false;
  bool isMetric;
  bool konik;

  Params params;

  QJsonDocument stats;

  StatsLabels all;
  StatsLabels frogPilot;
  StatsLabels week;

  QLabel *allTitle;
  QLabel *weekTitle;

private slots:
  void parseResponse(const QString &response, bool success);
};
