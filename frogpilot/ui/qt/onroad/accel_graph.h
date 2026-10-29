#pragma once

// BogPilot accel graph: 10 s rolling plot of the longitudinal acceleration command
// (carControl.actuators.accel) against the measured acceleration (carState.aEgo). Plain QPainter, no cereal.

#include <deque>

#include <QPainter>
#include <QRectF>
#include <QString>

class AccelGraph {
public:
  explicit AccelGraph(int hz = 20, float seconds = 10.0f);

  void push(float cmd, float actual);
  void clear();
  int size() const { return int(cmd_.size()); }
  int capacity() const { return capacity_; }

  // Symmetric y range (m/s^2): 1.5 by default, widened in 0.5 steps to fit the data (max 4).
  float range() const;

  void paint(QPainter &p, const QRectF &panel) const;

private:
  int capacity_;
  float seconds_;
  std::deque<float> cmd_;
  std::deque<float> act_;
};
