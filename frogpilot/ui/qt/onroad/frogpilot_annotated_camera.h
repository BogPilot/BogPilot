#pragma once

#include <QJsonDocument>
#include <QJsonObject>

#include "frogpilot/ui/qt/onroad/bogpilot_redesign.h"
#include "frogpilot/ui/qt/onroad/developer_hud.h"
#include "selfdrive/ui/qt/onroad/buttons.h"
#include "selfdrive/ui/qt/widgets/cameraview.h"

const int widget_size = img_size + (UI_BORDER_SIZE / 2);

class FrogPilotAnnotatedCameraWidget : public QWidget {
  Q_OBJECT

public:
  explicit FrogPilotAnnotatedCameraWidget(QWidget *parent = 0);

  void paintAdjacentPaths(QPainter &p, const cereal::CarState::Reader &carState, const FrogPilotUIScene &frogpilot_scene, const QJsonObject &frogpilot_toggles);
  void paintBlindSpotPath(QPainter &p, const cereal::CarState::Reader &carState, const FrogPilotUIScene &frogpilot_scene);
  void paintFrogPilotWidgets(QPainter &p, UIState &s, FrogPilotUIState &fs, SubMaster &sm, SubMaster &fpsm, QJsonObject &frogpilot_toggles);
  void paintLeadMetrics(QPainter &p, bool adjacent, QPointF *chevron, const cereal::FrogPilotPlan::Reader &frogpilotPlan, const cereal::RadarState::LeadData::Reader &lead_data);
  void paintPathEdges(QPainter &p, const cereal::NavInstruction::Reader &navInstruction, const UIScene &scene, const FrogPilotUIScene &frogpilot_scene, SubMaster &sm);
  void paintRainbowPath(QPainter &p, QLinearGradient &bg, float lin_grad_point, SubMaster &sm);
  void updateState(const FrogPilotUIState &fs, const QJsonObject &frogpilot_toggles);

  bool redesign = false;    // BogPilot redesign layout (set by AnnotatedCameraWidget)
  bool bigMapOpen = false;  // BogPilot: was uninitialized; only set once a map widget exists
  bool developerHudEnabled = false;
  bool alertShowing = false;  // BogPilot: set from OnroadAlerts each update; hides the Developer HUD
  bool hideBottomIcons;
  bool isCruiseSet;
  bool mapButtonVisible;
  bool mutcdSpeedLimit;
  bool rightHandDM;
  bool viennaSpeedLimit;

  int alertHeight;
  int frogHopCount;
  int signMargin;
  int standstillDuration;

  float distanceConversion;
  float setSpeed;
  float speed;
  float speedConversion;
  float speedConversionMetrics;

  QColor blueColor(int alpha = 255) { return QColor(0, 0, 255, alpha); }
  QColor purpleColor(int alpha = 255) { return QColor(128, 0, 128, alpha); }

  QPoint dmIconPosition;
  QPoint experimentalButtonPosition;

  QPoint pendingLimitTopLeft;        // redesign: pending speed limit sign position
  QPoint speedLimitSourcesTopLeft;   // redesign: speed limit sources list position

  QRect leadTextRect;
  QRect newSpeedLimitRect;
  QRect setSpeedRect;
  QRect speedLimitRect;

  QSize defaultSize;

  QString leadDistanceUnit;
  QString leadSpeedUnit;
  QString signalStyle;
  QString speedLimitOffsetStr;
  QString speedUnit;

protected:
  void showEvent(QShowEvent *event) override;

private:
  void paintCEMStatus(QPainter &p, const cereal::FrogPilotPlan::Reader &frogpilotPlan, FrogPilotUIScene &frogpilot_scene, SubMaster &sm);
  void paintCompass(QPainter &p, QJsonObject &frogpilot_toggles);
  void paintCurveSpeedControl(QPainter &p, const cereal::FrogPilotPlan::Reader &frogpilotPlan);
  void paintDeveloperHUDPanel(QPainter &p, UIState &s, SubMaster &sm, SubMaster &fpsm, QJsonObject &frogpilot_toggles);
  bool developerHudShown(const FrogPilotUIScene &frogpilot_scene);
  int bottomCornerWidgetX();
  QPoint redesignStatusSlot(int index);
  int statusIconSize() const { return redesign ? bogpilot::STATUS_ICON : widget_size; }   // CEM / paused icon size
  int mapButtonShift();
  QRect developerHudPanelRect();
  void paintLateralPaused(QPainter &p, FrogPilotUIScene &frogpilot_scene);
  void paintLongitudinalPaused(QPainter &p, FrogPilotUIScene &frogpilot_scene);
  void paintPedalIcons(QPainter &p, const cereal::CarState::Reader &carState, const cereal::FrogPilotCarState::Reader &frogpilotCarState, FrogPilotUIScene &frogpilot_scene, QJsonObject &frogpilot_toggles);
  void paintPendingSpeedLimit(QPainter &p, const cereal::FrogPilotPlan::Reader &frogpilotPlan);
  void paintRadarTracks(QPainter &p, const cereal::ModelDataV2::Reader &model, UIState &s, FrogPilotUIScene &frogpilot_scene, SubMaster &sm, SubMaster &fpsm);
  void paintRoadName(QPainter &p);
  void paintSmartControllerTraining(QPainter &p, const cereal::FrogPilotPlan::Reader &frogpilotPlan);
  void paintSpeedLimitSources(QPainter &p, const cereal::FrogPilotCarState::Reader &frogpilotCarState, const cereal::FrogPilotNavigation::Reader &frogpilotNavigation, const cereal::FrogPilotPlan::Reader &frogpilotPlan);
  void paintStandstillTimer(QPainter &p);
  void paintStoppingPoint(QPainter &p, UIScene &scene, FrogPilotUIScene &frogpilot_scene, QJsonObject &frogpilot_toggles);
  void paintTurnSignals(QPainter &p, const cereal::CarState::Reader &carState);
  void paintWeather(QPainter &p, const cereal::FrogPilotPlan::Reader &frogpilotPlan, FrogPilotUIScene &frogpilot_scene);
  void updateSignals();

  int animationFrameIndex;
  bool hudShown = false;
  int developerHudCheckFrame = 0;
  int signalAnimationLength;
  int signalHeight;
  int signalMovement;
  int signalWidth;
  int totalFrames;

  Params params;
  Params params_memory{"/dev/shm/params"};

  QColor blackColor(int alpha = 255) { return QColor(0, 0, 0, alpha); }
  QColor greenColor(int alpha = 242) { return QColor(23, 134, 68, alpha); }
  QColor redColor(int alpha = 255) { return QColor(201, 34, 49, alpha); }
  QColor whiteColor(int alpha = 255) { return QColor(255, 255, 255, alpha); }

  QElapsedTimer glowTimer;
  QElapsedTimer pendingLimitTimer;
  QElapsedTimer standstillTimer;

  QPixmap brakePedalImg;
  QPixmap curveSpeedIcon;
  QPixmap dashboardIcon;
  QPixmap gasPedalImg;
  QPixmap mapDataIcon;
  QPixmap navigationIcon;
  QPixmap nextMapsIcon;
  QPixmap pausedIcon;
  QPixmap speedIcon;
  QPixmap stopSignImg;
  QPixmap turnIcon;

  QPoint cemStatusPosition;
  QPoint compassPosition;
  QPoint lateralPausedPosition;

  QSharedPointer<QMovie> cemCurveIcon;
  QSharedPointer<QMovie> cemLeadIcon;
  QSharedPointer<QMovie> cemSpeedIcon;
  QSharedPointer<QMovie> cemStopIcon;
  QSharedPointer<QMovie> cemTurnIcon;
  QSharedPointer<QMovie> chillModeIcon;
  QSharedPointer<QMovie> experimentalModeIcon;
  QSharedPointer<QMovie> weatherClearDay;
  QSharedPointer<QMovie> weatherClearNight;
  QSharedPointer<QMovie> weatherLowVisibility;
  QSharedPointer<QMovie> weatherRain;
  QSharedPointer<QMovie> weatherSnow;

  QString cscSpeedStr;

  QTimer *animationTimer;

  QVector<QPixmap> blindspotImages;
  QVector<QPixmap> signalImages;
};
