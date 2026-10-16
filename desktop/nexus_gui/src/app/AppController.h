#pragma once

#include <QObject>
#include <QString>

#include "../runtime/RuntimeManager.h"

class AppController final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString runtimeStatus READ runtimeStatus NOTIFY runtimeStatusChanged)
    Q_PROPERTY(QString runtimeState READ runtimeState NOTIFY runtimeStateChanged)
    Q_PROPERTY(bool runtimeConnected READ runtimeConnected NOTIFY runtimeStatusChanged)
    Q_PROPERTY(QString lastRuntimeError READ lastRuntimeError NOTIFY runtimeErrorChanged)

public:
    explicit AppController(QObject *parent = nullptr);

    QString runtimeStatus() const;
    QString runtimeState() const;
    bool runtimeConnected() const;
    QString lastRuntimeError() const;

    void configureRuntime(const RuntimeLaunchConfig &config);
    RuntimeManager *runtimeManager();

    Q_INVOKABLE bool startRuntime();
    Q_INVOKABLE void stopRuntime();

public slots:
    void publishRuntimeStatus(const QString &status, bool connected);

signals:
    void runtimeStatusChanged();
    void runtimeStateChanged();
    void runtimeErrorChanged();
    void runtimeStandardOutput(const QString &text);
    void runtimeStandardError(const QString &text);

private:
    RuntimeManager m_runtime;
    QString m_runtimeStatus;
    bool m_runtimeConnected = false;
};
