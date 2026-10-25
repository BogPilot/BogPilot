#pragma once

#include "frogpilot/ui/qt/offroad/frogpilot_settings.h"

class FrogPilotVehiclesPanel : public FrogPilotListWidget {
  Q_OBJECT

public:
  explicit FrogPilotVehiclesPanel(FrogPilotSettingsWindow *parent);

signals:
  void openSubPanel();

protected:
  void showEvent(QShowEvent *event) override;

private:
  void updateState(const UIState &s);
  void updateTeslaSetup();
  void updateToggles();

  bool forceOpenDescriptions;
  bool started;

  std::map<QString, AbstractControl*> toggles;

  QSet<QString> gmKeys = {"ExperimentalGMTune", "LongPitch", "VoltSNG"};
  QSet<QString> hkgKeys = {"NewLongAPI", "TacoTuneHacks"};
  QSet<QString> hondaKeys = {"HondaAltTune", "HondaLowSpeedPedal", "HondaMaxBrake"};
  QSet<QString> longitudinalKeys = {"ExperimentalGMTune", "FrogsGoMoosTweak", "HondaAltTune", "HondaMaxBrake", "HondaLowSpeedPedal", "LongPitch", "NewLongAPI", "SNGHack", "SubaruSNG", "VoltSNG"};
  QSet<QString> subaruKeys = {"SubaruSNG"};
  QSet<QString> teslaKeys = {"EnableICIntegration", "RegenComfortBrake"};
  // Tesla car type selector ("TeslaCarProfile") and status rows; file-backed, shown for every Tesla.
  QSet<QString> teslaSetupKeys = {"TeslaDetectedProfile", "TeslaNAPImport", "TeslaPedalStatus", "TeslaProfileReason", "TeslaRadarStatus"};
  QSet<QString> toyotaKeys = {"ClusterOffset", "FrogsGoMoosTweak", "LockDoorsTimer", "SNGHack", "ToyotaDoors", "ToyotaDSUBypass"};
  QSet<QString> vehicleInfoKeys = {"BlindSpotSupport", "HardwareDetected", "OpenpilotLongitudinal", "PedalSupport", "RadarSupport", "SDSUSupport", "SNGSupport"};

  QSet<QString> parentKeys;

  FrogPilotSettingsWindow *parent;

  ParamControl *disableOpenpilotLong;
  ParamControl *forceFingerprint;

  FrogPilotButtonsControl *teslaCarTypeToggle;

  Params params;
  Params params_default{"/dev/shm/params_default"};

  QJsonObject frogpilotToggleLevels;

  QMap<QString, QString> carModels;
};
