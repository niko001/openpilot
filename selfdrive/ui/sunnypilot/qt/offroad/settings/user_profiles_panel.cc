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
#include <QPushButton>
#include <QSize>
#include <QItemSelectionModel>

#include "selfdrive/ui/qt/widgets/input.h"

UserProfilesPanel::UserProfilesPanel(QWidget *parent) : ListWidgetSP(parent) {
  auto *content = new QWidget(this);
  content->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
  auto *layout = new QVBoxLayout(content);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(25);

  star_filled_icon = QIcon("../../sunnypilot/selfdrive/assets/icons/star-filled.png");
  star_empty_icon = QIcon("../../sunnypilot/selfdrive/assets/icons/star-empty.png");

  auto *title = new QLabel(tr("User Profiles"));
  title->setStyleSheet("font-size: 70px; font-weight: 600;");
  layout->addWidget(title);

  auto *description = new QLabel(tr("Create snapshots of your current settings, then switch back to them later."));
  description->setWordWrap(true);
  description->setStyleSheet("font-size: 40px; color: #D0D0D0;");
  layout->addWidget(description);

  active_profile_label = new QLabel(this);
  active_profile_label->setStyleSheet("font-size: 45px; color: #FFFFFF; padding-top: 10px;");
  layout->addWidget(active_profile_label);

  profile_list = new QListWidget(this);
  profile_list->setSelectionMode(QAbstractItemView::SingleSelection);
  profile_list->setMinimumHeight(480);
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
  layout->addWidget(profile_list);
  int list_index = layout->count() - 1;
  layout->setStretch(list_index, 1);
  layout->addStretch(1);

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
  layout->addLayout(button_layout);

  addItem(content);

  connect(add_button, &QPushButton::clicked, this, &UserProfilesPanel::addProfile);
  connect(remove_button, &QPushButton::clicked, this, &UserProfilesPanel::removeSelectedProfile);
  connect(profile_list, &QListWidget::itemSelectionChanged, this, &UserProfilesPanel::updateSelectionState);
  connect(profile_list, &QListWidget::itemClicked, this, &UserProfilesPanel::activateProfile);

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
  const bool has_default = user_profiles::hasConfiguredDefaultProfile();
  const QString default_profile = user_profiles::defaultProfileName();

  if (profiles.isEmpty()) {
    auto *empty_item = new QListWidgetItem(tr("No saved profiles yet."));
    empty_item->setFlags(Qt::NoItemFlags);
    profile_list->addItem(empty_item);
  } else {
    QListWidgetItem *active_item = nullptr;
    for (const auto &profile : profiles) {
      const QDateTime updated = profile.updated_at.isValid() ? profile.updated_at.toLocalTime() : QDateTime();
      const QString subtitle = updated.isValid() ? updated.toString("yyyy-MM-dd HH:mm") : tr("Unknown");

      auto *item = new QListWidgetItem(profile_list);
      item->setData(Qt::UserRole, profile.name);
      item->setFlags(Qt::ItemIsSelectable | Qt::ItemIsEnabled);

      auto *row = new QWidget(profile_list);
      auto *row_layout = new QHBoxLayout(row);
      row_layout->setContentsMargins(20, 15, 20, 15);
      row_layout->setSpacing(20);

      QString header = profile.name;
      if (has_default && profile.name.compare(default_profile, Qt::CaseInsensitive) == 0) {
        header += tr("  (Default)");
      }
      auto *info_label = new QLabel(QString("%1\n%2").arg(header, subtitle), row);
      info_label->setStyleSheet("font-size: 42px; color: white;");
      info_label->setAlignment(Qt::AlignVCenter | Qt::AlignLeft);
      info_label->setAttribute(Qt::WA_TransparentForMouseEvents);
      row_layout->addWidget(info_label, 1);

      auto *star_btn = new QPushButton(row);
      star_btn->setFlat(true);
      star_btn->setCheckable(true);
      star_btn->setFocusPolicy(Qt::NoFocus);
      star_btn->setCursor(Qt::PointingHandCursor);
      star_btn->setIconSize(QSize(56, 56));
      star_btn->setStyleSheet("QPushButton { border: none; }");
      const bool is_default = has_default && profile.name.compare(default_profile, Qt::CaseInsensitive) == 0;
      star_btn->setChecked(is_default);
      star_btn->setIcon(is_default ? star_filled_icon : star_empty_icon);
      star_btn->setToolTip(is_default ? tr("Default profile") : tr("Set as default profile"));
      row_layout->addWidget(star_btn, 0, Qt::AlignRight | Qt::AlignVCenter);

      QObject::connect(star_btn, &QPushButton::clicked, this, [this, profile_name = profile.name](bool checked) {
        if (checked) {
          setDefaultProfile(profile_name);
        } else {
          setDefaultProfile(QString());
        }
      });

      profile_list->setItemWidget(item, row);
      item->setSizeHint(QSize(0, 150));

      if (profile.name.compare(active_profile, Qt::CaseInsensitive) == 0) {
        active_item = item;
      }
    }

    if (active_item != nullptr) {
      profile_list->setCurrentItem(active_item, QItemSelectionModel::ClearAndSelect);
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

void UserProfilesPanel::activateProfile(QListWidgetItem *item) {
  if (!item || !(item->flags() & Qt::ItemIsSelectable)) {
    return;
  }

  const QString name = item->data(Qt::UserRole).toString();
  if (name.isEmpty()) {
    return;
  }

  const QString current = user_profiles::currentProfileName();
  if (name.compare(current, Qt::CaseInsensitive) == 0) {
    return;
  }

  QString error;
  if (!user_profiles::applyProfile(name, &error)) {
    showError(error);
    return;
  }

  refreshProfiles();
}

void UserProfilesPanel::setDefaultProfile(const QString &profile_name) {
  if (profile_name.isEmpty()) {
    user_profiles::clearDefaultProfileName();
    refreshProfiles();
    return;
  }

  if (!user_profiles::profileExists(profile_name)) {
    showError(tr("Profile \"%1\" was not found.").arg(profile_name));
    refreshProfiles();
    return;
  }

  user_profiles::setDefaultProfileName(profile_name);
  refreshProfiles();
}

void UserProfilesPanel::showError(const QString &message) {
  const QString text = message.isEmpty() ? tr("Unable to complete the requested action.") : message;
  ConfirmationDialog::alert(text, this);
}
