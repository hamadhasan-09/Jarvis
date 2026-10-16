#pragma once

#include <QObject>
#include <QByteArray>
#include <QProcess>
#include <QProcessEnvironment>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QPointer>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QUrl>

struct RuntimeLaunchConfig
{
    QString program;
    QStringList arguments;
    QString workingDirectory;
    QProcessEnvironment environment;
    bool inheritEnvironment = true;
    QByteArray readinessMarker = QByteArrayLiteral("NEXUS_RUNTIME_READY");
    QByteArray gracefulShutdownInput;
    QUrl healthUrl;
    QString expectedLaunchId;
    bool requireDesktopSafeMode = false;
    QUrl shutdownUrl;
    QString shutdownSecret;
    int startupTimeoutMs = 30000;
    int healthPollIntervalMs = 250;
    int requestTimeoutMs = 1500;
    int shutdownGraceMs = 1500;
    int terminateTimeoutMs = 1000;
};

class RuntimeManager final : public QObject
{
    Q_OBJECT
    Q_PROPERTY(State state READ state NOTIFY stateChanged)
    Q_PROPERTY(QString stateName READ stateName NOTIFY stateChanged)
    Q_PROPERTY(QString statusMessage READ statusMessage NOTIFY stateChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    Q_PROPERTY(bool shutdownForced READ shutdownForced NOTIFY shutdownForcedChanged)

public:
    enum class State { STOPPED, STARTING, READY, STOPPING, ERROR };
    Q_ENUM(State)

    explicit RuntimeManager(QObject *parent = nullptr);
    ~RuntimeManager() override;

    State state() const;
    QString stateName() const;
    QString statusMessage() const;
    QString lastError() const;
    bool shutdownForced() const;
    bool isProcessRunning() const;
    RuntimeLaunchConfig launchConfig() const;

    void setLaunchConfig(const RuntimeLaunchConfig &config);
    Q_INVOKABLE bool start();
    Q_INVOKABLE void stop();

signals:
    void stateChanged();
    void lastErrorChanged(const QString &message);
    void standardOutputReceived(const QString &text);
    void standardErrorReceived(const QString &text);
    void shutdownForcedChanged();

private slots:
    void onStandardOutput();
    void onStandardError();
    void onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void onProcessError(QProcess::ProcessError error);
    void onStartupTimeout();
    void onShutdownGraceTimeout();
    void onForceKillTimeout();
    void pollHealth();

private:
    static QString stateNameFor(State state);
    void setState(State state, const QString &message);
    void setError(const QString &message);
    void checkReadiness(const QByteArray &bytes);
    void requestTermination();
    void flushOutput();
    void failHealthCheck(const QString &message);

    RuntimeLaunchConfig m_config;
    QProcess m_process;
    QNetworkAccessManager m_network;
    QPointer<QNetworkReply> m_healthReply;
    QPointer<QNetworkReply> m_shutdownReply;
    QTimer m_startupTimer;
    QTimer m_healthPollTimer;
    QTimer m_shutdownGraceTimer;
    QTimer m_forceKillTimer;
    State m_state = State::STOPPED;
    QString m_statusMessage = QStringLiteral("Runtime stopped");
    QString m_lastError;
    QByteArray m_readinessBuffer;
    bool m_shutdownRequested = false;
    bool m_failureActive = false;
    bool m_shutdownForced = false;
    bool m_shutdownKilled = false;
};
