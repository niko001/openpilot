/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/offroad/settings/user_profiles_panel.h"

#include <QDateTime>
#include <QHBoxLayout>
#include <QSignalBlocker>
#include <QVBoxLayout>

#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/sunnypilot/qt/widgets/scrollview.h"

UserProfilesPanel::UserProfilesPanel(QWidget *parent) : ListWidgetSP(parent) {
  auto *title = new QLabel(tr("User Profiles"));
  title->setStyleSheet("font-size: 70px; font-weight: 600;");
  addItem(title);

  auto *description = new QLabel(tr("Create snapshots of your current settings, then switch back to them later."));
  description->setWordWrap(true);
  description->setStyleSheet("font-size: 40px; color: #D0D0D0;");
  addItem(description);

  active_profile_label = new QLabel(this);
  active_profile_label->setStyleSheet("font-size: 45px; color: #FFFFFF; padding-top: 10px;");
  addItem(active_profile_label);

  profile_list = new QListWidget(this);
  profile_list->setSelectionMode(QAbstractItemView::SingleSelection);
  profile_list->setStyleSheet(R"(
    QListWidget {
      background: #101010;
      border-radius: 20px;
      font-size: 45px;
    }
    QListWidget::item {
      padding: 18px 24px;
    }
    QListWidget::item:selected {
      background: #2E6EDE;
    }
  )");
  profile_list->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);

  auto *list_container = new QWidget(this);
  auto *list_layout = new QVBoxLayout(list_container);
  list_layout->setContentsMargins(0, 0, 0, 0);
  list_layout->setSpacing(20);
  list_layout->addWidget(profile_list, 1);

  auto *button_layout = new QHBoxLayout();
  button_layout->setContentsMargins(0, 0, 0, 0);
  button_layout->setSpacing(20);

  add_button = new PushButtonSP(tr("Add Profile"), 450, this);
  remove_button = new PushButtonSP(tr("Remove Profile"), 450, this);
  remove_button->setEnabled(false);

  add_button->setMinimumWidth(0);
  add_button->setMaximumWidth(QWIDGETSIZE_MAX);
  add_button->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
  remove_button->setMinimumWidth(0);
  remove_button->setMaximumWidth(QWIDGETSIZE_MAX);
  remove_button->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);

  button_layout->addWidget(add_button);
  button_layout->addWidget(remove_button);
  button_layout->setStretch(0, 1);
  button_layout->setStretch(1, 1);

  list_layout->addLayout(button_layout);

  auto *scroll = new ScrollViewSP(list_container, this);
  scroll->setFrameShape(QFrame::NoFrame);
  scroll->setWidgetResizable(true);
  scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
  scroll->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
  addItem(scroll);

  connect(add_button, &QPushButton::clicked, this, &UserProfilesPanel::addProfile);
  connect(remove_button, &QPushButton::clicked, this, &UserProfilesPanel::removeSelectedProfile);
  connect(profile_list, &QListWidget::itemSelectionChanged, this, &UserProfilesPanel::updateSelectionState);

  refreshProfiles();
}

void UserProfilesPanel::showEvent(QShowEvent *event) {
  ListWidgetSP::showEvent(event);
  refreshProfiles();
}

void UserProfilesPanel::refreshProfiles() {
  const QString active_profile = user_profiles::currentProfileName();
  active_profile_label->setText(tr("Active profile: %1").arg(active_profile));

  QSignalBlocker blocker(profile_list);
  profile_list->clear();

  const auto profiles = user_profiles::listProfiles();
  if (profiles.isEmpty()) {
    auto *empty_item = new QListWidgetItem(tr("No saved profiles yet."));
    empty_item->setFlags(Qt::NoItemFlags);
    profile_list->addItem(empty_item);
  } else {
    int selected_row = -1;
    for (int i = 0; i < profiles.size(); ++i) {
      const auto &profile = profiles.at(i);
      const QDateTime updated = profile.updated_at.isValid() ? profile.updated_at.toLocalTime() : QDateTime();
      const QString subtitle = updated.isValid() ? updated.toString("yyyy-MM-dd HH:mm") : tr("Unknown");
      auto *item = new QListWidgetItem(QString("%1\n%2").arg(profile.name, subtitle));
      item->setData(Qt::UserRole, profile.name);
      profile_list->addItem(item);

      if (profile.name.compare(active_profile, Qt::CaseInsensitive) == 0) {
        selected_row = i;
      }
    }
    if (selected_row >= 0) {
      profile_list->setCurrentRow(selected_row);
    }
  }

  updateSelectionState();
}

void UserProfilesPanel::addProfile() {
  QString name = InputDialog::getText(tr("Save Current Settings"), this, tr("Choose a profile name to store the current settings:"));
  if (name.isEmpty()) {
    return;
  }

  bool overwrite = false;
  if (user_profiles::profileExists(name)) {
    if (!ConfirmationDialog::confirm(tr("A profile named \"%1\" already exists. Overwrite it with the current settings?").arg(name), tr("Overwrite"), this)) {
      return;
    }
    overwrite = true;
  }

  QString error;
  if (!user_profiles::saveCurrentSettingsAsProfile(name, overwrite, &error)) {
    showError(error);
    return;
  }

  refreshProfiles();
}

void UserProfilesPanel::removeSelectedProfile() {
  QListWidgetItem *item = profile_list->currentItem();
  if (!item || !(item->flags() & Qt::ItemIsSelectable)) {
    return;
  }

  const QString name = item->data(Qt::UserRole).toString();
  if (name.isEmpty()) {
    return;
  }

  if (!ConfirmationDialog::confirm(tr("Delete \"%1\"?").arg(name), tr("Delete"), this)) {
    return;
  }

  QString error;
  if (!user_profiles::deleteProfile(name, &error)) {
    showError(error);
    return;
  }

  refreshProfiles();
}

void UserProfilesPanel::updateSelectionState() {
  QListWidgetItem *item = profile_list->currentItem();
  const bool selectable = item && (item->flags() & Qt::ItemIsSelectable);
  remove_button->setEnabled(selectable);
}

void UserProfilesPanel::showError(const QString &message) {
  const QString text = message.isEmpty() ? tr("Unable to complete the requested action.") : message;
  ConfirmationDialog::alert(text, this);
}
