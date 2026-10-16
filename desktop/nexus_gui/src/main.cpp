#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <QGuiApplication>
#include <QIcon>
#include "app/AppController.h"
#ifdef Q_OS_WIN
#include "runtime/InstanceGuard.h"
#endif
#include "Visuals/HoloCoreGeometry.h"
#include "Visuals/NeuralGeometry.h"
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickWindow>
#include <QEvent>
#include <QSGRendererInterface>
#include <QCommandLineParser>
#include <QTimer>
#include <QFontDatabase>
#include <QDir>
#include <QFileInfo>
#include <QElapsedTimer>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDebug>
#include <QProcessEnvironment>
#include <QRandomGenerator>
#include <QUrl>
#include <QUrlQuery>
#include <QUuid>
#include <algorithm>
#include <vector>
#include <atomic>
#ifdef Q_OS_WIN
#include <d3d11.h>
#include <dxgi.h>
#endif

static QString findNexusProjectRoot()
{
    QStringList candidates;
    const QString configured = qEnvironmentVariable("NEXUS_PROJECT_ROOT").trimmed();
    if (!configured.isEmpty())
        candidates << QDir::cleanPath(configured);

    QDir executableDir(QCoreApplication::applicationDirPath());
    while (true) {
        candidates << executableDir.absolutePath();
        if (!executableDir.cdUp())
            break;
    }
    QDir currentDir(QDir::currentPath());
    while (true) {
        candidates << currentDir.absolutePath();
        if (!currentDir.cdUp())
            break;
    }

    for (const QString &candidate : candidates) {
        const QDir root(candidate);
        if (QFileInfo::exists(root.filePath("ui/server.py"))
            && QFileInfo::exists(root.filePath("venv/Scripts/python.exe")))
            return root.absolutePath();
    }
    return {};
}

static QString newShutdownSecret()
{
    QString secret;
    for (int index = 0; index < 4; ++index) {
        secret += QString::number(QRandomGenerator::system()->generate64(), 16)
                      .rightJustified(16, QLatin1Char('0'));
    }
    return secret;
}

class RuntimeCloseEventFilter final : public QObject
{
public:
    RuntimeCloseEventFilter(AppController *controller, RuntimeManager *manager,
                            QQuickWindow *window, QObject *parent = nullptr)
        : QObject(parent), m_controller(controller), m_manager(manager), m_window(window)
    {
    }

    bool eventFilter(QObject *watched, QEvent *event) override
    {
        if (watched == m_window && event->type() == QEvent::Close
            && m_manager->isProcessRunning()) {
            event->ignore();
            if (!m_closePending) {
                m_closePending = true;
                m_controller->stopRuntime();
            }
            return true;
        }
        return QObject::eventFilter(watched, event);
    }

    void closeWhenStopped()
    {
        if (!m_closePending || m_manager->isProcessRunning())
            return;
        m_closePending = false;
        m_window->close();
    }

private:
    AppController *m_controller;
    RuntimeManager *m_manager;
    QQuickWindow *m_window;
    bool m_closePending = false;
};

int main(int argc, char *argv[]) {
#ifdef Q_OS_WIN
    // Outlive Qt and every runtime owner, including their exit-time cleanup.
    InstanceGuard instanceGuard;
#endif
    QGuiApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("Nexus"));
    QCoreApplication::setOrganizationName(QStringLiteral("Nexus"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("nexus.local"));
    QIcon applicationIcon(QStringLiteral(":/branding/nexus.ico"));
    applicationIcon.addFile(QStringLiteral(":/branding/nexus-icon-512.png"), QSize(512, 512));
    applicationIcon.addFile(QStringLiteral(":/branding/nexus-icon-1024.png"), QSize(1024, 1024));
    app.setWindowIcon(applicationIcon);
#ifdef Q_OS_WIN
    QFontDatabase::addApplicationFont(QDir::fromNativeSeparators(
        qEnvironmentVariable("WINDIR", "C:/Windows")) + "/Fonts/segoeui.ttf");
    app.setFont(QFont("Segoe UI"));
    // Use Qt's native Windows GPU backend, not the software scene graph.
    QQuickWindow::setGraphicsApi(QSGRendererInterface::Direct3D11);
#endif
    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addOption({"snapshot", "Save a rendered preview and exit.", "path"});
    parser.addOption({"talking-preview", "Start with simulated talking."});
    parser.addOption({"benchmark", "Record approximately five seconds after two seconds warmup, then exit.", "json-path"});
    parser.addOption({"reference-time", "Hold the reference cycle at a specified second (0-20).", "seconds"});
    parser.process(app);
    const QString projectRoot = findNexusProjectRoot();
#ifdef Q_OS_WIN
    const auto instanceResult = instanceGuard.acquire(
        projectRoot.isEmpty() ? QCoreApplication::applicationDirPath() : projectRoot);
    if (instanceResult == InstanceGuard::Result::AlreadyRunning) {
        qWarning() << "Nexus is already running for this installation and Windows user; backend not started";
        return 10;
    }
    if (instanceResult == InstanceGuard::Result::Error) {
        qCritical().noquote() << "Nexus instance guard:" << instanceGuard.errorString()
                             << "- backend not started";
        return 11;
    }
#endif
    qmlRegisterType<HoloCoreGeometry>("Nexus.Native",1,0,"HoloCoreGeometry");
    qmlRegisterType<NeuralGeometry>("Nexus.Native",1,0,"NeuralGeometry");
    AppController appController;
    RuntimeLaunchConfig runtimeConfig;
    const QString launchId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const QString shutdownSecret = newShutdownSecret();
    QProcessEnvironment childEnvironment = QProcessEnvironment::systemEnvironment();
    childEnvironment.insert(QStringLiteral("NEXUS_DESKTOP_SAFE"), QStringLiteral("1"));
    childEnvironment.insert(QStringLiteral("NEXUS_LAUNCH_ID"), launchId);
    childEnvironment.insert(QStringLiteral("NEXUS_SHUTDOWN_SECRET"), shutdownSecret);
    childEnvironment.insert(QStringLiteral("JARVIS_VOICE_ENGINE"), QStringLiteral("0"));
    childEnvironment.insert(QStringLiteral("JARVIS_LOCAL_VOICE"), QStringLiteral("0"));
    childEnvironment.insert(QStringLiteral("JARVIS_AUTO_OPEN"), QStringLiteral("0"));
    childEnvironment.insert(QStringLiteral("JARVIS_HOST"), QStringLiteral("127.0.0.1"));
    runtimeConfig.inheritEnvironment = false;
    runtimeConfig.environment = childEnvironment;
    runtimeConfig.expectedLaunchId = launchId;
    runtimeConfig.requireDesktopSafeMode = true;
    runtimeConfig.readinessMarker.clear();
    runtimeConfig.startupTimeoutMs = 45000;
    runtimeConfig.healthPollIntervalMs = 250;
    runtimeConfig.requestTimeoutMs = 1200;
    runtimeConfig.shutdownGraceMs = 4000;
    runtimeConfig.terminateTimeoutMs = 1500;
    runtimeConfig.healthUrl = QUrl(QStringLiteral("http://127.0.0.1:%1/healthz")
                                       .arg(childEnvironment.value("JARVIS_PORT", "5000")));
    runtimeConfig.shutdownUrl = QUrl(QStringLiteral("http://127.0.0.1:%1/shutdown")
                                         .arg(childEnvironment.value("JARVIS_PORT", "5000")));
    runtimeConfig.shutdownSecret = shutdownSecret;
    if (!projectRoot.isEmpty()) {
        runtimeConfig.program = QDir(projectRoot).filePath("venv/Scripts/python.exe");
        runtimeConfig.arguments = {QStringLiteral("-u"), QDir(projectRoot).filePath("ui/server.py")};
        runtimeConfig.workingDirectory = projectRoot;
        childEnvironment.insert(QStringLiteral("NEXUS_PROJECT_ROOT"), projectRoot);
        runtimeConfig.environment = childEnvironment;
    }
    appController.configureRuntime(runtimeConfig);
    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty("appController", &appController);
    QObject::connect(&engine,&QQmlApplicationEngine::objectCreationFailed,&app,
        [] {
            qCritical() << "Nexus could not create its QML interface.";
            QCoreApplication::exit(1);
        },Qt::QueuedConnection);
    engine.loadFromModule("Nexus.Gui","Main");
    if (engine.rootObjects().isEmpty()) {
        qCritical() << "Nexus QML interface loaded no root window.";
        return 1;
    }
    auto *window=qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    if (!window) {
        qCritical() << "Nexus QML root object is not a window.";
        return 1;
    }
    window->setIcon(app.windowIcon());
    RuntimeManager *runtimeManager = appController.runtimeManager();
    auto *closeGuard = new RuntimeCloseEventFilter(
        &appController, runtimeManager, window, &app);
    window->installEventFilter(closeGuard);
    QObject::connect(runtimeManager, &RuntimeManager::stateChanged, &app,
        [closeGuard] { closeGuard->closeWhenStopped(); });
    if (parser.isSet("talking-preview")) window->setProperty("talking",true);
    appController.startRuntime();
    if (parser.isSet("reference-time")) {
        bool ok=false; const double t=parser.value("reference-time").toDouble(&ok);
        if(!ok || t<0 || t>=20) return 6;
        window->setProperty("referenceTime",t);
        window->setProperty("animationTime",t);
    }
    std::atomic<bool> gpuReady{false};
    QString backend="INITIALIZING";
    QString adapterName="unverified";
    bool hardwareAdapter=false;
    std::atomic<bool> adapterChecked{false};
#ifdef Q_OS_WIN
    QObject::connect(window,&QQuickWindow::beforeRendering,&app,[&] {
        if(adapterChecked.exchange(true)) return;
        auto *device=static_cast<ID3D11Device *>(window->rendererInterface()->getResource(window,QSGRendererInterface::DeviceResource));
        if(!device) return;
        IDXGIDevice *dxgiDevice=nullptr;
        if(FAILED(device->QueryInterface(__uuidof(IDXGIDevice),reinterpret_cast<void **>(&dxgiDevice)))) return;
        IDXGIAdapter *adapter=nullptr;
        dxgiDevice->GetAdapter(&adapter); dxgiDevice->Release();
        if(!adapter) return;
        DXGI_ADAPTER_DESC desc{};
        adapter->GetDesc(&desc);
        QString name=QString::fromWCharArray(desc.Description);
        bool hardware=false;
        IDXGIAdapter1 *adapter1=nullptr;
        if(SUCCEEDED(adapter->QueryInterface(__uuidof(IDXGIAdapter1),reinterpret_cast<void **>(&adapter1)))) {
            DXGI_ADAPTER_DESC1 desc1{};
            if(SUCCEEDED(adapter1->GetDesc1(&desc1))) hardware=(desc1.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)==0;
            adapter1->Release();
        }
        adapter->Release();
        QMetaObject::invokeMethod(&app,[&,name,hardware] { adapterName=name; hardwareAdapter=hardware; },Qt::QueuedConnection);
    },Qt::DirectConnection);
#endif
    QElapsedTimer clock;
    std::vector<double> frameMs;
    qint64 previous=0;
    bool firstFrame=true;
    const bool benchmark=parser.isSet("benchmark");
    const bool snapshot=parser.isSet("snapshot");
    const QString benchmarkPath=parser.value("benchmark");
    const QString snapshotPath=parser.value("snapshot");
    QObject::connect(window,&QQuickWindow::frameSwapped,&app,[&] {
        if(firstFrame) {
            firstFrame=false;
            clock.start();
            const auto api=window->rendererInterface()->graphicsApi();
            switch(api) {
            case QSGRendererInterface::Direct3D11: backend="GPU / DIRECT3D 11"; break;
            case QSGRendererInterface::Vulkan: backend="GPU / VULKAN"; break;
            case QSGRendererInterface::OpenGL: backend="GPU / OPENGL"; break;
            case QSGRendererInterface::Metal: backend="GPU / METAL"; break;
            case QSGRendererInterface::Software: backend="SOFTWARE / UNSUPPORTED"; break;
            default: backend="UNVERIFIED BACKEND"; break;
            }
            const bool ready=api!=QSGRendererInterface::Software && api!=QSGRendererInterface::Unknown;
            gpuReady.store(ready);
            window->setProperty("backendName",backend);
            window->setProperty("gpuAvailable",ready);
            if(snapshot) QTimer::singleShot(2400,&app,[&] {
                const auto image=window->grabWindow();
                app.exit(gpuReady.load() && !image.isNull() && image.save(snapshotPath)?0:3);
            });
            if(benchmark) QTimer::singleShot(7000,&app,[&] {
                std::sort(frameMs.begin(),frameMs.end());
                double sum=0; for(double v:frameMs) sum+=v;
                QJsonObject report{{"renderer","Qt Quick 3D / custom geometry + HDRBloomTonemap"},{"window_width",window->width()},{"window_height",window->height()},{"talking",window->property("talking").toBool()},{"backend",backend},{"gpu_initialized",gpuReady.load()},
                    {"adapter",adapterName},{"hardware_adapter_verified",hardwareAdapter},
                    {"frames_after_warmup",int(frameMs.size())},{"warmup_seconds",2},
                    {"reference_phase_seconds",window->property("animationTime").toDouble()},
                    {"measured_interval_seconds",sum/1000.0},
                    {"average_frame_ms",frameMs.empty()?0:sum/frameMs.size()},
                    {"median_frame_ms",frameMs.empty()?0:frameMs[frameMs.size()/2]},
                    {"p95_frame_ms",frameMs.empty()?0:frameMs[std::min(frameMs.size()-1,size_t(frameMs.size()*0.95))]},
                    {"observed_fps",sum>0?1000.0*frameMs.size()/sum:0},
                    {"note","FrameSwapped intervals include presentation and host scheduling; not isolated GPU execution times. See hardware_adapter_verified."}};
                QFile file(benchmarkPath);
                const bool ok=file.open(QIODevice::WriteOnly) && file.write(QJsonDocument(report).toJson())>0;
                app.exit(ok && gpuReady.load() && !frameMs.empty()?0:4);
            });
        }
        if(benchmark) {
            const qint64 now=clock.nsecsElapsed();
            if(previous && now>2000000000LL) frameMs.push_back((now-previous)/1000000.0);
            previous=now;
        }
    });
    if(snapshot || benchmark) QTimer::singleShot(60000,&app,[&] { app.exit(5); });
    return app.exec();
}
