/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#pragma once

#include <optional>

#include <QDateTime>
#include <QList>
#include <QString>

namespace user_profiles {

struct ProfileMetadata {
  QString name;
  QString id;
  QString file_path;
  QDateTime created_at;
  QDateTime updated_at;
};

QList<ProfileMetadata> listProfiles();
std::optional<ProfileMetadata> profileForName(const QString &display_name);

bool saveCurrentSettingsAsProfile(const QString &display_name, bool overwrite_existing = false, QString *error = nullptr);
bool deleteProfile(const QString &display_name, QString *error = nullptr);
bool applyProfile(const QString &display_name, QString *error = nullptr);

QString currentProfileName();
void setCurrentProfileName(const QString &display_name);
QString defaultProfileName();
bool hasConfiguredDefaultProfile();
void setDefaultProfileName(const QString &display_name);
void clearDefaultProfileName();
void ensureDefaultProfileActive();

bool profileExists(const QString &display_name);
QString profileInitial(const QString &display_name);

}  // namespace user_profiles

