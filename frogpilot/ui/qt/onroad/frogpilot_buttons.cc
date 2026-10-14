#include "frogpilot/ui/qt/onroad/frogpilot_buttons.h"

#include <QMouseEvent>

#include "frogpilot/ui/qt/onroad/bogpilot_redesign.h"

DistanceButton::DistanceButton(QWidget *parent) : QPushButton(parent) {
  setFixedSize(btn_size + UI_BORDER_SIZE, btn_size);

  QObject::connect(frogpilotUIState(), &FrogPilotUIState::themeUpdated, this, &DistanceButton::updateTheme);
  // The redesign pill selects a personality directly in mouseReleaseEvent instead of emulating the distance button
  QObject::connect(this, &QPushButton::pressed, [this] {if (!redesign) params_memory.putBool("OnroadDistanceButtonPressed", true);});
  QObject::connect(this, &QPushButton::released, [this] {if (!redesign) params_memory.putBool("OnroadDistanceButtonPressed", false);});
}

void DistanceButton::setRedesign(bool on) {
  if (redesign == on) {
    return;
  }
  redesign = on;
  if (redesign) {
    setFixedSize(bogpilot::PILL_W, bogpilot::PILL_H + bogpilot::PILL_LABEL_SPACE);
  } else {
    setFixedSize(btn_size + UI_BORDER_SIZE, btn_size);
  }
  update();
}

QRectF DistanceButton::pillRect() const {
  return QRectF(0, bogpilot::PILL_LABEL_SPACE, bogpilot::PILL_W, bogpilot::PILL_H);
}

void DistanceButton::mouseReleaseEvent(QMouseEvent *event) {
  QPushButton::mouseReleaseEvent(event);
  if (!redesign) {
    return;
  }
  // Same param the Driving Personality setting and controlsd's distance-button handling write; controlsd re-reads it every 0.1 s
  // v5: the circle cycles Aggressive -> Standard -> Relaxed -> Aggressive
  const int selected = bogpilot::personalityAt(pillRect(), event->pos(), personality - 1);
  if (selected >= 0) {
    params.putNonBlocking("LongitudinalPersonality", std::to_string(selected));
    personality = selected + 1;  // show the new selection immediately; updateState confirms it from controlsState
    pending_personality = selected;
    pending_timer.start();
    update();
  }
}

void DistanceButton::showEvent(QShowEvent *event) {
  updateTheme();
}

void DistanceButton::updateTheme() {
  for (QMap<int, QPair<QPixmap, QSharedPointer<QMovie>>>::iterator it = icon_map.begin(); it != icon_map.end(); ++it) {
    QSharedPointer<QMovie> movie = it.value().second;
    if (!movie.isNull()) {
      QObject::disconnect(movie.data(), nullptr, this, nullptr);
      movie->stop();
    }
  }

  icon_map.clear();

  QPixmap traffic_img, aggressive_img, standard_img, relaxed_img;
  QSharedPointer<QMovie> traffic_gif, aggressive_gif, standard_gif, relaxed_gif;

  loadImage("../../frogpilot/assets/active_theme/distance_icons/traffic", traffic_img, traffic_gif, QSize(btn_size, btn_size), this);
  loadImage("../../frogpilot/assets/active_theme/distance_icons/aggressive", aggressive_img, aggressive_gif, QSize(btn_size, btn_size), this);
  loadImage("../../frogpilot/assets/active_theme/distance_icons/standard", standard_img, standard_gif, QSize(btn_size, btn_size), this);
  loadImage("../../frogpilot/assets/active_theme/distance_icons/relaxed", relaxed_img, relaxed_gif, QSize(btn_size, btn_size), this);

  icon_map.insert(0, qMakePair(traffic_img, traffic_gif));
  icon_map.insert(1, qMakePair(aggressive_img, aggressive_gif));
  icon_map.insert(2, qMakePair(standard_img, standard_gif));
  icon_map.insert(3, qMakePair(relaxed_img, relaxed_gif));
}

void DistanceButton::updateState(const UIScene &scene, const FrogPilotUIScene &frogpilot_scene) {
  // Hold a tapped selection until controlsd reports it (or 1.5 s pass) so the pill does not flick back
  if (pending_personality >= 0) {
    if (static_cast<int>(scene.personality) != pending_personality && pending_timer.elapsed() < 1500) {
      return;
    }
    pending_personality = -1;
  }

  bool state_changed = (traffic_mode_active != frogpilot_scene.traffic_mode_enabled) ||
                       (personality != static_cast<int>(scene.personality) + 1 && !traffic_mode_active);

  if (!state_changed) {
    return;
  }

  personality = static_cast<int>(scene.personality) + 1;
  traffic_mode_active = frogpilot_scene.traffic_mode_enabled;

  update();
}

void DistanceButton::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  if (redesign) {
    static const QStringList names = {tr("Aggressive"), tr("Standard"), tr("Relaxed")};
    const int selected = std::clamp(personality - 1, 0, 2);
    bogpilot::paintPersonalityPill(p, pillRect(), selected, traffic_mode_active, traffic_mode_active ? tr("Traffic") : names[selected]);
    return;
  }

  QPair<QPixmap, QSharedPointer<QMovie>> icon = icon_map.value(traffic_mode_active ? 0 : personality);
  QPixmap img = icon.first;
  QMovie *gif = icon.second.data();

  drawIcon(p, rect().center() + QPoint(UI_BORDER_SIZE / 2, 0), gif ? gif->currentPixmap() : img, Qt::transparent, 1.0);
}
