#include <QCoreApplication>
#include <QThread>

#include <cstdio>
#include <iostream>
#include <string>

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    const QStringList arguments = app.arguments();
    const QString mode = arguments.value(1);
    if (mode == QStringLiteral("wait")) {
        std::fputs("FIXTURE_STDOUT: booting\nTEST_RUNTIME_READY\n", stdout);
        std::fflush(stdout);
        std::fputs("FIXTURE_STDERR: diagnostic\n", stderr);
        std::fflush(stderr);
        std::string line;
        while (std::getline(std::cin, line)) {
            if (line == "STOP") {
                std::fputs("FIXTURE_GRACEFUL_STOP\n", stdout);
                std::fflush(stdout);
                return 0;
            }
        }
        return 4;
    }

    if (mode == QStringLiteral("timeout")) {
        QThread::msleep(10000);
        return 0;
    }

    if (mode == QStringLiteral("exit")) {
        std::fputs("FIXTURE_STDOUT: exiting\n", stdout);
        std::fflush(stdout);
        std::fputs("FIXTURE_STDERR: exit requested\n", stderr);
        std::fflush(stderr);
        return 23;
    }

    std::fputs("Unknown fixture mode\n", stderr);
    std::fflush(stderr);
    return 2;
}
