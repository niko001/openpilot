#include "selfdrive/ui/qt/waze_alert.h"
#include <QPainter>
#include <QPainterPath>
#include <QFont>
#include <QFontMetrics>
#include <QDebug>

WazeAlertOverlay::WazeAlertOverlay(QWidget *parent) : QWidget(parent) {
  // Set up timers
  dismissTimer = new QTimer(this);
  connect(dismissTimer, &QTimer::timeout, [this]() {
    alertVisible = false;
    animationStep = ANIMATION_STEPS;
    animationTimer->start(ANIMATION_DURATION_MS / ANIMATION_STEPS);
    update();
  });

  animationTimer = new QTimer(this);
  connect(animationTimer, &QTimer::timeout, [this]() {
    if (alertVisible) {
      // Fade in animation
      if (animationStep < ANIMATION_STEPS) {
        animationStep++;
        update();
        if (animationStep == ANIMATION_STEPS) {
          animationTimer->stop();
        }
      }
    } else {
      // Fade out animation
      if (animationStep > 0) {
        animationStep--;
        update();
        if (animationStep == 0) {
          animationTimer->stop();
        }
      }
    }
  });

  // Widget properties
  setAttribute(Qt::WA_TransparentForMouseEvents);
  setVisible(true);
}

void WazeAlertOverlay::updateState(const UIState &s) {
  // Check for waze alerts in the state
  if (s.sm->updated("wazeAlerts")) {
    auto waze_alerts = (*s.sm)["wazeAlerts"].getWazeAlerts();

    if (waze_alerts.getShowAlert() && waze_alerts.getAlertsCount() > 0) {
      // New alert received
      alertTitle = QString::fromStdString(waze_alerts.getAlertText1());
      alertText = QString::fromStdString(waze_alerts.getAlertText2());
      alertType = QString::fromStdString(waze_alerts.getAlertType());
      alertDistance = waze_alerts.getAlertDistance();

      // Start showing the alert
      if (!alertVisible) {
        alertVisible = true;
        animationStep = 0;
        animationTimer->start(ANIMATION_DURATION_MS / ANIMATION_STEPS);
      }

      // Reset dismiss timer
      dismissTimer->start(ALERT_DURATION_MS);

      // Force repaint
      update();
    } else if (!waze_alerts.getShowAlert() && alertVisible) {
      // Alert cleared, start dismiss animation
      updateAlertVisibility();
    }
  }
}

void WazeAlertOverlay::updateAlertVisibility() {
  alertVisible = false;
  animationStep = ANIMATION_STEPS;
  animationTimer->start(ANIMATION_DURATION_MS / ANIMATION_STEPS);
  update();
}

void WazeAlertOverlay::paintEvent(QPaintEvent *event) {
  if (animationStep == 0 && !alertVisible) {
    return; // Nothing to draw
  }

  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  // Calculate opacity based on animation step
  float opacity = alertVisible ?
                  (float)animationStep / ANIMATION_STEPS :
                  1.0 - (float)animationStep / ANIMATION_STEPS;

  // Get the color for this alert type
  QColor color = alertColors.value(alertType, alertColors["UNKNOWN"]);
  color.setAlpha(color.alpha() * opacity);

  // Calculate alert box dimensions and position
  int width = this->width() * 0.8; // 80% of screen width
  int height = this->height() * 0.25; // 25% of screen height
  int x = (this->width() - width) / 2;
  int y = ALERT_MARGIN;

  // Draw background
  QPainterPath path;
  path.addRoundedRect(x, y, width, height, ALERT_RADIUS, ALERT_RADIUS);
  p.fillPath(path, color);

  // Add a border
  QPen pen(Qt::white, 2);
  pen.setColor(QColor(255, 255, 255, 200 * opacity));
  p.setPen(pen);
  p.drawRoundedRect(x, y, width, height, ALERT_RADIUS, ALERT_RADIUS);

  // Draw the title
  QFont titleFont("Inter", TITLE_FONT_SIZE, QFont::Bold);
  p.setFont(titleFont);
  p.setPen(QPen(QColor(255, 255, 255, 255 * opacity)));
  QRect titleRect(x + 20, y + 20, width - 40, TITLE_FONT_SIZE + 10);
  p.drawText(titleRect, Qt::AlignLeft | Qt::AlignTop, alertTitle);

  // Draw the text
  QFont textFont("Inter", TEXT_FONT_SIZE);
  p.setFont(textFont);
  QRect textRect(x + 20, y + TITLE_FONT_SIZE + 30, width - 40, height - TITLE_FONT_SIZE - 40);
  p.drawText(textRect, Qt::AlignLeft | Qt::AlignTop | Qt::TextWordWrap, alertText);

  // Draw the distance
  if (alertDistance > 0) {
    QFont distanceFont("Inter", DISTANCE_FONT_SIZE);
    p.setFont(distanceFont);
    QString distanceText = QString("%1 m").arg(qRound(alertDistance));
    QRect distanceRect(x + 20, y + height - DISTANCE_FONT_SIZE - 20, width - 40, DISTANCE_FONT_SIZE + 10);
    p.drawText(distanceRect, Qt::AlignRight | Qt::AlignBottom, distanceText);
  }
}
