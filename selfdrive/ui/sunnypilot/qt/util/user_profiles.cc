/**
 * Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.
 *
 * This file is part of sunnypilot and is licensed under the MIT License.
 * See the LICENSE.md file in the root directory for more details.
 */

#include "selfdrive/ui/sunnypilot/qt/util/user_profiles.h"

#include <algorithm>

#include <QByteArray>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QHash>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QObject>
#include <QRegularExpression>
#include <QStringList>
#include <QtGlobal>

#include "common/params.h"
#include "system/hardware/hw.h"

namespace user_profiles {
namespace {

constexpr char kCurrentProfileParam[] = "UserProfileCurrent";
constexpr char kDefaultProfileParam[] = "UserProfileDefault";
const QString kDefaultProfileName = QStringLiteral("Default");

QString normalizeName(const QString &name) {
  return name.trimmed();
}

QString profilesDirectory() {
  QString dir_path = QString::fromStdString(Path::params()) + "/profiles";
  QDir dir(dir_path);
  if (!dir.exists()) {
    dir.mkpath(".");
  }
  return dir.absolutePath();
}

QString sanitizeId(const QString &display_name) {
  QString id = display_name.trimmed().toLower();
  id.replace(QRegularExpression("[^a-z0-9]+"), "-");
  id.remove(QRegularExpression("^-+"));
  id.remove(QRegularExpression("-+$"));
  if (id.isEmpty()) {
    id = QStringLiteral("profile");
  }
  return id;
}

struct ProfileFileTarget {
  QString id;
  QString path;
};

ProfileFileTarget generateFileTarget(const QString &base_id) {
  QDir dir(profilesDirectory());
  ProfileFileTarget target{base_id, dir.absoluteFilePath(base_id + ".json")};

  int suffix = 2;
  while (QFile::exists(target.path)) {
    target.id = base_id + "-" + QString::number(suffix++);
    target.path = dir.absoluteFilePath(target.id + ".json");
  }
  return target;
}

std::optional<QJsonObject> readProfileJson(const QString &file_path) {
  QFile file(file_path);
  if (!file.exists()) return std::nullopt;
  if (!file.open(QIODevice::ReadOnly)) {
    qWarning() << "Failed to open user profile file for reading:" << file_path << file.errorString();
    return std::nullopt;
  }

  const QByteArray data = file.readAll();
  file.close();

  QJsonParseError parse_error;
  const QJsonDocument doc = QJsonDocument::fromJson(data, &parse_error);
  if (parse_error.error != QJsonParseError::NoError || !doc.isObject()) {
    qWarning() << "Failed to parse user profile JSON:" << file_path << parse_error.errorString();
    return std::nullopt;
  }

  return doc.object();
}

ProfileMetadata metadataFromJson(const QJsonObject &root, const QString &file_path) {
  ProfileMetadata metadata;
  metadata.name = root.value("name").toString();
  metadata.id = root.value("id").toString();
  metadata.file_path = file_path;

  auto created = QDateTime::fromString(root.value("created_at").toString(), Qt::ISODateWithMs);
  if (!created.isValid()) {
    created = QDateTime::fromString(root.value("created_at").toString(), Qt::ISODate);
  }
  metadata.created_at = created;

  auto updated = QDateTime::fromString(root.value("updated_at").toString(), Qt::ISODateWithMs);
  if (!updated.isValid()) {
    updated = QDateTime::fromString(root.value("updated_at").toString(), Qt::ISODate);
  }
  metadata.updated_at = updated;

  if (metadata.id.isEmpty()) {
    metadata.id = sanitizeId(metadata.name);
  }
  if (!metadata.created_at.isValid()) {
    metadata.created_at = metadata.updated_at;
  }
  if (!metadata.updated_at.isValid()) {
    metadata.updated_at = metadata.created_at;
  }

  return metadata;
}

QJsonObject collectConfig() {
  Params params;
  QJsonObject config;
  const auto keys = params.allKeys(ParamKeyFlag::BACKUP);
  for (const auto &key : keys) {
    const std::string value = params.get(key);
    QByteArray raw = QByteArray::fromStdString(value);
    const QString encoded = QString::fromLatin1(raw.toBase64());
    config.insert(QString::fromStdString(key), encoded);
  }
  return config;
}

bool writeProfile(const QString &file_path, const QJsonObject &root, QString *error) {
  QFile file(file_path);
  if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
    if (error) *error = QObject::tr("Unable to save profile to %1: %2").arg(file_path, file.errorString());
    return false;
  }

  const QJsonDocument doc(root);
  const QByteArray serialized = doc.toJson(QJsonDocument::Indented);
  if (file.write(serialized) != serialized.size()) {
    if (error) *error = QObject::tr("Failed writing profile data to %1.").arg(file_path);
    file.close();
    return false;
  }

  file.close();
  return true;
}

bool applyConfig(const QJsonObject &config, QString *error) {
  if (config.isEmpty()) {
    if (error) *error = QObject::tr("Profile contains no configuration values.");
    return false;
  }

  Params params;
  const auto backup_keys = params.allKeys(ParamKeyFlag::BACKUP);
  QHash<QString, QString> lookup;
  lookup.reserve(static_cast<int>(backup_keys.size()));
  for (const auto &key : backup_keys) {
    const QString qkey = QString::fromStdString(key);
    lookup.insert(qkey.toLower(), qkey);
  }

  for (auto it = config.constBegin(); it != config.constEnd(); ++it) {
    const QString normalized = it.key().toLower();
    if (!lookup.contains(normalized)) {
      continue;
    }
    const QByteArray decoded = QByteArray::fromBase64(it.value().toString().toUtf8());
    const std::string value(decoded.constData(), decoded.constData() + decoded.size());
    params.put(lookup[normalized].toStdString(), value);
  }

  return true;
}


std::optional<QString> configuredDefaultProfile() {
  Params params;
  const std::string stored = params.get(kDefaultProfileParam);
  if (stored.empty()) {
    return std::nullopt;
  }
  return normalizeName(QString::fromStdString(stored));
}

}  // namespace

QList<ProfileMetadata> listProfiles() {
  QList<ProfileMetadata> profiles;
  QDir dir(profilesDirectory());
  const QStringList files = dir.entryList(QStringList() << "*.json", QDir::Files | QDir::Readable);
  for (const QString &file_name : files) {
    const QString file_path = dir.absoluteFilePath(file_name);
    const auto root = readProfileJson(file_path);
    if (!root.has_value()) {
      continue;
    }
    ProfileMetadata metadata = metadataFromJson(root.value(), file_path);
    if (metadata.name.isEmpty()) {
      metadata.name = metadata.id;
    }
    profiles.push_back(metadata);
  }

  std::sort(profiles.begin(), profiles.end(), [](const ProfileMetadata &a, const ProfileMetadata &b) {
    return a.name.compare(b.name, Qt::CaseInsensitive) < 0;
  });

  return profiles;
}

std::optional<ProfileMetadata> profileForName(const QString &display_name) {
  const QString normalized = normalizeName(display_name);
  for (const auto &profile : listProfiles()) {
    if (profile.name.compare(normalized, Qt::CaseInsensitive) == 0) {
      return profile;
    }
  }
  return std::nullopt;
}

bool saveCurrentSettingsAsProfile(const QString &display_name, bool overwrite_existing, QString *error) {
  const QString trimmed = normalizeName(display_name);
  if (trimmed.isEmpty()) {
    if (error) *error = QObject::tr("Profile name cannot be empty.");
    return false;
  }

  auto existing = profileForName(trimmed);
  QString target_path;
  QString profile_id;
  QDateTime created_at = QDateTime::currentDateTimeUtc();
  if (existing.has_value()) {
    if (!overwrite_existing) {
      if (error) *error = QObject::tr("A profile named \"%1\" already exists.").arg(trimmed);
      return false;
    }
    target_path = existing->file_path;
    profile_id = existing->id;
    if (existing->created_at.isValid()) {
      created_at = existing->created_at;
    }
  } else {
    ProfileFileTarget target = generateFileTarget(sanitizeId(trimmed));
    target_path = target.path;
    profile_id = target.id;
  }

  const QJsonObject config = collectConfig();
  if (config.isEmpty()) {
    if (error) *error = QObject::tr("No backup-enabled settings were found to save.");
    return false;
  }

  QJsonObject root;
  const QDateTime now = QDateTime::currentDateTimeUtc();
  root.insert("name", trimmed);
  root.insert("id", profile_id);
  root.insert("created_at", created_at.toString(Qt::ISODateWithMs));
  root.insert("updated_at", now.toString(Qt::ISODateWithMs));
  root.insert("config", config);

  if (!writeProfile(target_path, root, error)) {
    return false;
  }

  setCurrentProfileName(trimmed);
  return true;
}

bool deleteProfile(const QString &display_name, QString *error) {
  auto existing = profileForName(display_name);
  if (!existing.has_value()) {
    if (error) *error = QObject::tr("Profile \"%1\" was not found.").arg(display_name);
    return false;
  }

  QFile file(existing->file_path);
  if (!file.remove()) {
    if (error) *error = QObject::tr("Unable to delete profile file %1: %2").arg(existing->file_path, file.errorString());
    return false;
  }

  if (currentProfileName().compare(display_name, Qt::CaseInsensitive) == 0) {
    setCurrentProfileName(QString());
  }

  if (hasConfiguredDefaultProfile() && defaultProfileName().compare(existing->name, Qt::CaseInsensitive) == 0) {
    clearDefaultProfileName();
  }

  return true;
}

bool applyProfile(const QString &display_name, QString *error) {
  auto existing = profileForName(display_name);
  if (!existing.has_value()) {
    if (error) *error = QObject::tr("Profile \"%1\" was not found.").arg(display_name);
    return false;
  }

  const auto root = readProfileJson(existing->file_path);
  if (!root.has_value()) {
    if (error) *error = QObject::tr("Unable to read profile data from %1.").arg(existing->file_path);
    return false;
  }

  const QJsonObject config = root->value("config").toObject();
  if (!applyConfig(config, error)) {
    return false;
  }

  setCurrentProfileName(existing->name);
  return true;
}

QString currentProfileName() {
  Params params;
  const std::string value = params.get(kCurrentProfileParam);
  if (value.empty()) {
    return kDefaultProfileName;
  }
  QString trimmed = normalizeName(QString::fromStdString(value));
  return trimmed.isEmpty() ? kDefaultProfileName : trimmed;
}

void setCurrentProfileName(const QString &display_name) {
  Params params;
  const QString trimmed = normalizeName(display_name);
  if (trimmed.isEmpty()) {
    params.remove(kCurrentProfileParam);
  } else {
    params.put(kCurrentProfileParam, trimmed.toStdString());
  }
}

QString defaultProfileName() {
  if (auto stored = configuredDefaultProfile(); stored.has_value()) {
    return stored.value();
  }
  return kDefaultProfileName;
}


bool hasConfiguredDefaultProfile() {
  return configuredDefaultProfile().has_value();
}

void setDefaultProfileName(const QString &display_name) {
  Params params;
  const QString trimmed = normalizeName(display_name);
  if (trimmed.isEmpty()) {
    params.remove(kDefaultProfileParam);
    return;
  }

  if (auto existing = profileForName(trimmed); existing.has_value()) {
    params.put(kDefaultProfileParam, existing->name.toStdString());
  } else {
    params.remove(kDefaultProfileParam);
  }
}

void clearDefaultProfileName() {
  Params params;
  params.remove(kDefaultProfileParam);
}

void ensureDefaultProfileActive() {
  static bool applied = false;
  if (applied) {
    return;
  }
  applied = true;

  auto stored = configuredDefaultProfile();
  if (!stored.has_value()) {
    return;
  }

  const QString target = stored.value();
  if (!profileExists(target)) {
    clearDefaultProfileName();
    return;
  }

  if (currentProfileName().compare(target, Qt::CaseInsensitive) == 0) {
    return;
  }

  QString error;
  if (!applyProfile(target, &error)) {
    qWarning() << "Failed to apply default profile" << target << error;
  }
}

bool profileExists(const QString &display_name) {
  return profileForName(display_name).has_value();
}

QString profileInitial(const QString &display_name) {
  QString trimmed = normalizeName(display_name);
  if (trimmed.isEmpty()) {
    trimmed = kDefaultProfileName;
  }
  const QChar first = trimmed.front().toUpper();
  if (first.isLetterOrNumber()) {
    return QString(first);
  }
  return QStringLiteral("?");
}

}  // namespace user_profiles
