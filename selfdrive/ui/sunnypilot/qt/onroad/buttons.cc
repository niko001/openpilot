/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/onroad/buttons.h"

#include <QFont>
#include <QPainter>
#include <QStringList>
#include <QtGlobal>

#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/widgets/input.h"

ExperimentalButtonSP::ExperimentalButtonSP(QWidget *parent) : ExperimentalButton(parent) {
  QObject::disconnect(uiState(), &UIState::uiUpdate, this, &ExperimentalButton::updateState);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &ExperimentalButtonSP::updateState);
}

void ExperimentalButtonSP::updateState(const UIState &s) {
  ExperimentalButton::updateState(s);
  const auto long_plan_sp = (*s.sm)["longitudinalPlanSP"].getLongitudinalPlanSP();

  int mode = int(long_plan_sp.getDec().getState());
  if ((long_plan_sp.getDec().getActive() != dynamic_experimental_control) || (mode != dec_mpc_mode)) {
    dynamic_experimental_control = long_plan_sp.getDec().getActive();
    dec_mpc_mode = mode;
    update();
  }
}

void ExperimentalButtonSP::drawButton(QPainter &p) {
  if (dynamic_experimental_control) {
    QPixmap left_half = engage_img.copy(0, 0, engage_img.width() / 2, engage_img.height());
    QPixmap right_half = experimental_img.copy(experimental_img.width() / 2, 0, experimental_img.width() / 2, experimental_img.height());

    QPixmap combined_img(engage_img.width(), engage_img.height());
    combined_img.fill(Qt::transparent);

    QPainter combined_painter(&combined_img);

    combined_painter.setOpacity(dec_mpc_mode == 1 ? 0.1 : 1.0);
    combined_painter.drawPixmap(0, 0, left_half);

    combined_painter.setOpacity(dec_mpc_mode == 1 ? 1.0 : 0.1);
    combined_painter.drawPixmap(engage_img.width() / 2, 0, right_half);

    combined_painter.end();

    drawIcon(p, QPoint(btn_size / 2, btn_size / 2), combined_img, QColor(0, 0, 0, 166), (isDown() || !engageable) ? 0.6 : 1.0);
  } else {
    ExperimentalButton::drawButton(p);
  }
}

UserProfileButton::UserProfileButton(QWidget *parent) : QPushButton(parent) {
  setCursor(Qt::PointingHandCursor);
  setFlat(true);
  setFocusPolicy(Qt::NoFocus);
  setStyleSheet("background: transparent; border: none;");
  setFixedSize(120, 120);

  user_icon = loadPixmap("../assets/icons/monitoring.png", QSize(72, 72));

  connect(this, &QPushButton::clicked, this, &UserProfileButton::showSelectorDialog);

  profile_timer.start();
  refresh(true);
}

void UserProfileButton::updateState(const UIState &) {
  if (!profile_timer.isValid() || profile_timer.hasExpired(600)) {
    refresh();
    profile_timer.restart();
  }
}

void UserProfileButton::refresh(bool reload_list) {
  const QString new_profile = user_profiles::currentProfileName();
  if (current_profile.compare(new_profile, Qt::CaseInsensitive) != 0) {
    current_profile = new_profile;
    update();
  }

  const QList<user_profiles::ProfileMetadata> updated_profiles = user_profiles::listProfiles();
  const bool size_changed = updated_profiles.size() != profiles.size();

  bool names_changed = false;
  if (!size_changed) {
    for (int i = 0; i < updated_profiles.size(); ++i) {
      if (updated_profiles[i].name.compare(profiles[i].name, Qt::CaseInsensitive) != 0) {
        names_changed = true;
        break;
      }
    }
  }

  if (reload_list || size_changed || names_changed) {
    profiles = updated_profiles;
  }

  setEnabled(!profiles.isEmpty());
}

void UserProfileButton::showSelectorDialog() {
  refresh(true);

  if (profiles.isEmpty()) {
    ConfirmationDialog::alert(tr("No saved profiles yet. Add one from Settings > User Profiles."), this);
    return;
  }

  if (profiles.size() == 2) {
    const QString current_lower = current_profile.toLower();
    QString target;
    for (const auto &profile : profiles) {
      if (profile.name.toLower() != current_lower) {
        target = profile.name;
        break;
      }
    }

    if (target.isEmpty()) {
      // both entries resolve to the current profile; nothing to switch
      return;
    }

    QString error;
    if (!user_profiles::applyProfile(target, &error)) {
      ConfirmationDialog::alert(error.isEmpty() ? tr("Unable to load the selected profile.") : error, this);
      return;
    }

    refresh(true);
    return;
  }

  QStringList options;
  options.reserve(profiles.size());
  for (const auto &profile : profiles) {
    options.push_back(profile.name);
  }

  const QString selection = MultiOptionDialog::getSelection(tr("Select Profile"), options, current_profile, this);
  if (selection.isEmpty() || selection.compare(current_profile, Qt::CaseInsensitive) == 0) {
    return;
  }

  QString error;
  if (!user_profiles::applyProfile(selection, &error)) {
    ConfirmationDialog::alert(error.isEmpty() ? tr("Unable to load the selected profile.") : error, this);
    return;
  }

  refresh(true);
}

QColor UserProfileButton::badgeColor() const {
  const QString name = current_profile.isEmpty() ? user_profiles::defaultProfileName() : current_profile;
  const uint hash = qHash(name.toLower());
  return QColor::fromHsl(hash % 360, 170, 140);
}

void UserProfileButton::paintEvent(QPaintEvent *event) {
  Q_UNUSED(event);

  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  const QRectF circle_rect = rect().adjusted(6, 6, -6, -6);
  QColor background = QColor(0, 0, 0, isDown() ? 190 : 150);
  if (!isEnabled()) {
    background = QColor(70, 70, 70, 140);
  }
  p.setPen(Qt::NoPen);
  p.setBrush(background);
  p.drawEllipse(circle_rect);

  if (!user_icon.isNull()) {
    const int max_icon_width = static_cast<int>(circle_rect.width() * 0.6);
    const int max_icon_height = static_cast<int>(circle_rect.height() * 0.6);
    QPixmap icon = user_icon;
    if (icon.width() > max_icon_width || icon.height() > max_icon_height) {
      icon = user_icon.scaled(max_icon_width, max_icon_height, Qt::KeepAspectRatio, Qt::SmoothTransformation);
    }
    const QPoint icon_top_left(circle_rect.center().x() - icon.width() / 2,
                               circle_rect.center().y() - icon.height() / 2);
    p.drawPixmap(icon_top_left, icon);
  }

  const QString initial = user_profiles::profileInitial(current_profile);
  const int badge_size = static_cast<int>(circle_rect.width() * 0.38);
  QRectF badge_rect(circle_rect.right() - badge_size, circle_rect.bottom() - badge_size, badge_size, badge_size);
  p.setBrush(badgeColor());
  p.drawEllipse(badge_rect);

  p.setPen(Qt::white);
  const int font_px = qBound(18, static_cast<int>(badge_size * 0.6), badge_size - 4);
  p.setFont(InterFont(font_px, QFont::DemiBold));
  p.drawText(badge_rect, Qt::AlignCenter, initial);
}
