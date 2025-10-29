/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#pragma once

#include <QLabel>
#include <QListWidget>

#include "selfdrive/ui/sunnypilot/qt/util/user_profiles.h"
#include "selfdrive/ui/sunnypilot/qt/widgets/controls.h"

class UserProfilesPanel : public ListWidgetSP {
  Q_OBJECT

public:
  explicit UserProfilesPanel(QWidget *parent = nullptr);

protected:
  void showEvent(QShowEvent *event) override;

private slots:
  void refreshProfiles();
  void addProfile();
  void removeSelectedProfile();
  void updateSelectionState();
  void activateProfile(QListWidgetItem *item);

private:
  void showError(const QString &message);

  QLabel *active_profile_label = nullptr;
  QListWidget *profile_list = nullptr;
  PushButtonSP *add_button = nullptr;
  PushButtonSP *remove_button = nullptr;
};
#include <QShowEvent>
