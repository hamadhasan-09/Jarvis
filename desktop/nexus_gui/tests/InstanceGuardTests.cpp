#ifndef NOMINMAX
#define NOMINMAX
#endif
#include "../src/runtime/InstanceGuard.h"
#include <QCoreApplication>
#include <QDir>
#include <QProcess>
#include <QScopeGuard>
#include <QTemporaryDir>
#include <QTextStream>
#include <QtTest>
#include <windows.h>

class InstanceGuardTests final : public QObject
{
    Q_OBJECT
private:
    static void start(QProcess &process, const QString &installation)
    {
        process.start(QCoreApplication::applicationFilePath(),
                      {QStringLiteral("--hold-guard"), installation});
    }

    static QByteArray result(QProcess &process)
    {
        if (process.state() == QProcess::Starting && !process.waitForStarted(5000))
            return "START_FAILED: " + process.errorString().toUtf8();
        QByteArray output;
        QElapsedTimer timer;
        timer.start();
        while (!output.contains('\n') && timer.elapsed() < 5000) {
            output += process.readAllStandardOutput();
            if (!output.contains('\n'))
                process.waitForReadyRead(100);
        }
        return output.trimmed();
    }

    static bool stop(QProcess &process)
    {
        process.write("STOP\n");
        return process.waitForFinished(5000) && process.exitCode() == 0;
    }

private slots:
    void canonicalIdentityIsStableAndInstallationSpecific()
    {
        QTemporaryDir first;
        QTemporaryDir second;
        QVERIFY(first.isValid() && second.isValid());
        const QString name = InstanceGuard::nameForInstallation(first.path());
        QVERIFY(name.startsWith(QStringLiteral("Global\\Nexus.Instance.v1.")));
        QCOMPARE(name, InstanceGuard::nameForInstallation(first.path() + "/."));
        QCOMPARE(name, InstanceGuard::nameForInstallation(QDir::toNativeSeparators(first.path().toUpper())));
        QVERIFY(name != InstanceGuard::nameForInstallation(second.path()));
    }

    void concurrentProcessIsRejectedAndNormalExitReleasesGuard()
    {
        QTemporaryDir installation;
        QVERIFY(installation.isValid());
        QProcess owner;
        start(owner, installation.path());
        QCOMPARE(result(owner), QByteArray("ACQUIRED"));
        QProcess duplicate;
        start(duplicate, installation.path());
        QCOMPARE(result(duplicate), QByteArray("ALREADY_RUNNING"));
        if (duplicate.state() != QProcess::NotRunning)
            QVERIFY(duplicate.waitForFinished(5000));
        QCOMPARE(duplicate.exitCode(), 10);
        QCOMPARE(owner.state(), QProcess::Running);
        QVERIFY(stop(owner));
        QProcess successor;
        start(successor, installation.path());
        QCOMPARE(result(successor), QByteArray("ACQUIRED"));
        QVERIFY(stop(successor));
    }

    void simultaneousContendersHaveExactlyOneOwner()
    {
        QTemporaryDir installation;
        QVERIFY(installation.isValid());
        QProcess first;
        QProcess second;
        start(first, installation.path());
        start(second, installation.path());
        const auto firstResult = result(first);
        const auto secondResult = result(second);
        QVERIFY((firstResult == "ACQUIRED" && secondResult == "ALREADY_RUNNING")
                || (secondResult == "ACQUIRED" && firstResult == "ALREADY_RUNNING"));
        QProcess &owner = firstResult == "ACQUIRED" ? first : second;
        QProcess &duplicate = firstResult == "ACQUIRED" ? second : first;
        if (duplicate.state() != QProcess::NotRunning)
            QVERIFY(duplicate.waitForFinished(5000));
        QCOMPARE(duplicate.exitCode(), 10);
        QVERIFY(stop(owner));
    }

    void crashReleasesEvenAnExistingAbandonedMutex()
    {
        QTemporaryDir installation;
        QVERIFY(installation.isValid());
        QProcess owner;
        start(owner, installation.path());
        QCOMPARE(result(owner), QByteArray("ACQUIRED"));
        const QString name = InstanceGuard::nameForInstallation(installation.path());
        HANDLE retained = OpenMutexW(SYNCHRONIZE, FALSE, reinterpret_cast<LPCWSTR>(name.utf16()));
        QVERIFY(retained != nullptr);
        const auto closeRetained = qScopeGuard([&] { CloseHandle(retained); });
        owner.kill();
        QVERIFY(owner.waitForFinished(5000));
        QProcess successor;
        start(successor, installation.path());
        QCOMPARE(result(successor), QByteArray("ACQUIRED"));
        QVERIFY(stop(successor));
    }

    void creationFailureIsReportedAndDoesNotAcquire()
    {
        QTemporaryDir installation;
        QVERIFY(installation.isValid());
        const QString name = InstanceGuard::nameForInstallation(installation.path());
        HANDLE collision = CreateEventW(nullptr, FALSE, FALSE, reinterpret_cast<LPCWSTR>(name.utf16()));
        QVERIFY(collision != nullptr);
        const auto closeCollision = qScopeGuard([&] { CloseHandle(collision); });
        InstanceGuard guard;
        QCOMPARE(guard.acquire(installation.path()), InstanceGuard::Result::Error);
        QVERIFY(guard.errorString().contains(QStringLiteral("CreateMutexW")));
    }

    void invalidInstallationFailsClosed()
    {
        QTemporaryDir installation;
        QVERIFY(installation.isValid());
        InstanceGuard guard;
        QCOMPARE(guard.acquire(installation.path() + "/missing"), InstanceGuard::Result::Error);
        QVERIFY(!guard.errorString().isEmpty());
    }
};

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    const QStringList arguments = app.arguments();
    if (arguments.value(1) == QStringLiteral("--mutex-name")) {
        QTextStream(stdout) << InstanceGuard::nameForInstallation(arguments.value(2)) << Qt::endl;
        return 0;
    }
    if (arguments.value(1) == QStringLiteral("--hold-guard")) {
        InstanceGuard guard;
        const auto acquired = guard.acquire(arguments.value(2));
        if (acquired == InstanceGuard::Result::AlreadyRunning) {
            QTextStream(stdout) << "ALREADY_RUNNING" << Qt::endl;
            return 10;
        }
        if (acquired == InstanceGuard::Result::Error) {
            QTextStream(stdout) << "ERROR: " << guard.errorString() << Qt::endl;
            return 11;
        }
        QTextStream(stdout) << "ACQUIRED" << Qt::endl;
        QTextStream(stdin).readLine();
        return 0;
    }
    InstanceGuardTests tests;
    return QTest::qExec(&tests, argc, argv);
}

#include "InstanceGuardTests.moc"
