#include "RuntimeManager.h"

#include <QDebug>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkRequest>
#include <QUrlQuery>

RuntimeManager::RuntimeManager(QObject *parent)
    : QObject(parent), m_network(this)
{
    m_process.setProcessChannelMode(QProcess::SeparateChannels);
    m_startupTimer.setSingleShot(true);
    m_healthPollTimer.setSingleShot(false);
    m_shutdownGraceTimer.setSingleShot(true);
    m_forceKillTimer.setSingleShot(true);

    connect(&m_process, &QProcess::readyReadStandardOutput,
            this, &RuntimeManager::onStandardOutput);
    connect(&m_process, &QProcess::readyReadStandardError,
            this, &RuntimeManager::onStandardError);
    // QIODevice::readyRead is the reliable current-channel notification on
    // Windows; keep the explicit standard-output signal as a secondary path.
    connect(&m_process, &QIODevice::readyRead,
            this, &RuntimeManager::onStandardOutput);
    connect(&m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this, &RuntimeManager::onProcessFinished);
    connect(&m_process, &QProcess::errorOccurred,
            this, &RuntimeManager::onProcessError);
    connect(&m_process, &QProcess::started, this, [this] {
        if (m_state == State::STARTING && !m_config.healthUrl.isEmpty()) {
            m_healthPollTimer.start(qMax(50, m_config.healthPollIntervalMs));
            pollHealth();
        }
    });
    connect(&m_startupTimer, &QTimer::timeout,
            this, &RuntimeManager::onStartupTimeout);
    connect(&m_healthPollTimer, &QTimer::timeout,
            this, &RuntimeManager::pollHealth);
    connect(&m_shutdownGraceTimer, &QTimer::timeout,
            this, &RuntimeManager::onShutdownGraceTimeout);
    connect(&m_forceKillTimer, &QTimer::timeout,
            this, &RuntimeManager::onForceKillTimeout);
}

RuntimeManager::~RuntimeManager()
{
    m_startupTimer.stop();
    m_healthPollTimer.stop();
    m_shutdownGraceTimer.stop();
    m_forceKillTimer.stop();
    if (m_healthReply)
        m_healthReply->abort();
    if (m_shutdownReply)
        m_shutdownReply->abort();

    if (m_process.state() == QProcess::NotRunning)
        return;

    if (!m_config.gracefulShutdownInput.isEmpty()) {
        m_process.write(m_config.gracefulShutdownInput);
        m_process.closeWriteChannel();
        m_process.waitForBytesWritten(qMax(1, m_config.shutdownGraceMs));
    }
    m_process.terminate();
    if (!m_process.waitForFinished(qMax(1, m_config.terminateTimeoutMs))) {
        m_process.kill();
        m_process.waitForFinished(qMax(1, m_config.terminateTimeoutMs));
    }
}

RuntimeManager::State RuntimeManager::state() const
{
    return m_state;
}

QString RuntimeManager::stateName() const
{
    return stateNameFor(m_state);
}

QString RuntimeManager::statusMessage() const
{
    return m_statusMessage;
}

QString RuntimeManager::lastError() const
{
    return m_lastError;
}

bool RuntimeManager::shutdownForced() const
{
    return m_shutdownForced;
}

bool RuntimeManager::isProcessRunning() const
{
    return m_process.state() != QProcess::NotRunning;
}

RuntimeLaunchConfig RuntimeManager::launchConfig() const
{
    return m_config;
}

void RuntimeManager::setLaunchConfig(const RuntimeLaunchConfig &config)
{
    if (isProcessRunning()) {
        qWarning() << "Runtime launch configuration cannot change while the process is active";
        return;
    }
    m_config = config;
}

bool RuntimeManager::start()
{
    if (isProcessRunning() || m_state == State::STARTING || m_state == State::STOPPING)
        return false;
    if (m_config.program.trimmed().isEmpty()) {
        setError(QStringLiteral("Runtime executable is not configured"));
        return false;
    }
    if (m_config.startupTimeoutMs < 1 || m_config.shutdownGraceMs < 0
        || m_config.terminateTimeoutMs < 1) {
        setError(QStringLiteral("Runtime timeouts must be positive (shutdown grace may be zero)"));
        return false;
    }
    if (m_config.healthUrl.isEmpty() && m_config.readinessMarker.isEmpty()) {
        setError(QStringLiteral("Runtime readiness marker must not be empty"));
        return false;
    }
    if (!m_config.healthUrl.isEmpty() && m_config.expectedLaunchId.trimmed().isEmpty()) {
        setError(QStringLiteral("Runtime health checks require an expected launch identity"));
        return false;
    }
    if (m_config.shutdownUrl.isEmpty() != m_config.shutdownSecret.isEmpty()) {
        setError(QStringLiteral("Authenticated shutdown requires both a URL and a per-launch secret"));
        return false;
    }
    if (m_config.healthPollIntervalMs < 1 || m_config.requestTimeoutMs < 1) {
        setError(QStringLiteral("Health polling and request timeouts must be positive"));
        return false;
    }
    m_shutdownRequested = false;
    m_failureActive = false;
    m_shutdownKilled = false;
    if (m_shutdownForced) {
        m_shutdownForced = false;
        emit shutdownForcedChanged();
    }
    m_lastError.clear();
    emit lastErrorChanged(m_lastError);
    m_readinessBuffer.clear();
    m_healthPollTimer.stop();
    m_process.setProgram(m_config.program);
    m_process.setArguments(m_config.arguments);
    m_process.setWorkingDirectory(m_config.workingDirectory);
    if (!m_config.inheritEnvironment) {
        m_process.setProcessEnvironment(m_config.environment);
    } else if (!m_config.environment.isEmpty()) {
        QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
        for (const QString &name : m_config.environment.keys())
            environment.insert(name, m_config.environment.value(name));
        m_process.setProcessEnvironment(environment);
    }
    setState(State::STARTING, QStringLiteral("Starting runtime"));
    m_process.start();
    m_startupTimer.start(m_config.startupTimeoutMs);
    return true;
}

void RuntimeManager::stop()
{
    if (m_process.state() == QProcess::NotRunning) {
        if (m_state != State::ERROR)
            setState(State::STOPPED, QStringLiteral("Runtime stopped"));
        return;
    }

    m_shutdownRequested = true;
    m_startupTimer.stop();
    m_healthPollTimer.stop();
    if (m_healthReply) {
        m_healthReply->abort();
        m_healthReply = nullptr;
    }
    setState(State::STOPPING, QStringLiteral("Stopping runtime"));
    if (!m_config.shutdownUrl.isEmpty() && !m_config.shutdownSecret.isEmpty()) {
        m_shutdownGraceTimer.start(m_config.shutdownGraceMs);
        QNetworkRequest request(m_config.shutdownUrl);
        request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
        request.setRawHeader("X-Nexus-Shutdown-Secret", m_config.shutdownSecret.toUtf8());
        request.setTransferTimeout(m_config.requestTimeoutMs);
        const QJsonObject payload{{QStringLiteral("launch_id"), m_config.expectedLaunchId}};
        QNetworkReply *reply = m_network.post(
            request, QJsonDocument(payload).toJson(QJsonDocument::Compact));
        m_shutdownReply = reply;
        connect(reply, &QNetworkReply::finished, this, [this, reply] {
            if (m_shutdownReply == reply)
                m_shutdownReply = nullptr;
            const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
            const auto error = reply->error();
            reply->deleteLater();
            if (m_shutdownRequested && isProcessRunning()
                && (error != QNetworkReply::NoError || status != 202)) {
                setState(State::STOPPING,
                         QStringLiteral("Authenticated shutdown was rejected; terminating backend"));
                requestTermination();
            }
        });
    } else if (!m_config.gracefulShutdownInput.isEmpty()) {
        m_process.write(m_config.gracefulShutdownInput);
        m_process.closeWriteChannel();
        m_shutdownGraceTimer.start(m_config.shutdownGraceMs);
    } else {
        requestTermination();
    }
}

void RuntimeManager::onStandardOutput()
{
    const QByteArray bytes = m_process.readAllStandardOutput();
    if (bytes.isEmpty())
        return;
    emit standardOutputReceived(QString::fromLocal8Bit(bytes));
    if (m_state == State::STARTING && m_config.healthUrl.isEmpty())
        checkReadiness(bytes);
}

void RuntimeManager::onStandardError()
{
    const QByteArray bytes = m_process.readAllStandardError();
    if (!bytes.isEmpty())
        emit standardErrorReceived(QString::fromLocal8Bit(bytes));
}

void RuntimeManager::onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus)
{
    flushOutput();
    m_startupTimer.stop();
    m_healthPollTimer.stop();
    m_shutdownGraceTimer.stop();
    m_forceKillTimer.stop();
    if (m_healthReply) {
        m_healthReply->abort();
        m_healthReply = nullptr;
    }
    if (m_shutdownReply) {
        m_shutdownReply->abort();
        m_shutdownReply = nullptr;
    }

    if (m_shutdownRequested) {
        const QString stopMessage = m_shutdownKilled
            ? QStringLiteral("Runtime was force-killed after shutdown timeouts")
            : m_shutdownForced
                ? QStringLiteral("Runtime was terminated after graceful shutdown timed out")
                : QStringLiteral("Runtime stopped");
        setState(State::STOPPED, stopMessage);
        m_shutdownRequested = false;
        return;
    }
    if (m_failureActive)
        return;

    QString reason = QStringLiteral("Runtime exited unexpectedly (code %1)").arg(exitCode);
    if (exitStatus == QProcess::CrashExit)
        reason = QStringLiteral("Runtime crashed unexpectedly (code %1)").arg(exitCode);
    setError(reason);
}

void RuntimeManager::onProcessError(QProcess::ProcessError error)
{
    if (error == QProcess::FailedToStart) {
        m_startupTimer.stop();
        m_healthPollTimer.stop();
        setError(QStringLiteral("Could not start runtime: %1").arg(m_process.errorString()));
    } else if (error == QProcess::Crashed && !m_shutdownRequested && !m_failureActive) {
        setError(QStringLiteral("Runtime process crashed: %1").arg(m_process.errorString()));
    }
}

void RuntimeManager::onStartupTimeout()
{
    if (m_state != State::STARTING)
        return;
    m_healthPollTimer.stop();
    setError(QStringLiteral("Runtime startup timed out before readiness"));
    if (isProcessRunning())
        requestTermination();
}

void RuntimeManager::onShutdownGraceTimeout()
{
    if (isProcessRunning())
        requestTermination();
}

void RuntimeManager::onForceKillTimeout()
{
    if (isProcessRunning()) {
        if (m_shutdownRequested) {
            if (!m_shutdownForced) {
                m_shutdownForced = true;
                emit shutdownForcedChanged();
            }
            m_shutdownKilled = true;
            setState(State::STOPPING, QStringLiteral("Shutdown timed out; force-killing runtime"));
        }
        m_process.kill();
    }
}

void RuntimeManager::pollHealth()
{
    if (m_state != State::STARTING || m_healthReply)
        return;

    QUrl url = m_config.healthUrl;
    QUrlQuery query(url);
    query.removeAllQueryItems(QStringLiteral("expected_launch_id"));
    query.addQueryItem(QStringLiteral("expected_launch_id"), m_config.expectedLaunchId);
    url.setQuery(query);

    QNetworkRequest request(url);
    request.setTransferTimeout(m_config.requestTimeoutMs);
    QNetworkReply *reply = m_network.get(request);
    m_healthReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        if (m_healthReply == reply)
            m_healthReply = nullptr;
        const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const auto networkError = reply->error();
        const QByteArray body = reply->readAll();
        reply->deleteLater();
        if (m_state != State::STARTING)
            return;

        // A connection refusal is expected while the child is importing and
        // binding. Any responding HTTP service must prove its identity.
        if (status == 0 && networkError != QNetworkReply::NoError)
            return;
        if (status != 200) {
            const QString reason = status == 409
                ? QStringLiteral("A different runtime identity already owns the configured port")
                : QStringLiteral("Backend health check failed with HTTP %1; the port may belong to another service").arg(status);
            failHealthCheck(reason);
            return;
        }

        QJsonParseError parseError{};
        const QJsonDocument document = QJsonDocument::fromJson(body, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
            failHealthCheck(QStringLiteral("Backend health check returned invalid JSON"));
            return;
        }
        const QJsonObject health = document.object();
        if (health.value(QStringLiteral("launch_id")).toString() != m_config.expectedLaunchId) {
            failHealthCheck(QStringLiteral("Backend health check returned a mismatched launch identity"));
            return;
        }
        if (health.value(QStringLiteral("runtime_status")).toString() != QStringLiteral("ready")) {
            failHealthCheck(QStringLiteral("Backend responded but did not report READY"));
            return;
        }
        if (m_config.requireDesktopSafeMode
            && !health.value(QStringLiteral("desktop_safe_mode")).toBool()) {
            failHealthCheck(QStringLiteral("Backend is responding but desktop-safe mode is not active"));
            return;
        }

        m_healthPollTimer.stop();
        m_startupTimer.stop();
        setState(State::READY, m_config.requireDesktopSafeMode
                     ? QStringLiteral("Backend ready; voice and models are not started")
                     : QStringLiteral("Backend ready"));
    });
}

void RuntimeManager::failHealthCheck(const QString &message)
{
    m_healthPollTimer.stop();
    m_startupTimer.stop();
    setError(message);
    if (isProcessRunning())
        requestTermination();
}

QString RuntimeManager::stateNameFor(State state)
{
    switch (state) {
    case State::STOPPED: return QStringLiteral("STOPPED");
    case State::STARTING: return QStringLiteral("STARTING");
    case State::READY: return QStringLiteral("READY");
    case State::STOPPING: return QStringLiteral("STOPPING");
    case State::ERROR: return QStringLiteral("ERROR");
    }
    return QStringLiteral("ERROR");
}

void RuntimeManager::setState(State state, const QString &message)
{
    if (m_state == state && m_statusMessage == message)
        return;
    m_state = state;
    m_statusMessage = message;
    emit stateChanged();
}

void RuntimeManager::setError(const QString &message)
{
    m_failureActive = true;
    m_lastError = message;
    emit lastErrorChanged(m_lastError);
    setState(State::ERROR, message);
}

void RuntimeManager::checkReadiness(const QByteArray &bytes)
{
    m_readinessBuffer.append(bytes);
    if (m_readinessBuffer.contains(m_config.readinessMarker)) {
        m_readinessBuffer.clear();
        m_startupTimer.stop();
        setState(State::READY, QStringLiteral("Runtime reported ready"));
        return;
    }
    constexpr qsizetype maxBufferedBytes = 8192;
    if (m_readinessBuffer.size() > maxBufferedBytes)
        m_readinessBuffer.remove(0, m_readinessBuffer.size() - maxBufferedBytes);
}

void RuntimeManager::requestTermination()
{
    m_shutdownGraceTimer.stop();
    if (!isProcessRunning())
        return;
    if (m_shutdownRequested && !m_shutdownForced) {
        m_shutdownForced = true;
        emit shutdownForcedChanged();
    }
    m_process.terminate();
    m_forceKillTimer.start(m_config.terminateTimeoutMs);
}

void RuntimeManager::flushOutput()
{
    onStandardOutput();
    onStandardError();
}
