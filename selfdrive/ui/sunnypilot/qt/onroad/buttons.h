/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#pragma once

#include <QElapsedTimer>
#include <QList>
#include <QPixmap>
#include <QPushButton>

#include "selfdrive/ui/qt/onroad/buttons.h"
#include "selfdrive/ui/sunnypilot/qt/util/user_profiles.h"

class QColor;

class ExperimentalButtonSP : public ExperimentalButton {
  Q_OBJECT

public:
  explicit ExperimentalButtonSP(QWidget *parent = nullptr);
  void updateState(const UIState &s) override;

private:
  void drawButton(QPainter &p) override;

  bool dynamic_experimental_control;
  int dec_mpc_mode;
};

class UserProfileButton : public QPushButton {
  Q_OBJECT

public:
  explicit UserProfileButton(QWidget *parent = nullptr);
  void updateState(const UIState &s);
  void refresh(bool reload_list = false);

protected:
  void paintEvent(QPaintEvent *event) override;

private slots:
  void showSelectorDialog();

private:
  QColor badgeColor() const;

  QString current_profile;
  QList<user_profiles::ProfileMetadata> profiles;
  QPixmap user_icon;
  QElapsedTimer profile_timer;
};
