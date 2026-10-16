#ifndef NOMINMAX
#define NOMINMAX
#endif
#include "InstanceGuard.h"

#include <QCryptographicHash>
#include <QDir>
#include <QFileInfo>
#include <windows.h>
#include <sddl.h>
#include <vector>

namespace {
QString windowsError(const QString &operation, DWORD code)
{
    return QStringLiteral("%1 failed (Windows error %2)").arg(operation).arg(code);
}
}

InstanceGuard::~InstanceGuard()
{
    if (m_mutex) {
        ReleaseMutex(m_mutex);
        CloseHandle(m_mutex);
    }
}

QString InstanceGuard::nameForInstallation(const QString &installationPath, QString *error)
{
    if (error)
        error->clear();
    const QFileInfo installation(installationPath);
    const QString canonicalPath = installation.canonicalFilePath();
    if (!installation.isDir() || canonicalPath.isEmpty()) {
        if (error)
            *error = QStringLiteral("Cannot resolve the Nexus installation directory");
        return {};
    }

    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        if (error)
            *error = windowsError(QStringLiteral("OpenProcessToken"), GetLastError());
        return {};
    }
    DWORD bytes = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &bytes);
    const DWORD sizeError = GetLastError();
    if (sizeError != ERROR_INSUFFICIENT_BUFFER || bytes == 0) {
        CloseHandle(token);
        if (error)
            *error = windowsError(QStringLiteral("GetTokenInformation(size)"), sizeError);
        return {};
    }
    std::vector<BYTE> storage(bytes);
    if (!GetTokenInformation(token, TokenUser, storage.data(), bytes, &bytes)) {
        const DWORD code = GetLastError();
        CloseHandle(token);
        if (error)
            *error = windowsError(QStringLiteral("GetTokenInformation"), code);
        return {};
    }
    CloseHandle(token);
    LPWSTR sidText = nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER *>(storage.data())->User.Sid, &sidText)) {
        if (error)
            *error = windowsError(QStringLiteral("ConvertSidToStringSidW"), GetLastError());
        return {};
    }
    QByteArray identity = QString::fromWCharArray(sidText).toUtf8();
    LocalFree(sidText);
    identity.append('\0');
    identity.append(QDir::fromNativeSeparators(canonicalPath).toCaseFolded().toUtf8());
    // Global also covers two logon sessions for this same Windows user.
    return QStringLiteral("Global\\Nexus.Instance.v1.%1").arg(
        QString::fromLatin1(QCryptographicHash::hash(identity, QCryptographicHash::Sha256).toHex()));
}

InstanceGuard::Result InstanceGuard::acquire(const QString &installationPath)
{
    if (m_mutex)
        return Result::Acquired;
    const QString name = nameForInstallation(installationPath, &m_error);
    if (name.isEmpty())
        return Result::Error;

    // The handle is not inheritable. Atomically acquire ownership rather than
    // relying on object existence: Windows releases ownership after a crash.
    HANDLE mutex = CreateMutexW(nullptr, FALSE, reinterpret_cast<LPCWSTR>(name.utf16()));
    if (!mutex) {
        m_error = windowsError(QStringLiteral("CreateMutexW"), GetLastError());
        return Result::Error;
    }
    const DWORD wait = WaitForSingleObject(mutex, 0);
    if (wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED) {
        m_mutex = mutex;
        return Result::Acquired;
    }
    const DWORD code = wait == WAIT_FAILED ? GetLastError() : wait;
    CloseHandle(mutex);
    if (wait == WAIT_TIMEOUT)
        return Result::AlreadyRunning;
    m_error = windowsError(QStringLiteral("WaitForSingleObject"), code);
    return Result::Error;
}
