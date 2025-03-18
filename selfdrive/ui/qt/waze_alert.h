#pragma once

#include <memory>
#include <QWidget>
#include <QPainter>
#include <QLabel>
#include <QVBoxLayout>
#include <QTimer>

#include "selfdrive/ui/ui.h"

class WazeAlertOverlay : public QWidget {
  Q_OBJECT

public:
  explicit WazeAlertOverlay(QWidget *parent = nullptr);
  void updateState(const UIState &s);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  void updateAlertVisibility();

  bool alertVisible = false;
  QString alertTitle;
  QString alertText;
  QString alertType;
  QString alertSubType;
  float alertDistance = 0.0;
  QTimer *dismissTimer;
  QTimer *animationTimer;
  int animationStep = 0;

  // Animation parameters
  static const int ANIMATION_STEPS = 10;
  static const int ANIMATION_DURATION_MS = 300;

  // Alert display parameters
  static const int ALERT_DURATION_MS = 10000;  // 10 seconds

  // These parameters are now directly set in the paintEvent function
  // to match the style of regular alerts in alerts.cc
};
