#include "frogpilot/ui/qt/onroad/frogpilot_buttons.h"

#include <QMouseEvent>

#include <cmath>

#include "frogpilot/ui/qt/onroad/bogpilot_redesign.h"

DistanceButton::DistanceButton(QWidget *parent) : QPushButton(parent) {
  setFixedSize(btn_size + UI_BORDER_SIZE, btn_size);

  QObject::connect(frogpilotUIState(), &FrogPilotUIState::themeUpdated, this, &DistanceButton::updateTheme);
  // Both layouts emulate the distance button: controlsd steps the personality on a tap (Relaxed -> Standard ->
  // Aggressive, like the stalk) and frogpilot_card toggles Traffic Mode on a 2.5 s hold (no Experimental Mode)
  QObject::connect(this, &QPushButton::pressed, [this] {press_timer.start(); params_memory.putBool("OnroadDistanceButtonPressed", true);});
  QObject::connect(this, &QPushButton::released, [this] {params_memory.putBool("OnroadDistanceButtonPressed", false);});
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

bool DistanceButton::hitButton(const QPoint &pos) const {
  if (!redesign) {
    return QPushButton::hitButton(pos);
  }
  // Taps and holds count only inside the personality circle; the text label above it is not part of the button
  const QRectF c = pillRect();
  const QPointF d = QPointF(pos) - c.center();
  const qreal r = c.width() / 2.0;
  return d.x() * d.x() + d.y() * d.y() <= r * r;
}

void DistanceButton::mouseReleaseEvent(QMouseEvent *event) {
  const bool was_down = isDown();
  QPushButton::mouseReleaseEvent(event);
  if (!redesign || !was_down || !hitButton(event->pos())) {
    return;
  }
  // Preview only: controlsd writes LongitudinalPersonality. Skip after a Traffic Mode hold or while in Traffic Mode.
  if (traffic_mode_active || (press_timer.isValid() && press_timer.elapsed() >= 2500)) {
    return;
  }
  const int selected = bogpilot::personalityAt(pillRect(), event->pos(), personality - 1);
  if (selected >= 0) {
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

// AP1 stalk: the follow banner reads carState.cruiseState.speedOffset (stalk follow seconds) directly, while
// controlsState.personality only changes after frogpilot_card writes LongitudinalPersonality and controlsd re-reads
// it. A quick flick can leave that param path behind (icon stuck on Relaxed while the banner shows 1.25 s), so when
// the stalk lands on a named detent the icon shows that personality until controlsState changes (catches up or a tap).
int DistanceButton::livePersonality(const UIScene &scene) {
  const int cs_personality = static_cast<int>(scene.personality);
  const SubMaster &sm = *(uiState()->sm);
  if (sm.rcv_frame("carParams") > 0 && sm["carParams"].getCarParams().getCarFingerprint() == "TESLA_AP1_MODELS") {
    const float follow_s = sm["carState"].getCarState().getCruiseState().getSpeedOffset();
    if (std::abs(follow_s - stalk_follow_s) > 1e-3) {
      stalk_follow_s = follow_s;
      // Stalk default seconds per named detent (selfdrive/car/tesla/stalk_follow.py); in-between detents keep the icon
      static const float named_s[3] = {1.25f, 1.45f, 1.75f};  // Aggressive, Standard, Relaxed
      for (int i = 0; i < 3; i++) {
        if (std::abs(follow_s - named_s[i]) < 1e-3) {
          stalk_personality = i;
          stalk_cs_personality = cs_personality;
        }
      }
    }
  }
  if (stalk_personality >= 0) {
    if (cs_personality == stalk_personality || cs_personality != stalk_cs_personality) {
      stalk_personality = -1;  // controlsd agrees, or it moved (screen tap / param landed): it is the source again
    } else {
      return stalk_personality;
    }
  }
  return cs_personality;
}

void DistanceButton::updateState(const UIScene &scene, const FrogPilotUIScene &frogpilot_scene) {
  // Hold a tapped selection until controlsd reports it (or 1.5 s pass) so the pill does not flick back
  if (pending_personality >= 0) {
    if (static_cast<int>(scene.personality) != pending_personality && pending_timer.elapsed() < 1500) {
      return;
    }
    pending_personality = -1;
  }

  const int live_personality = livePersonality(scene);

  bool state_changed = (traffic_mode_active != frogpilot_scene.traffic_mode_enabled) ||
                       (personality != live_personality + 1 && !traffic_mode_active);

  if (!state_changed) {
    return;
  }

  personality = live_personality + 1;
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
