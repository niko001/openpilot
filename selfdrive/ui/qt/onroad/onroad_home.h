#pragma once

#include "selfdrive/ui/qt/onroad/alerts.h"
#include "selfdrive/ui/qt/onroad/annotated_camera.h"
#include "selfdrive/ui/qt/waze_alert.h"

class OnroadWindow : public QWidget {
  Q_OBJECT

public:
  OnroadWindow(QWidget* parent = 0);

private:
  void paintEvent(QPaintEvent *event);
  OnroadAlerts *alerts;
  WazeAlertOverlay *wazeAlerts;
  AnnotatedCameraWidget *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QHBoxLayout* split;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
