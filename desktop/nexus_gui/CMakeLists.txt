cmake_minimum_required(VERSION 3.21)
project(Nexus VERSION 0.1 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Qt6 6.12 REQUIRED COMPONENTS Quick Quick3D Network)
qt_standard_project_setup(REQUIRES 6.5)
qt_policy(SET QTP0004 NEW)

add_library(NexusRuntime STATIC
    src/runtime/RuntimeManager.cpp
    src/runtime/RuntimeManager.h
)
target_link_libraries(NexusRuntime PUBLIC Qt6::Core Qt6::Network)

add_library(NexusAppController STATIC
    src/app/AppController.cpp
    src/app/AppController.h
)
target_link_libraries(NexusAppController PUBLIC NexusRuntime Qt6::Core)

qt_add_executable(Nexus
    src/main.cpp
    src/Visuals/HoloCoreGeometry.cpp
    src/Visuals/HoloCoreGeometry.h
    src/Visuals/NeuralGeometry.cpp
    src/Visuals/NeuralGeometry.h
)
qt_add_qml_module(Nexus URI Nexus.Gui VERSION 1.0 QML_FILES qml/Main.qml)
qt_add_resources(Nexus native_materials PREFIX "/" FILES shaders/core.vert shaders/core.frag shaders/neural.frag)

# Keep the original branding assets in the project and embed them so deployed
# applications do not depend on an external icons directory.
get_filename_component(NEXUS_ICON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../icons" ABSOLUTE)
qt_add_resources(Nexus branding
    PREFIX "/branding"
    BASE "${NEXUS_ICON_DIR}"
    FILES
        "${NEXUS_ICON_DIR}/nexus.ico"
        "${NEXUS_ICON_DIR}/nexus-icon-512.png"
        "${NEXUS_ICON_DIR}/nexus-icon-1024.png"
)
target_link_libraries(Nexus PRIVATE NexusAppController Qt6::Quick Qt6::Quick3D)
set_target_properties(Nexus PROPERTIES WIN32_EXECUTABLE TRUE)

if(WIN32)
    add_library(NexusInstanceGuard STATIC
        src/runtime/InstanceGuard.cpp
        src/runtime/InstanceGuard.h
    )
    target_link_libraries(NexusInstanceGuard PUBLIC Qt6::Core PRIVATE advapi32)
    target_link_libraries(Nexus PRIVATE NexusInstanceGuard)

    enable_language(RC)
    set(NEXUS_WINDOWS_ICON "${NEXUS_ICON_DIR}/nexus.ico")
    configure_file(resources/nexus.rc.in "${CMAKE_CURRENT_BINARY_DIR}/nexus.rc" @ONLY)
    set_source_files_properties("${CMAKE_CURRENT_BINARY_DIR}/nexus.rc" PROPERTIES
        OBJECT_DEPENDS "${NEXUS_WINDOWS_ICON}"
    )
    target_sources(Nexus PRIVATE "${CMAKE_CURRENT_BINARY_DIR}/nexus.rc")

    # Deploy the same Qt configuration selected by CMake, including the QML
    # modules discovered from the embedded application QML.  Keep the build
    # layout flat (Nexus.exe and Qt DLLs beside each other) while placing QML
    # modules and plugins in their standard relative directories.
    qt_generate_deploy_qml_app_script(
        TARGET Nexus
        OUTPUT_SCRIPT nexus_deploy_script
        NO_TRANSLATIONS
        NO_COMPILER_RUNTIME
    )
    add_custom_command(TARGET Nexus POST_BUILD
        COMMAND "${CMAKE_COMMAND}"
            "-DQT_DEPLOY_PREFIX=$<TARGET_FILE_DIR:Nexus>"
            "-DQT_DEPLOY_BIN_DIR=."
            "-DQT_DEPLOY_PLUGINS_DIR=plugins"
            "-DQT_DEPLOY_QML_DIR=qml"
            "-DQT_DEPLOY_TRANSLATIONS_DIR=translations"
            -P "${nexus_deploy_script}"
        COMMENT "Deploying Qt runtime and QML modules for Nexus"
        VERBATIM
    )
endif()

include(CTest)
if(BUILD_TESTING)
    find_package(Qt6 6.12 REQUIRED COMPONENTS Test Network)

    get_target_property(_qt_core_debug_location Qt6::Core IMPORTED_LOCATION_DEBUG)
    get_target_property(_qt_core_release_location Qt6::Core IMPORTED_LOCATION_RELEASE)
    if(_qt_core_debug_location)
        get_filename_component(_qt_bin_dir "${_qt_core_debug_location}" DIRECTORY)
    elseif(_qt_core_release_location)
        get_filename_component(_qt_bin_dir "${_qt_core_release_location}" DIRECTORY)
    endif()
    find_program(QT_WINDEPLOYQT_EXECUTABLE NAMES windeployqt HINTS "${_qt_bin_dir}")

    add_executable(RuntimeTestFixture tests/RuntimeTestFixture.cpp)
    target_link_libraries(RuntimeTestFixture PRIVATE Qt6::Core)

    add_executable(RuntimeManagerTests tests/RuntimeManagerTests.cpp)
    target_link_libraries(RuntimeManagerTests PRIVATE NexusRuntime Qt6::Test Qt6::Core Qt6::Network)
    add_test(NAME RuntimeManagerTests COMMAND RuntimeManagerTests)
    set_tests_properties(RuntimeManagerTests PROPERTIES
        ENVIRONMENT "NEXUS_RUNTIME_TEST_FIXTURE=$<TARGET_FILE:RuntimeTestFixture>"
    )

    if(WIN32)
        add_executable(InstanceGuardTests tests/InstanceGuardTests.cpp)
        target_link_libraries(InstanceGuardTests PRIVATE NexusInstanceGuard Qt6::Test)
        add_test(NAME InstanceGuardTests COMMAND InstanceGuardTests)
    endif()

    if(WIN32 AND QT_WINDEPLOYQT_EXECUTABLE)
        foreach(_runtime_test_target RuntimeTestFixture RuntimeManagerTests InstanceGuardTests)
            add_custom_command(TARGET ${_runtime_test_target} POST_BUILD
                COMMAND "${QT_WINDEPLOYQT_EXECUTABLE}"
                    --no-compiler-runtime --no-translations --no-plugins
                    "$<TARGET_FILE:${_runtime_test_target}>"
                COMMENT "Deploying Qt runtime libraries for ${_runtime_test_target}"
                VERBATIM
            )
        endforeach()
    endif()
endif()
