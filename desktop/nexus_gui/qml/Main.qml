import QtQuick
import QtQuick.Window
import QtQuick3D
import QtQuick3D.Effects
import Nexus.Native

Window {
    id: root
    visible: true
    width: 1100; height: 760
    minimumWidth: 820; minimumHeight: 600
    title: "Nexus — Layered Energy Field"
    color: "#03070c"
    property bool talking: false
    property bool loopRunning: true
    property bool highQuality: false
    property real animationTime: 0
    property real referenceTime: -1
    property real energy: 0
    property string backendName: "INITIALIZING"
    property bool gpuAvailable: true
    property string runtimeDiagnostic: ""

    Connections {
        target: appController
        function onRuntimeStandardError(text) {
            root.runtimeDiagnostic = (root.runtimeDiagnostic + text).slice(-500)
        }
    }

    FrameAnimation {
        running: root.visible
        onTriggered: {
            if (root.referenceTime >= 0) root.animationTime = root.referenceTime
            else if (root.loopRunning) root.animationTime += Math.min(frameTime, 0.1)
            const phrase = Math.pow(Math.max(0, Math.sin(elapsedTime * 1.65)), 0.6)
            const syllable = 0.3 + 0.7 * Math.pow(Math.abs(Math.sin(elapsedTime * 9.7)
                * Math.cos(elapsedTime * 3.1)), 0.65)
            const target = root.talking ? 0.1 + phrase * syllable * 0.9 : 0
            root.energy += (target - root.energy) * (1 - Math.exp(-9 * Math.min(frameTime, 0.1)))
        }
    }
    Shortcut { sequence: "Space"; onActivated: root.talking = !root.talking }
    Shortcut { sequence: "Escape"; onActivated: root.visibility = Window.Windowed }
    Shortcut {
        sequence: "F11"
        onActivated: root.visibility = root.visibility === Window.FullScreen
            ? Window.Windowed : Window.FullScreen
    }

    // Reference-style orange diagnostic header and subdued technical border.
    Rectangle {
        x: 22; y: 22; width: parent.width - 44; height: 13
        color: "#d98219"; border.color: "#9ed9cb"; border.width: 1
        Text {
            x: 7; anchors.verticalCenter: parent.verticalCenter
            text: "NEXUS A.I."
            color: "#f0ffed"; font.pixelSize: 11; font.bold: true
        }
    }
    Row {
        x: 25; y: 15; spacing: 4
        Repeater {
            model: 16
            Rectangle { width: 3; height: 2; color: index % 3 === 0 ? "#dea230" : "#396874" }
        }
    }
    Text {
        x: 25; y: 39
        width: parent.width - 50
        text: appController.runtimeState === "ERROR"
            ? "BACKEND ERROR / " + appController.lastRuntimeError + " " + root.runtimeDiagnostic
            : "BACKEND " + appController.runtimeState + " / VOICE DISABLED"
        color: appController.runtimeState === "ERROR" ? "#d68a83" : "#66838a"
        font.pixelSize: 8
        font.letterSpacing: 0.7
        elide: Text.ElideRight
    }
    Rectangle {
        id: frame
        x: 22; y: 57
        width: parent.width - 44; height: parent.height - 122
        color: "#04080d"
        border.color: "#344957"; border.width: 1
        Rectangle {
            anchors.fill: parent; anchors.margins: 4
            color: "transparent"; border.color: "#142837"
        }
        View3D {
            id: scene
            explicitTextureWidth: root.highQuality ? 1100 : 800
            explicitTextureHeight: Math.round(explicitTextureWidth * height / width)
            anchors.fill: parent; anchors.margins: 8
            visible: root.gpuAvailable
            environment: SceneEnvironment {
                backgroundMode: SceneEnvironment.Color
                clearColor: "#03070c"
                antialiasingMode: SceneEnvironment.NoAA
                effects: HDRBloomTonemap { bloomThreshold: 0.55; exposure: 0; blurFalloff: 1; tonemappingLerp: 0.75 }
            }
            PerspectiveCamera { id: sceneCamera; z: 650; clipNear: 5; clipFar: 1500; fieldOfView: 48 }
            camera: sceneCamera
            property real assembly: Math.pow(Math.max(0, Math.sin((root.animationTime % 20) * Math.PI / 20)), 0.65)
            Node {
                eulerRotation: Qt.vector3d(9*Math.sin(root.animationTime*0.13), root.animationTime*3, -8)
                Model {
                    geometry: HoloCoreGeometry { }
                    materials: CustomMaterial {
                        shadingMode: CustomMaterial.Unshaded
                        cullMode: Material.NoCulling
                        sourceBlend: CustomMaterial.One; destinationBlend: CustomMaterial.One
                        depthDrawMode: Material.NeverDepthDraw
                        property real time: root.animationTime
                        property real assembly: scene.assembly
                        property real speech: root.energy
                        property vector3d tint: Qt.vector3d(0.18,0.9,1.0)
                        property real brightness: 1.9
                        vertexShader: "qrc:/shaders/core.vert"
                        fragmentShader: "qrc:/shaders/core.frag"
                    }
                }
                Model {
                    geometry: HoloCoreGeometry { outer: true }
                    materials: CustomMaterial {
                        shadingMode: CustomMaterial.Unshaded
                        cullMode: Material.NoCulling
                        sourceBlend: CustomMaterial.One; destinationBlend: CustomMaterial.One
                        depthDrawMode: Material.NeverDepthDraw
                        property real time: root.animationTime
                        property real assembly: scene.assembly
                        property real speech: root.energy
                        property vector3d tint: Qt.vector3d(0.025,0.35,1.0)
                        property real brightness: 1.5
                        vertexShader: "qrc:/shaders/core.vert"
                        fragmentShader: "qrc:/shaders/core.frag"
                    }
                }
                Model {
                    geometry: NeuralGeometry { }
                    materials: CustomMaterial {
                        shadingMode: CustomMaterial.Unshaded
                        cullMode: Material.NoCulling
                        sourceBlend: CustomMaterial.One; destinationBlend: CustomMaterial.One
                        depthDrawMode: Material.NeverDepthDraw
                        property real time: root.animationTime
                        property real assembly: scene.assembly
                        property real speech: root.energy
                        vertexShader: "qrc:/shaders/core.vert"
                        fragmentShader: "qrc:/shaders/neural.frag"
                    }
                }
            }
        }
        Repeater {
            model: 4
            Rectangle {
                required property int index
                width: 24; height: 2
                x: index % 2 === 0 ? 0 : frame.width - width
                y: index < 2 ? 0 : frame.height - height
                color: "#557781"
            }
        }
        Text {
            x: 13; y: 12
            text: "FIELD / NATIVE 3D"
            color: "#294354"; font.pixelSize: 8; font.letterSpacing: 1.2
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 13
            anchors.bottom: parent.bottom; anchors.bottomMargin: 11
            text: root.backendName
            color: "#3a5865"; font.pixelSize: 8; font.letterSpacing: 1.0
        }
        Text {
            anchors.centerIn: parent; visible: !root.gpuAvailable
            text: "GPU rendering unavailable. Use the Direct3D 11 launcher."
            color: "#9bbec7"; font.pixelSize: 15
        }
    }
    Row {
        x: 24; anchors.bottom: parent.bottom; anchors.bottomMargin: 25; spacing: 9
        Repeater {
            model: ["Cycle", "Talking", "Detail", "Restart"]
            delegate: Rectangle {
                required property string modelData
                required property int index
                width: 78; height: 25; radius: 2
                property bool active: index === 0 ? root.loopRunning
                    : index === 1 ? root.talking : index === 2 ? root.highQuality : false
                color: active ? "#10232b" : "#060e14"
                border.color: active ? "#31505e" : "#142a36"
                Text {
                    anchors.centerIn: parent; text: modelData
                    color: parent.active ? "#add0da" : "#4a6e7b"; font.pixelSize: 10
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (index === 0) { root.referenceTime = -1; root.loopRunning = !root.loopRunning }
                        else if (index === 1) root.talking = !root.talking
                        else if (index === 2) root.highQuality = !root.highQuality
                        else { root.referenceTime = -1; root.animationTime = 0; root.loopRunning = true }
                    }
                }
            }
        }
    }
    Text {
        anchors.right: parent.right; anchors.rightMargin: 24
        anchors.bottom: parent.bottom; anchors.bottomMargin: 33
        text: root.talking ? "SYNTHETIC SPEECH / SPACE TO TOGGLE"
            : "20s REFERENCE CYCLE  /  F11 FULL SCREEN"
        color: "#3a5967"; font.pixelSize: 9; font.letterSpacing: 0.5
    }
}
