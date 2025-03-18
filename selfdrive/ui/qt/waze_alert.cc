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

  // Calculate alert box dimensions and position
  // This matches the AlertSize::MID in alerts.cc
  int margin = 40;
  int radius = 30;

  // Increased height for our alert to show all text
  int height = this->height() * 0.28; // 28% of screen height

  // Calculate position at the bottom of the screen with margin
  int width = this->width() - margin * 2;
  int x = margin;
  int y = this->height() - height - margin;

  QRect r = QRect(x, y, width, height);

  // Draw background (dark gray with transparency matching existing alerts)
  p.setPen(Qt::NoPen);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);
  QColor bgColor(0x15, 0x15, 0x15, 0xf1 * opacity);

  // For certain alert types, use special colors (but more subtle than before)
  if (alertType == "POLICE") {
    bgColor = QColor(0x00, 0x00, 0x80, 0xf1 * opacity); // Dark blue
  } else if (alertType == "ACCIDENT") {
    bgColor = QColor(0x80, 0x00, 0x00, 0xf1 * opacity); // Dark red
  } else if (alertType == "HAZARD") {
    bgColor = QColor(0x80, 0x40, 0x00, 0xf1 * opacity); // Dark orange
  }

  p.setBrush(QBrush(bgColor));
  p.drawRoundedRect(r, radius, radius);

  // Add a subtle gradient like in alerts.cc
  QLinearGradient g(0, r.y(), 0, r.bottom());
  g.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.05));
  g.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0.35));

  p.setCompositionMode(QPainter::CompositionMode_DestinationOver);
  p.setBrush(QBrush(g));
  p.drawRoundedRect(r, radius, radius);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  // Draw the title
  p.setPen(QColor(0xff, 0xff, 0xff, 0xff * opacity));
  p.setRenderHint(QPainter::TextAntialiasing);

  // Use the same font as alerts.cc - InterFont is a custom function, using QFont directly
  QFont titleFont("Inter", 88, QFont::Bold);
  p.setFont(titleFont);

  // Title centered at top with more space
  QRect titleRect(x, y + 20, width, 120);
  p.drawText(titleRect, Qt::AlignHCenter | Qt::AlignTop, alertTitle);

  // Text below title with slightly smaller font for better fit
  QFont textFont("Inter", 64);
  p.setFont(textFont);

  // Add distance to the description text
  QString displayText = alertText;
  if (alertDistance > 0) {
    displayText += QString("\n%1 m").arg(qRound(alertDistance));
  }

  // More space for the text content
  QRect textRect(x, y + 150, width, height - 170);
  p.drawText(textRect, Qt::AlignHCenter | Qt::AlignTop | Qt::TextWordWrap, displayText);
}
