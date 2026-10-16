#pragma once

#include <QString>

// Main-thread lifetime guard for a single Windows user and installation.
// Declare before the runtime owner so it is destroyed after backend cleanup.
class InstanceGuard final
{
public:
    enum class Result { Acquired, AlreadyRunning, Error };

    InstanceGuard() = default;
    ~InstanceGuard();
    InstanceGuard(const InstanceGuard &) = delete;
    InstanceGuard &operator=(const InstanceGuard &) = delete;

    Result acquire(const QString &installationPath);
    QString errorString() const { return m_error; }
    static QString nameForInstallation(const QString &installationPath, QString *error = nullptr);

private:
    void *m_mutex = nullptr;
    QString m_error;
};
