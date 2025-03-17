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
  float alertDistance = 0.0;
  QTimer *dismissTimer;
  QTimer *animationTimer;
  int animationStep = 0;

  // Animation parameters
  static const int ANIMATION_STEPS = 10;
  static const int ANIMATION_DURATION_MS = 300;

  // Alert display parameters
  static const int ALERT_DURATION_MS = 10000;  // 10 seconds
  static const int ALERT_MARGIN = 30;
  static const int ALERT_RADIUS = 15;
  static const int TITLE_FONT_SIZE = 48;
  static const int TEXT_FONT_SIZE = 32;
  static const int DISTANCE_FONT_SIZE = 28;

  // Alert colors mapped by type
  const QMap<QString, QColor> alertColors = {
    {"POLICE", QColor(0, 0, 255, 200)},          // Blue
    {"ACCIDENT", QColor(255, 0, 0, 200)},        // Red
    {"HAZARD", QColor(255, 165, 0, 200)},        // Orange
    {"JAM", QColor(160, 32, 240, 200)},          // Purple
    {"ROAD_CLOSED", QColor(222, 184, 135, 200)}, // Tan
    {"UNKNOWN", QColor(128, 128, 128, 200)}      // Gray
  };
};
