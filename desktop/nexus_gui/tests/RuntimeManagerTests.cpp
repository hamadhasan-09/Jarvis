#include "../src/runtime/RuntimeManager.h"

#include <QCoreApplication>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTcpServer>
#include <QTcpSocket>
#include <QtTest>

class HealthTestServer final : public QTcpServer
{
public:
    int responseStatus = 200;
    QByteArray responseBody;
    QByteArray lastRequest;

    HealthTestServer()
    {
        connect(this, &QTcpServer::newConnection, this, [this] {
            while (hasPendingConnections()) {
                QTcpSocket *socket = nextPendingConnection();
                connect(socket, &QTcpSocket::readyRead, this, [this, socket] {
                    lastRequest += socket->readAll();
                    if (!lastRequest.contains("\r\n\r\n"))
                        return;
                    const QByteArray reason = responseStatus == 200
                        ? QByteArrayLiteral("OK") : QByteArrayLiteral("Conflict");
                    const QByteArray response = QByteArrayLiteral("HTTP/1.1 ")
                        + QByteArray::number(responseStatus) + ' ' + reason
                        + QByteArrayLiteral("\r\nContent-Type: application/json\r\nContent-Length: ")
                        + QByteArray::number(responseBody.size())
                        + QByteArrayLiteral("\r\nConnection: close\r\n\r\n") + responseBody;
                    socket->write(response);
                    socket->disconnectFromHost();
                });
            }
        });
    }

    QUrl url(const QString &path) const
    {
        return QUrl(QStringLiteral("http://127.0.0.1:%1%2").arg(serverPort()).arg(path));
    }
};

class RuntimeManagerTests final : public QObject
{
    Q_OBJECT

private slots:
    void initTestCase()
    {
        m_fixturePath = qEnvironmentVariable("NEXUS_RUNTIME_TEST_FIXTURE");
        if (m_fixturePath.isEmpty()) {
            m_fixturePath = QCoreApplication::applicationDirPath() +
                            QStringLiteral("/RuntimeTestFixture.exe");
        }
        QVERIFY2(QFileInfo::exists(m_fixturePath),
                 qPrintable(QStringLiteral("Runtime fixture not found: %1").arg(m_fixturePath)));
    }

    void startupCapturesOutputAndStopsGracefully()
    {
        RuntimeManager manager;
        RuntimeLaunchConfig config;
        config.program = m_fixturePath;
        config.arguments = {QStringLiteral("wait")};
        config.readinessMarker = QByteArrayLiteral("TEST_RUNTIME_READY");
        config.gracefulShutdownInput = QByteArrayLiteral("STOP\n");
        config.startupTimeoutMs = 3000;
        config.shutdownGraceMs = 500;
        config.terminateTimeoutMs = 500;
        manager.setLaunchConfig(config);

        QString capturedStdout;
        QString capturedStderr;
        QSignalSpy stateSpy(&manager, &RuntimeManager::stateChanged);
        connect(&manager, &RuntimeManager::standardOutputReceived, this,
                [&capturedStdout](const QString &text) { capturedStdout += text; });
        connect(&manager, &RuntimeManager::standardErrorReceived, this,
                [&capturedStderr](const QString &text) { capturedStderr += text; });

        QVERIFY(manager.start());
        QCOMPARE(manager.state(), RuntimeManager::State::STARTING);
        const auto readyDiagnostic = [&] {
            return QStringLiteral("state=%1 running=%2 error=%3 stdout=%4 stderr=%5")
                .arg(manager.stateName())
                .arg(manager.isProcessRunning())
                .arg(manager.lastError(), capturedStdout, capturedStderr);
        };
        QTRY_VERIFY2_WITH_TIMEOUT(manager.state() == RuntimeManager::State::READY,
                                  qPrintable(readyDiagnostic()), 5000);
        QCOMPARE(manager.stateName(), QStringLiteral("READY"));
        QVERIFY(capturedStdout.contains(QStringLiteral("FIXTURE_STDOUT: booting")));
        QVERIFY(capturedStdout.contains(QStringLiteral("TEST_RUNTIME_READY")));
        QVERIFY(capturedStderr.contains(QStringLiteral("FIXTURE_STDERR: diagnostic")));

        manager.stop();
        QCOMPARE(manager.state(), RuntimeManager::State::STOPPING);
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::STOPPED, 3000);
        QVERIFY(capturedStdout.contains(QStringLiteral("FIXTURE_GRACEFUL_STOP")));
        QVERIFY(!manager.isProcessRunning());
        QVERIFY(stateSpy.count() >= 4);
    }

    void startupTimeoutReportsErrorAndTerminatesChild()
    {
        RuntimeManager manager;
        RuntimeLaunchConfig config;
        config.program = m_fixturePath;
        config.arguments = {QStringLiteral("timeout")};
        config.startupTimeoutMs = 200;
        config.shutdownGraceMs = 0;
        config.terminateTimeoutMs = 500;
        manager.setLaunchConfig(config);

        QVERIFY(manager.start());
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::ERROR, 3000);
        QVERIFY(manager.lastError().contains(QStringLiteral("timed out"), Qt::CaseInsensitive));
        QTRY_VERIFY_WITH_TIMEOUT(!manager.isProcessRunning(), 3000);
        QCOMPARE(manager.state(), RuntimeManager::State::ERROR);
    }

    void unexpectedExitIsReported()
    {
        RuntimeManager manager;
        RuntimeLaunchConfig config;
        config.program = m_fixturePath;
        config.arguments = {QStringLiteral("exit")};
        config.startupTimeoutMs = 3000;
        manager.setLaunchConfig(config);

        QVERIFY(manager.start());
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::ERROR, 5000);
        QVERIFY2(manager.lastError().contains(QStringLiteral("exited unexpectedly")),
                 qPrintable(QStringLiteral("Unexpected-exit error: %1").arg(manager.lastError())));
    }

    void healthReadinessRequiresMatchingIdentityAndDesktopSafeMode()
    {
        HealthTestServer healthServer;
        QVERIFY(healthServer.listen(QHostAddress::LocalHost, 0));
        const QString launchId = QStringLiteral("runtime-test-launch");
        healthServer.responseBody = QJsonDocument(QJsonObject{
            {QStringLiteral("runtime_status"), QStringLiteral("ready")},
            {QStringLiteral("launch_id"), launchId},
            {QStringLiteral("desktop_safe_mode"), true},
        }).toJson(QJsonDocument::Compact);

        RuntimeManager manager;
        RuntimeLaunchConfig config;
        config.program = m_fixturePath;
        config.arguments = {QStringLiteral("wait")};
        config.healthUrl = healthServer.url(QStringLiteral("/healthz"));
        config.expectedLaunchId = launchId;
        config.requireDesktopSafeMode = true;
        config.gracefulShutdownInput = QByteArrayLiteral("STOP\n");
        config.startupTimeoutMs = 3000;
        config.healthPollIntervalMs = 50;
        config.requestTimeoutMs = 500;
        config.shutdownGraceMs = 500;
        config.terminateTimeoutMs = 500;
        manager.setLaunchConfig(config);

        QVERIFY(manager.start());
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::READY, 3000);
        QVERIFY(healthServer.lastRequest.contains(launchId.toUtf8()));
        manager.stop();
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::STOPPED, 3000);
    }

    void healthReadinessRejectsMismatchedIdentity()
    {
        HealthTestServer healthServer;
        QVERIFY(healthServer.listen(QHostAddress::LocalHost, 0));
        healthServer.responseBody = QJsonDocument(QJsonObject{
            {QStringLiteral("runtime_status"), QStringLiteral("ready")},
            {QStringLiteral("launch_id"), QStringLiteral("another-process")},
            {QStringLiteral("desktop_safe_mode"), true},
        }).toJson(QJsonDocument::Compact);

        RuntimeManager manager;
        RuntimeLaunchConfig config;
        config.program = m_fixturePath;
        config.arguments = {QStringLiteral("wait")};
        config.healthUrl = healthServer.url(QStringLiteral("/healthz"));
        config.expectedLaunchId = QStringLiteral("my-process");
        config.requireDesktopSafeMode = true;
        config.startupTimeoutMs = 3000;
        config.healthPollIntervalMs = 50;
        config.requestTimeoutMs = 500;
        config.terminateTimeoutMs = 500;
        manager.setLaunchConfig(config);

        QVERIFY(manager.start());
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::ERROR, 3000);
        QVERIFY(manager.lastError().contains(QStringLiteral("mismatched launch identity")));
        QTRY_VERIFY_WITH_TIMEOUT(!manager.isProcessRunning(), 3000);
        QCOMPARE(manager.state(), RuntimeManager::State::ERROR);
    }

    void occupiedPortIsReportedAsAnIdentityConflict()
    {
        HealthTestServer healthServer;
        QVERIFY(healthServer.listen(QHostAddress::LocalHost, 0));
        healthServer.responseStatus = 409;
        healthServer.responseBody = QByteArrayLiteral("{}");

        RuntimeManager manager;
        RuntimeLaunchConfig config;
        config.program = m_fixturePath;
        config.arguments = {QStringLiteral("wait")};
        config.healthUrl = healthServer.url(QStringLiteral("/healthz"));
        config.expectedLaunchId = QStringLiteral("new-launch");
        config.startupTimeoutMs = 3000;
        config.healthPollIntervalMs = 50;
        config.requestTimeoutMs = 500;
        config.terminateTimeoutMs = 500;
        manager.setLaunchConfig(config);

        QVERIFY(manager.start());
        QTRY_COMPARE_WITH_TIMEOUT(manager.state(), RuntimeManager::State::ERROR, 3000);
        QVERIFY(manager.lastError().contains(QStringLiteral("different runtime identity")));
        QTRY_VERIFY_WITH_TIMEOUT(!manager.isProcessRunning(), 3000);
    }

private:
    QString m_fixturePath;
};

QTEST_GUILESS_MAIN(RuntimeManagerTests)

#include "RuntimeManagerTests.moc"
