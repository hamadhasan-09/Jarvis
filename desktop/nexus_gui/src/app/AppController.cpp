#include "AppController.h"

AppController::AppController(QObject *parent)
    : QObject(parent), m_runtimeStatus(QStringLiteral("Runtime stopped"))
{
    connect(&m_runtime, &RuntimeManager::stateChanged, this, [this] {
                const bool connected = m_runtime.state() == RuntimeManager::State::READY;
                const QString message = m_runtime.statusMessage();
                const bool statusChanged = m_runtimeStatus != message;
                const bool connectionChanged = m_runtimeConnected != connected;
                m_runtimeStatus = message;
                m_runtimeConnected = connected;
                emit runtimeStateChanged();
                if (statusChanged || connectionChanged)
                    emit runtimeStatusChanged();
            });
    connect(&m_runtime, &RuntimeManager::lastErrorChanged,
            this, [this](const QString &) { emit runtimeErrorChanged(); });
    connect(&m_runtime, &RuntimeManager::standardOutputReceived,
            this, &AppController::runtimeStandardOutput);
    connect(&m_runtime, &RuntimeManager::standardErrorReceived,
            this, &AppController::runtimeStandardError);
}

QString AppController::runtimeStatus() const
{
    return m_runtimeStatus;
}

QString AppController::runtimeState() const
{
    return m_runtime.stateName();
}

bool AppController::runtimeConnected() const
{
    return m_runtimeConnected;
}

QString AppController::lastRuntimeError() const
{
    return m_runtime.lastError();
}

void AppController::configureRuntime(const RuntimeLaunchConfig &config)
{
    m_runtime.setLaunchConfig(config);
}

RuntimeManager *AppController::runtimeManager()
{
    return &m_runtime;
}

bool AppController::startRuntime()
{
    return m_runtime.start();
}

void AppController::stopRuntime()
{
    m_runtime.stop();
}

void AppController::publishRuntimeStatus(const QString &status, bool connected)
{
    if (m_runtimeStatus == status && m_runtimeConnected == connected)
        return;

    m_runtimeStatus = status;
    m_runtimeConnected = connected;
    emit runtimeStatusChanged();
}
